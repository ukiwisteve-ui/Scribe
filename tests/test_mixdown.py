import wave

import numpy as np
import soundfile as sf

from scribe import mixdown


def write_wav(path, rate, channels, seconds, freq):
    t = np.arange(int(rate * seconds)) / rate
    tone = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    data = np.repeat(tone[:, None], channels, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def test_resamples_and_downmixes(tmp_path):
    src = tmp_path / "a.wav"
    write_wav(src, 44100, 2, 2.0, 440)
    out = mixdown.load_mono_resampled(src)
    assert abs(len(out) - 32000) <= 1
    assert out.dtype == np.float32


def test_stereo_mixdown_aligns_and_pads(tmp_path):
    system = tmp_path / "raw_system.wav"
    mic = tmp_path / "raw_mic.wav"
    write_wav(system, 48000, 2, 3.0, 300)
    write_wav(mic, 44100, 1, 2.0, 500)
    out, channels, duration = mixdown.mixdown(system, tmp_path / "rec.flac", mic, mic_offset_seconds=0.5)
    data, rate = sf.read(str(out))
    assert rate == 16000 and channels == 2 and data.shape[1] == 2
    assert abs(duration - 3.0) < 0.01
    # Mic started half a second later, so its first 0.5 s is silence.
    assert np.max(np.abs(data[: 7000, 0])) < 1e-3
    assert np.max(np.abs(data[9000:20000, 0])) > 0.1


def test_system_only_is_mono(tmp_path):
    system = tmp_path / "raw_system.wav"
    write_wav(system, 48000, 2, 1.0, 300)
    _, channels, _ = mixdown.mixdown(system, tmp_path / "rec.flac")
    assert channels == 1
