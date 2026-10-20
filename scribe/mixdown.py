"""Turn the raw per-device recordings into one time-aligned file for transcription.

The output is 16 kHz FLAC. With a microphone it is stereo: channel 0 is you
(the mic), channel 1 is everyone else (system audio). Keeping the two apart lets
the transcriber attribute speech far more reliably than diarization alone.
"""

from __future__ import annotations

from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

TARGET_RATE = 16000
BLOCK_SECONDS = 60


def load_mono_resampled(path: Path, target_rate: int = TARGET_RATE) -> np.ndarray:
    """Read a WAV file in blocks, downmix to mono and resample. Returns float32."""
    info = sf.info(str(path))
    g = gcd(info.samplerate, target_rate)
    up, down = target_rate // g, info.samplerate // g
    parts = []
    for block in sf.blocks(str(path), blocksize=info.samplerate * BLOCK_SECONDS,
                           dtype="float32", always_2d=True):
        mono = block.mean(axis=1)
        if up != down:
            mono = resample_poly(mono, up, down).astype(np.float32)
        parts.append(mono)
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts)


def _normalize(track: np.ndarray, peak: float = 0.9) -> np.ndarray:
    """Bring quiet tracks up so speech is clearly audible to the transcriber."""
    max_abs = float(np.max(np.abs(track))) if track.size else 0.0
    if max_abs < 1e-4:
        return track
    gain = min(peak / max_abs, 10.0)  # Cap the boost so a silent track stays quiet.
    return track * gain if gain > 1.0 else track


def mixdown(
    system_path: Path,
    output_path: Path,
    mic_path: Path | None = None,
    mic_offset_seconds: float = 0.0,
) -> tuple[Path, int, float]:
    """Write the aligned recording to `output_path`.

    `mic_offset_seconds` is how much later the mic started than the system
    track (negative if it started earlier).

    Returns (path, channel count, duration in seconds).
    """
    system = _normalize(load_mono_resampled(system_path))
    if mic_path is None:
        data = system.reshape(-1, 1)
    else:
        mic = _normalize(load_mono_resampled(mic_path))
        shift = int(round(abs(mic_offset_seconds) * TARGET_RATE))
        pad = np.zeros(shift, dtype=np.float32)
        if mic_offset_seconds > 0:
            mic = np.concatenate([pad, mic])
        elif mic_offset_seconds < 0:
            system = np.concatenate([pad, system])
        length = max(len(mic), len(system))
        mic = np.pad(mic, (0, length - len(mic)))
        system = np.pad(system, (0, length - len(system)))
        data = np.stack([mic, system], axis=1)

    np.clip(data, -1.0, 1.0, out=data)
    sf.write(str(output_path), data, TARGET_RATE, format="FLAC", subtype="PCM_16")
    return output_path, data.shape[1], data.shape[0] / TARGET_RATE
