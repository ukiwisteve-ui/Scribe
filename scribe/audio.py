"""Windows audio capture: system output (WASAPI loopback) plus the microphone.

Each source is written to its own WAV file at the device's native format while
recording. `mixdown.py` turns those into one aligned file afterwards.
"""

from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

try:
    import pyaudiowpatch as pyaudio
except ImportError:  # Not on Windows (tests, CI). Recording is unavailable.
    pyaudio = None


@dataclass
class Device:
    index: int
    name: str
    channels: int
    rate: int

    def __str__(self) -> str:
        return self.name


@dataclass
class TrackInfo:
    path: Path
    rate: int
    channels: int
    first_frame_time: float | None = None
    frames_written: int = 0


def _require_pyaudio():
    if pyaudio is None:
        raise RuntimeError(
            "PyAudioWPatch is not installed. Recording only works on Windows; "
            "run setup.ps1 to install the dependencies."
        )


def list_devices() -> tuple[list[Device], list[Device], Device | None, Device | None]:
    """Return (loopback outputs, microphones, default loopback, default mic)."""
    _require_pyaudio()
    p = pyaudio.PyAudio()
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        outputs: list[Device] = []
        mics: list[Device] = []
        for loop in p.get_loopback_device_info_generator():
            outputs.append(
                Device(loop["index"], loop["name"].replace(" [Loopback]", ""),
                       int(loop["maxInputChannels"]), int(loop["defaultSampleRate"]))
            )
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["hostApi"] != wasapi["index"] or info.get("isLoopbackDevice"):
                continue
            if info["maxInputChannels"] > 0:
                mics.append(Device(i, info["name"], int(info["maxInputChannels"]),
                                   int(info["defaultSampleRate"])))

        default_out = None
        if wasapi["defaultOutputDevice"] >= 0:
            speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            default_out = next((d for d in outputs if d.name == speakers["name"]), None)
            if default_out is None:
                default_out = next((d for d in outputs if speakers["name"] in d.name), None)
        default_mic = None
        if wasapi["defaultInputDevice"] >= 0:
            default_mic = next((d for d in mics if d.index == wasapi["defaultInputDevice"]), None)
        return outputs, mics, default_out or (outputs[0] if outputs else None), default_mic
    finally:
        p.terminate()


class _TrackWriter:
    """Receives audio chunks from a PortAudio callback and writes them on a worker thread."""

    # If loopback data stops arriving for longer than this, the gap is filled with
    # silence so the system track stays in step with the wall clock.
    GAP_FILL_SECONDS = 0.5

    def __init__(self, info: TrackInfo, fill_gaps: bool):
        self.info = info
        self.fill_gaps = fill_gaps
        self.level = 0.0
        self._queue: queue.Queue[tuple[float, bytes] | None] = queue.Queue()
        self._wav = wave.open(str(info.path), "wb")
        self._wav.setnchannels(info.channels)
        self._wav.setsampwidth(2)
        self._wav.setframerate(info.rate)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def callback(self, in_data, frame_count, time_info, status):
        self._queue.put((time.monotonic(), in_data))
        return (None, pyaudio.paContinue)

    def _run(self):
        bytes_per_frame = 2 * self.info.channels
        while True:
            item = self._queue.get()
            if item is None:
                break
            arrived, data = item
            frames = len(data) // bytes_per_frame
            if self.info.first_frame_time is None:
                # The callback fires once a buffer is full, so the first frame was
                # captured roughly one buffer earlier.
                self.info.first_frame_time = arrived - frames / self.info.rate
            elif self.fill_gaps:
                expected = (arrived - self.info.first_frame_time) * self.info.rate
                missing = int(expected - self.info.frames_written - frames)
                if missing > self.GAP_FILL_SECONDS * self.info.rate:
                    self._wav.writeframes(b"\x00" * missing * bytes_per_frame)
                    self.info.frames_written += missing
            self._wav.writeframes(data)
            self.info.frames_written += frames
            samples = np.frombuffer(data, dtype=np.int16)
            if samples.size:
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0
                self.level = min(1.0, rms * 4)

    def close(self):
        self._queue.put(None)
        self._thread.join()
        self._wav.close()


@dataclass
class Recorder:
    """Records system audio and (optionally) the microphone into `session_dir`."""

    session_dir: Path
    output_device: Device
    mic_device: Device | None = None
    tracks: dict[str, TrackInfo] = field(default_factory=dict)

    def __post_init__(self):
        _require_pyaudio()
        self._p = None
        self._streams = []
        self._writers: dict[str, _TrackWriter] = {}
        self.started_at: float | None = None

    def start(self):
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._p = pyaudio.PyAudio()
        try:
            self._start_silence_keeper()
            self._open("system", self.output_device, fill_gaps=True)
            if self.mic_device is not None:
                self._open("mic", self.mic_device, fill_gaps=False)
        except Exception:
            self.stop()
            raise
        self.started_at = time.monotonic()

    def _open(self, name: str, device: Device, fill_gaps: bool):
        channels = max(1, min(device.channels, 2))
        info = TrackInfo(self.session_dir / f"raw_{name}.wav", device.rate, channels)
        writer = _TrackWriter(info, fill_gaps)
        stream = self._p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=device.rate,
            input=True,
            input_device_index=device.index,
            frames_per_buffer=int(device.rate * 0.05),
            stream_callback=writer.callback,
        )
        self.tracks[name] = info
        self._writers[name] = writer
        self._streams.append(stream)

    def _start_silence_keeper(self):
        """Play silence to the output device.

        WASAPI loopback delivers nothing while no app is playing sound, which
        would leave holes in the system track. Playing silence keeps it flowing.
        """
        speaker_index = None
        for i in range(self._p.get_device_count()):
            info = self._p.get_device_info_by_index(i)
            if (not info.get("isLoopbackDevice") and info["maxOutputChannels"] > 0
                    and info["name"] == self.output_device.name):
                speaker_index = i
                break
        if speaker_index is None:
            return
        info = self._p.get_device_info_by_index(speaker_index)
        channels = max(1, min(int(info["maxOutputChannels"]), 2))

        def callback(in_data, frame_count, time_info, status):
            return (b"\x00" * frame_count * channels * 2, pyaudio.paContinue)

        try:
            stream = self._p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=int(info["defaultSampleRate"]),
                output=True,
                output_device_index=speaker_index,
                stream_callback=callback,
            )
            self._streams.append(stream)
        except Exception:
            pass  # Gap filling in _TrackWriter covers us without it.

    def levels(self) -> dict[str, float]:
        return {name: w.level for name, w in self._writers.items()}

    def elapsed(self) -> float:
        return 0.0 if self.started_at is None else time.monotonic() - self.started_at

    def stop(self) -> dict[str, TrackInfo]:
        for stream in self._streams:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        self._streams = []
        for writer in self._writers.values():
            writer.close()
        self._writers = {}
        if self._p is not None:
            self._p.terminate()
            self._p = None
        return self.tracks
