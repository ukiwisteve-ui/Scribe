"""Session folders and the post-call pipeline: mixdown, transcribe, summarise."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import mixdown, summarize, transcribe
from .config import Settings

SESSION_FILE = "session.json"
AUDIO_FILE = "recording.flac"
RESPONSE_FILE = "deepgram_response.json"


@dataclass
class SessionMeta:
    started: str
    context: str = ""
    tracks: dict = field(default_factory=dict)  # name -> {path, rate, channels, first_frame_time}
    duration_seconds: float = 0.0
    channels: int = 0
    participants: str = ""  # names typed before or during the call
    speaker_names: dict = field(default_factory=dict)  # "Speaker 1" -> "Sarah", set after the call

    def save(self, folder: Path):
        (folder / SESSION_FILE).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> "SessionMeta":
        return cls(**json.loads((folder / SESSION_FILE).read_text(encoding="utf-8")))


def tracks_to_meta(tracks: dict) -> dict:
    """Describe recorded tracks (audio.TrackInfo) for session.json."""
    return {
        name: {"path": t.path.name, "rate": t.rate, "channels": t.channels,
               "first_frame_time": t.first_frame_time}
        for name, t in tracks.items()
    }


def speaker_samples(folder: Path, limit: int = 3) -> dict[str, list[str]]:
    """A few lines from each speaker, to help the person put names to them."""
    samples: dict[str, list[str]] = {}
    for seg in transcribe.load_segments(folder / "transcript.json"):
        lines = samples.setdefault(seg.speaker, [])
        if len(lines) < limit:
            lines.append(seg.text if len(seg.text) <= 140 else seg.text[:137] + "...")
    return samples


def new_session_dir(settings: Settings, context: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", context.strip().splitlines()[0] if context.strip() else "")
    slug = slug.strip("-")[:40]
    name = f"{stamp}_{slug}" if slug else stamp
    folder = settings.output_dir / name
    n = 2
    while folder.exists():
        folder = settings.output_dir / f"{name}_{n}"
        n += 1
    folder.mkdir(parents=True)
    return folder


def _duration_text(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def process_session(folder: Path, settings: Settings,
                    log: Callable[[str], None] = print) -> Path:
    """Run every step that has not already produced its output. Returns summary path."""
    missing = settings.missing_keys()
    if missing:
        raise RuntimeError(f"Add {', '.join(missing)} to the .env file, then reprocess the session.")

    meta = SessionMeta.load(folder)
    audio_path = folder / AUDIO_FILE

    if not audio_path.exists():
        log("Preparing audio...")
        system = meta.tracks.get("system")
        mic = meta.tracks.get("mic")
        if not system:
            raise RuntimeError("This session has no system audio track.")
        offset = 0.0
        if mic and mic.get("first_frame_time") and system.get("first_frame_time"):
            offset = mic["first_frame_time"] - system["first_frame_time"]
        _, meta.channels, meta.duration_seconds = mixdown.mixdown(
            folder / system["path"], audio_path,
            mic_path=folder / mic["path"] if mic else None,
            mic_offset_seconds=offset,
        )
        meta.save(folder)
        if not settings.keep_raw_audio:
            for track in meta.tracks.values():
                (folder / track["path"]).unlink(missing_ok=True)

    response_path = folder / RESPONSE_FILE
    if not response_path.exists():
        log("Transcribing...")
        data = transcribe.request_transcript(audio_path, settings.deepgram_api_key,
                                             settings.language, meta.channels)
        response_path.write_text(json.dumps(data), encoding="utf-8")
    # Parsing is cheap, so it is redone every time; reprocessing an old call
    # picks up improvements to speaker handling without paying to transcribe again.
    data = json.loads(response_path.read_text(encoding="utf-8"))
    segments = transcribe.parse_response(data, meta.channels, settings.user_name)
    recording_note = ""
    if meta.channels > 1 and not any(w.channel == 1 for w in transcribe.extract_words(data)):
        recording_note = (
            "No speech was heard on the call-audio channel, so every line came through "
            f"{settings.user_name}'s microphone. Other people's words are probably labelled "
            f"as {settings.user_name}."
        )
        log("Warning: no speech was heard from the call audio, only from your microphone. "
            "Check that 'Call audio from' is the device you hear the call through. "
            "Calls on your phone can't be captured.")
    transcribe.save_segments(segments, folder / "transcript.json")
    segments = transcribe.rename_speakers(segments, meta.speaker_names)
    title = f"Transcript, {meta.started}"
    (folder / "transcript.md").write_text(transcribe.to_markdown(segments, title),
                                          encoding="utf-8")
    if not segments:
        raise RuntimeError("No speech was detected in the recording.")

    summary_path = folder / "summary.md"
    log("Writing summary and action items with Claude...")
    summary = summarize.summarize(
        transcribe.to_plain_text(segments),
        api_key=settings.anthropic_api_key,
        model=settings.claude_model,
        user_name=settings.user_name,
        context=meta.context,
        participants=meta.participants,
        recording_note=recording_note,
        recorded_at=meta.started,
        duration=_duration_text(meta.duration_seconds),
    )
    summary_path.write_text(summary + "\n", encoding="utf-8")
    log(f"Done. Notes saved to {summary_path}")
    return summary_path
