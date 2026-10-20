"""Transcription with Deepgram Nova-3 (speaker diarization + per-channel attribution)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

import httpx2

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"

# Consecutive lines from the same speaker closer together than this are merged.
MERGE_GAP_SECONDS = 1.5


@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str
    channel: int = 0


def request_transcript(audio_path: Path, api_key: str, language: str, channels: int) -> dict:
    params = {
        "model": "nova-3",
        "smart_format": "true",
        "punctuate": "true",
        "paragraphs": "true",
        "utterances": "true",
        "diarize": "true",
    }
    if language.lower() in {"auto", "detect"}:
        params["detect_language"] = "true"
    else:
        params["language"] = language
    if channels > 1:
        params["multichannel"] = "true"

    headers = {"Authorization": f"Token {api_key}", "Content-Type": "audio/flac"}
    timeout = httpx2.Timeout(connect=30.0, read=1800.0, write=1800.0, pool=30.0)
    with open(audio_path, "rb") as f:
        response = httpx2.post(DEEPGRAM_URL, params=params, headers=headers, content=f,
                              timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"Deepgram error {response.status_code}: {response.text[:500]}")
    return response.json()


def _speaker_label(channel: int, speaker: int, channels: int, user_name: str,
                   mic_speakers: set[int]) -> str:
    if channels > 1 and channel == 0:
        # The mic is you. If the room had more voices, number the extras.
        if len(mic_speakers) <= 1 or speaker == min(mic_speakers):
            return user_name
        return f"{user_name} (room) {speaker + 1}"
    return f"Speaker {speaker + 1}"


def parse_response(data: dict, channels: int, user_name: str) -> list[Segment]:
    utterances = data.get("results", {}).get("utterances") or []
    mic_speakers = {int(u.get("speaker", 0)) for u in utterances if int(u.get("channel", 0)) == 0}
    segments = []
    for u in utterances:
        text = (u.get("transcript") or "").strip()
        if not text:
            continue
        channel = int(u.get("channel", 0))
        speaker = int(u.get("speaker", 0))
        segments.append(Segment(
            start=float(u["start"]),
            end=float(u["end"]),
            speaker=_speaker_label(channel, speaker, channels, user_name, mic_speakers),
            text=text,
            channel=channel,
        ))
    segments.sort(key=lambda s: (s.start, s.channel))
    if channels > 1:
        segments = drop_echo(segments)
    return merge_segments(segments)


def drop_echo(segments: list[Segment], similarity: float = 0.75) -> list[Segment]:
    """Remove mic lines that are just the speakers being picked up by the mic.

    Without headphones the mic hears the other people too, which would show up
    as you repeating what they said. A mic line that overlaps in time with a
    near-identical system-audio line is treated as echo.
    """
    system = [s for s in segments if s.channel == 1]
    kept = []
    for seg in segments:
        if seg.channel == 0:
            for other in system:
                if other.end < seg.start - 1.0 or other.start > seg.end + 1.0:
                    continue
                ratio = SequenceMatcher(None, seg.text.lower(), other.text.lower()).ratio()
                if ratio >= similarity:
                    break
            else:
                kept.append(seg)
        else:
            kept.append(seg)
    return kept


def merge_segments(segments: list[Segment]) -> list[Segment]:
    merged: list[Segment] = []
    for seg in segments:
        prev = merged[-1] if merged else None
        if prev and prev.speaker == seg.speaker and seg.start - prev.end <= MERGE_GAP_SECONDS:
            prev.text = f"{prev.text} {seg.text}"
            prev.end = max(prev.end, seg.end)
        else:
            merged.append(Segment(**asdict(seg)))
    return merged


def format_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def to_markdown(segments: list[Segment], title: str) -> str:
    lines = [f"# {title}", ""]
    for seg in segments:
        lines.append(f"**[{format_timestamp(seg.start)}] {seg.speaker}:** {seg.text}")
        lines.append("")
    return "\n".join(lines)


def to_plain_text(segments: list[Segment]) -> str:
    return "\n".join(f"[{format_timestamp(s.start)}] {s.speaker}: {s.text}" for s in segments)


def save_segments(segments: list[Segment], path: Path):
    path.write_text(json.dumps([asdict(s) for s in segments], indent=2), encoding="utf-8")


def load_segments(path: Path) -> list[Segment]:
    return [Segment(**s) for s in json.loads(path.read_text(encoding="utf-8"))]
