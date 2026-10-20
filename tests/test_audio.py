import sys
import time
import wave

import numpy as np
import pytest

from scribe import audio, config


def interleave(*channels):
    return np.stack(channels, axis=1).astype(np.int16).reshape(-1)


def test_stereo_and_mono_pass_through():
    samples = interleave([1, 2, 3], [4, 5, 6])
    assert audio.downmix_to_stereo(samples, 2) is samples
    assert audio.downmix_to_stereo(samples, 1) is samples


def test_surround_folds_to_stereo_keeping_centre_and_dropping_lfe():
    n = 4
    zero = [0] * n
    # 7.1 order: FL FR C LFE BL BR SL SR. Only the centre and LFE carry sound.
    samples = interleave(zero, zero, [10000] * n, [30000] * n, zero, zero, zero, zero)
    stereo = audio.downmix_to_stereo(samples, 8).reshape(-1, 2)
    assert stereo.shape == (n, 2)
    assert np.all(stereo[:, 0] == stereo[:, 1])
    weight = 1 + 0.707 * 3  # front, centre and two surround pairs
    assert np.allclose(stereo[:, 0], 0.707 * 10000 / weight, atol=1)


def test_surround_keeps_left_and_right_apart():
    n = 4
    zero = [0] * n
    samples = interleave([8000] * n, zero, zero, zero, zero, [8000] * n)  # 5.1: FL and BR
    stereo = audio.downmix_to_stereo(samples, 6).reshape(-1, 2)
    weight = 1 + 0.707 * 2
    assert np.allclose(stereo[:, 0], 8000 / weight, atol=1)
    assert np.allclose(stereo[:, 1], 0.707 * 8000 / weight, atol=1)


def test_track_writer_downmixes_surround_capture(tmp_path):
    info = audio.TrackInfo(tmp_path / "raw_system.wav", 48000, 2)
    writer = audio._TrackWriter(info, fill_gaps=False, in_channels=8)
    chunk = np.full(480 * 8, 1000, dtype=np.int16).tobytes()
    for _ in range(3):
        writer._queue.put((time.monotonic(), chunk))
    writer.close()
    assert info.frames_written == 1440
    with wave.open(str(info.path)) as w:
        assert w.getnchannels() == 2
        assert w.getnframes() == 1440


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known folders")
def test_documents_dir_follows_shell_folder():
    import subprocess

    expected = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('MyDocuments')"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert config.documents_dir() == config.Path(expected)
