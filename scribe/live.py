"""Live transcript while recording, streamed to Deepgram.

Each track (your mic, the call audio) gets its own websocket. The recorder only
drops audio chunks into a bounded queue here; everything else happens on this
module's threads, so a slow or failed live stream never touches the recording
or the transcript made after the call.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from collections import deque
from math import gcd
from typing import Callable
from urllib.parse import urlencode

import numpy as np
from scipy.signal import resample_poly

try:
    from websockets.sync.client import connect
except ImportError:  # Live transcript is unavailable without it.
    connect = None

LIVE_URL = "wss://api.deepgram.com/v1/listen"
LIVE_RATE = 16000

# A mic line counts as the call leaking into the mic when most of its words
# were just heard on the call audio.
ECHO_WINDOW_SECONDS = 6.0
ECHO_OVERLAP = 0.6

RETRY_DELAYS = (2, 5, 15)


def live_url(language: str, keyterms: list[str] | None = None) -> str:
    params: list[tuple[str, str]] = [
        ("model", "nova-3"), ("encoding", "linear16"), ("sample_rate", str(LIVE_RATE)),
        ("channels", "1"), ("punctuate", "true"), ("smart_format", "true"),
        ("interim_results", "false"), ("endpointing", "300"),
        # Streaming has no language detection; "multi" copes with any language.
        ("language", "multi" if language.lower() in {"auto", "detect"} else language),
    ]
    params += [("keyterm", term) for term in keyterms or []]
    return f"{LIVE_URL}?{urlencode(params)}"


def to_live_audio(data: bytes, rate: int, channels: int) -> bytes:
    """Interleaved int16 at the device rate to 16 kHz mono int16."""
    samples = np.frombuffer(data, dtype=np.int16)
    if channels > 1:
        samples = samples[: len(samples) // channels * channels]
        mono = samples.reshape(-1, channels).astype(np.float32).mean(axis=1)
    else:
        mono = samples.astype(np.float32)
    if rate != LIVE_RATE and mono.size:
        g = gcd(rate, LIVE_RATE)
        mono = resample_poly(mono, LIVE_RATE // g, rate // g)
    return np.clip(mono, -32768, 32767).astype(np.int16).tobytes()


def final_text(message: str | bytes) -> str:
    """The transcript from a Deepgram live message, if it is a final result."""
    try:
        data = json.loads(message)
    except (TypeError, ValueError):
        return ""
    if data.get("type") != "Results" or not data.get("is_final"):
        return ""
    alternatives = (data.get("channel") or {}).get("alternatives") or [{}]
    return (alternatives[0].get("transcript") or "").strip()


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w]


class EchoFilter:
    """Drops mic lines that repeat what the call audio just said (no headphones)."""

    def __init__(self, window: float = ECHO_WINDOW_SECONDS):
        self.window = window
        self.recent: deque[tuple[float, set[str]]] = deque()

    def heard_on_call(self, text: str, now: float | None = None):
        now = time.monotonic() if now is None else now
        self.recent.append((now, set(_words(text))))
        self._expire(now)

    def is_echo(self, text: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._expire(now)
        words = _words(text)
        if not words or not self.recent:
            return False
        heard = set().union(*(w for _, w in self.recent))
        overlap = sum(w in heard for w in words) / len(words)
        return overlap >= ECHO_OVERLAP

    def _expire(self, now: float):
        while self.recent and now - self.recent[0][0] > self.window:
            self.recent.popleft()


class TrackStream(threading.Thread):
    """Streams one track to Deepgram and reports each final line."""

    QUEUE_CHUNKS = 400  # About 20 seconds of 50 ms chunks.

    def __init__(self, label: str, url: str, api_key: str, rate: int, channels: int,
                 on_line: Callable[[str, str], None], on_status: Callable[[str], None]):
        super().__init__(daemon=True, name=f"live-{label}")
        self.label = label
        self.url = url
        self.api_key = api_key
        self.rate = rate
        self.channels = channels
        self.on_line = on_line
        self.on_status = on_status
        self.chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=self.QUEUE_CHUNKS)
        self.stopping = threading.Event()

    def feed(self, data: bytes):
        try:
            self.chunks.put_nowait(data)
        except queue.Full:
            pass  # Falling behind: drop audio rather than hold up the recorder.

    def stop(self):
        self.stopping.set()
        try:
            self.chunks.put_nowait(None)
        except queue.Full:
            pass

    def run(self):
        attempt = 0
        while not self.stopping.is_set():
            try:
                self._stream()
                return
            except Exception as exc:  # noqa: BLE001
                if self.stopping.is_set():
                    return
                if attempt >= len(RETRY_DELAYS):
                    self.on_status(f"Live transcript for {self.label} stopped ({exc}). "
                                   "The recording carries on as normal.")
                    return
                if attempt == 0:
                    self.on_status(f"Live transcript for {self.label} lost its connection "
                                   f"({exc}). Retrying...")
                self.stopping.wait(RETRY_DELAYS[attempt])
                attempt += 1
                self._drain()

    def _drain(self):
        """Throw away audio that piled up while disconnected."""
        while True:
            try:
                if self.chunks.get_nowait() is None:
                    self.stopping.set()
            except queue.Empty:
                return

    def _stream(self):
        if connect is None:
            raise RuntimeError("the websockets package is not installed")
        with connect(self.url, additional_headers={"Authorization": f"Token {self.api_key}"},
                     open_timeout=10, close_timeout=5, max_size=2**22) as ws:
            receiver = threading.Thread(target=self._receive, args=(ws,), daemon=True)
            receiver.start()
            last_sent = time.monotonic()
            while True:
                try:
                    chunk = self.chunks.get(timeout=1.0)
                except queue.Empty:
                    chunk = b""
                if chunk is None:
                    ws.send(json.dumps({"type": "CloseStream"}))
                    receiver.join(timeout=5)
                    return
                if chunk:
                    ws.send(to_live_audio(chunk, self.rate, self.channels))
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent > 5:
                    ws.send(json.dumps({"type": "KeepAlive"}))
                    last_sent = time.monotonic()
                if not receiver.is_alive():
                    raise RuntimeError("Deepgram closed the connection")

    def _receive(self, ws):
        try:
            for message in ws:
                text = final_text(message)
                if text:
                    self.on_line(self.label, text)
        except Exception:  # noqa: BLE001
            pass  # The sender notices and reconnects.


class LiveTranscriber:
    """Live transcript for a recording. Every method is safe to call from any thread."""

    def __init__(self, api_key: str, language: str, keyterms: list[str],
                 on_line: Callable[[str, str], None], on_status: Callable[[str], None],
                 echo_label: str = "Call"):
        self.url = live_url(language, keyterms)
        self.api_key = api_key
        self.on_line = on_line
        self.on_status = on_status
        self.echo_label = echo_label
        self.echo = EchoFilter()
        self.streams: dict[str, TrackStream] = {}
        self.lock = threading.Lock()

    def add_track(self, name: str, label: str, rate: int, channels: int):
        stream = TrackStream(label, self.url, self.api_key, rate, channels,
                             self._line, self.on_status)
        with self.lock:
            self.streams[name] = stream
        stream.start()

    def feed(self, name: str, data: bytes):
        stream = self.streams.get(name)
        if stream is not None:
            stream.feed(data)

    def stop(self):
        with self.lock:
            streams, self.streams = list(self.streams.values()), {}
        for stream in streams:
            stream.stop()

    def _line(self, label: str, text: str):
        with self.lock:
            if label == self.echo_label:
                self.echo.heard_on_call(text)
            elif self.echo.is_echo(text):
                return
        self.on_line(label, text)
