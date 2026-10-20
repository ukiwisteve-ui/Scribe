"""Transcription with Deepgram Nova-3 (speaker diarization + per-channel attribution)."""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx2

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"

# Consecutive lines from the same speaker closer together than this are merged.
MERGE_GAP_SECONDS = 1.5

# How far apart in time the same word can appear on both channels and still
# count as the mic picking up the call audio.
ECHO_WINDOW = 0.6


@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str
    channel: int = 0


# Deepgram caps keyterm prompting at about 500 tokens, so keep the list short.
MAX_KEYTERMS = 50


def participant_names(participants: str) -> list[str]:
    """Pull names out of the "Who's on the call?" text, e.g. "Sarah, Tom from Acme"."""
    names = []
    for part in re.split(r"[,;\n/&]|\band\b|\bfrom\b|\bat\b|\bwith\b|[()]", participants):
        part = part.strip(" .:-")
        if part and len(part.split()) <= 4:
            names.append(part)
    return names


def keyterms(vocabulary: list[str], participants: str = "", user_name: str = "") -> list[str]:
    """Words Deepgram should listen out for: names on the call first, then jargon."""
    seen, terms = set(), []
    for term in [*participant_names(participants), user_name, *vocabulary]:
        term = term.strip()
        if term and term.lower() not in seen and term.lower() != "me":
            seen.add(term.lower())
            terms.append(term)
    return terms[:MAX_KEYTERMS]


def request_transcript(audio_path: Path, api_key: str, language: str, channels: int,
                       keyterms: list[str] | None = None) -> dict:
    params: dict[str, str | list[str]] = {
        "model": "nova-3",
        "smart_format": "true",
        "punctuate": "true",
        "diarize": "true",
    }
    if language.lower() in {"auto", "detect"}:
        params["detect_language"] = "true"
    else:
        params["language"] = language
    if channels > 1:
        params["multichannel"] = "true"
    if keyterms:
        params["keyterm"] = list(keyterms)  # Sent as one keyterm=... per term.

    headers = {"Authorization": f"Token {api_key}", "Content-Type": "audio/flac"}
    timeout = httpx2.Timeout(connect=30.0, read=1800.0, write=1800.0, pool=30.0)
    with open(audio_path, "rb") as f:
        response = httpx2.post(DEEPGRAM_URL, params=params, headers=headers, content=f,
                              timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"Deepgram error {response.status_code}: {response.text[:500]}")
    return response.json()


@dataclass
class Word:
    start: float
    end: float
    text: str
    channel: int
    speaker: int

    @property
    def norm(self) -> str:
        return re.sub(r"[^a-z0-9']", "", self.text.lower())


def extract_words(data: dict) -> list[Word]:
    words = []
    for channel, result in enumerate(data.get("results", {}).get("channels") or []):
        alternatives = result.get("alternatives") or [{}]
        for w in alternatives[0].get("words") or []:
            words.append(Word(
                start=float(w["start"]),
                end=float(w["end"]),
                text=w.get("punctuated_word") or w.get("word", ""),
                channel=channel,
                speaker=int(w.get("speaker", 0)),
            ))
    return words


def remove_echo(words: list[Word]) -> list[Word]:
    """Drop mic words that are the other people leaking into your microphone.

    Without headphones the mic hears the call too, so the same words appear on
    both channels at about the same moment. Each mic word is matched against
    system words within ECHO_WINDOW seconds. A run of two or more matching
    words is echo, as is a lone matching word that makes up a whole phrase on
    its own ("Thanks." "Yeah."). One matching word in the middle of your own
    sentence is kept, because people do say the same word at the same time.
    """
    system = sorted((w for w in words if w.channel == 1), key=lambda w: w.start)
    starts = [w.start for w in system]
    mic = sorted((w for w in words if w.channel == 0), key=lambda w: w.start)

    def matches(word: Word) -> bool:
        if not word.norm:
            return False
        i = bisect_left(starts, word.start - ECHO_WINDOW)
        while i < len(system) and system[i].start <= word.start + ECHO_WINDOW:
            if system[i].norm == word.norm:
                return True
            i += 1
        return False

    echo = set()
    # Phrases: runs of mic words without a long pause.
    phrases: list[list[int]] = []
    for i, word in enumerate(mic):
        if phrases and word.start - mic[phrases[-1][-1]].end < 1.0:
            phrases[-1].append(i)
        else:
            phrases.append([i])
    for phrase in phrases:
        flags = [matches(mic[i]) for i in phrase]
        run: list[int] = []
        for i, flag in zip(phrase + [None], flags + [False]):
            if flag:
                run.append(i)
                continue
            if len(run) >= 2 or (run and len(run) == len(phrase)):
                echo.update(run)
            run = []
    kept_mic = [w for i, w in enumerate(mic) if i not in echo]
    return sorted(system + kept_mic, key=lambda w: w.start)


def speaker_labels(words: list[Word], channels: int, user_name: str) -> dict[tuple[int, int], str]:
    """Name each (channel, speaker) pair.

    On the mic channel the speaker with the most words is you; anyone else the
    mic picked up (people in the room, a phone on speaker) gets a number after
    the people heard through the PC.
    """
    labels: dict[tuple[int, int], str] = {}
    order: list[tuple[int, int]] = []
    counts: dict[tuple[int, int], int] = {}
    for w in sorted(words, key=lambda w: w.start):
        key = (w.channel, w.speaker)
        counts[key] = counts.get(key, 0) + 1
        if key not in order:
            order.append(key)
    if channels > 1:
        mic_keys = [k for k in order if k[0] == 0]
        if mic_keys:
            labels[max(mic_keys, key=lambda k: counts[k])] = user_name
    n = 1
    for key in sorted(order, key=lambda k: (k[0] == 0, order.index(k))):
        if key not in labels:
            labels[key] = f"Speaker {n}"
            n += 1
    return labels


def parse_response(data: dict, channels: int, user_name: str) -> list[Segment]:
    words = extract_words(data)
    if channels > 1:
        words = remove_echo(words)
    labels = speaker_labels(words, channels, user_name)

    # Group each speaker's words into turns, then order the turns by time. A
    # turn continues across someone talking over it ("yeah", "mm") but ends when
    # another person speaks in the pause after it.
    ordered = sorted(words, key=lambda w: w.start)
    turns: list[Segment] = []
    current: dict[str, tuple[Segment, int]] = {}
    for i, w in enumerate(ordered):
        label = labels[(w.channel, w.speaker)]
        seg, last = current.get(label, (None, -1))
        interrupted = seg is not None and any(
            labels[(o.channel, o.speaker)] != label and o.start >= seg.end - 0.05
            for o in ordered[last + 1:i]
        )
        if seg and not interrupted and w.start - seg.end <= MERGE_GAP_SECONDS:
            seg.text = f"{seg.text} {w.text}"
            seg.end = w.end
        else:
            seg = Segment(start=w.start, end=w.end, speaker=label, text=w.text, channel=w.channel)
            turns.append(seg)
        current[label] = (seg, i)
    turns.sort(key=lambda s: s.start)
    return merge_segments(turns)


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


def rename_speakers(segments: list[Segment], names: dict[str, str]) -> list[Segment]:
    """Apply names chosen after the call, e.g. {"Speaker 1": "Sarah"}."""
    renamed = []
    for seg in segments:
        name = (names.get(seg.speaker) or "").strip() or seg.speaker
        renamed.append(Segment(**{**asdict(seg), "speaker": name}))
    return merge_segments(renamed)


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
