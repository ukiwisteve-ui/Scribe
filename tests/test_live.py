import json
import threading
import time
from urllib.parse import parse_qs, urlparse

import numpy as np
from websockets.sync.server import serve

from scribe import audio, live


def results(text, is_final=True):
    return json.dumps({"type": "Results", "is_final": is_final,
                       "channel": {"alternatives": [{"transcript": text}]}})


def test_live_url_params():
    url = live.live_url("en-GB", ["Siobhan", "OKRs"])
    query = parse_qs(urlparse(url).query)
    assert url.startswith("wss://api.deepgram.com/v1/listen?")
    assert query["model"] == ["nova-3"]
    assert query["encoding"] == ["linear16"] and query["sample_rate"] == ["16000"]
    assert query["channels"] == ["1"] and query["language"] == ["en-GB"]
    assert query["keyterm"] == ["Siobhan", "OKRs"]
    assert parse_qs(urlparse(live.live_url("auto")).query)["language"] == ["multi"]


def test_audio_is_downmixed_and_resampled_to_16k_mono():
    stereo = np.zeros((4800, 2), dtype=np.int16)  # 0.1 s at 48 kHz
    stereo[:, 0] = 1000
    stereo[:, 1] = 3000
    out = np.frombuffer(live.to_live_audio(stereo.tobytes(), 48000, 2), dtype=np.int16)
    assert len(out) == 1600
    assert abs(int(np.median(out)) - 2000) <= 2
    mono = np.full(1600, 500, dtype=np.int16)
    assert live.to_live_audio(mono.tobytes(), 16000, 1) == mono.tobytes()


def test_final_text_only_for_final_results():
    assert live.final_text(results(" Hello there. ")) == "Hello there."
    assert live.final_text(results("Hello", is_final=False)) == ""
    assert live.final_text(json.dumps({"type": "Metadata"})) == ""
    assert live.final_text("not json") == ""


def test_echo_filter_drops_call_audio_heard_by_the_mic():
    echo = live.EchoFilter(window=6)
    echo.heard_on_call("Can you send the revised budget to Tom?", now=10.0)
    assert echo.is_echo("send the revised budget to Tom", now=11.0)
    assert not echo.is_echo("Yes, I'll do that this afternoon.", now=11.5)
    assert not echo.is_echo("send the revised budget to Tom", now=20.0)  # too long ago
    assert not live.EchoFilter().is_echo("Anything at all")


def test_transcriber_streams_each_track_and_labels_lines():
    received = {"audio": 0, "close": False}

    def handler(ws):
        for message in ws:
            if isinstance(message, bytes):
                received["audio"] += len(message)
                if received["audio"] >= 3200 and not received.get("sent"):
                    received["sent"] = True
                    ws.send(results("Hi Siobhan, thanks for joining."))
            elif json.loads(message).get("type") == "CloseStream":
                received["close"] = True
                return

    with serve(handler, "127.0.0.1", 0) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.socket.getsockname()[1]
        lines = []
        transcriber = live.LiveTranscriber("key", "en", [], on_line=lambda w, t: lines.append((w, t)),
                                           on_status=lines.append)
        transcriber.url = f"ws://127.0.0.1:{port}"
        transcriber.add_track("system", "Call", 48000, 2)
        chunk = np.zeros(2400 * 2, dtype=np.int16).tobytes()  # 50 ms stereo at 48 kHz
        for _ in range(6):
            transcriber.feed("system", chunk)
        deadline = time.monotonic() + 5
        while not lines and time.monotonic() < deadline:
            time.sleep(0.05)
        transcriber.stop()
        deadline = time.monotonic() + 5
        while not received["close"] and time.monotonic() < deadline:
            time.sleep(0.05)
        server.shutdown()
    assert lines == [("Call", "Hi Siobhan, thanks for joining.")]
    assert received["audio"] == 6 * 800 * 2  # 50 ms at 16 kHz, 16-bit
    assert received["close"]


def test_failed_stream_reports_and_never_blocks_feeding(monkeypatch):
    monkeypatch.setattr(live, "RETRY_DELAYS", (0.01,))
    statuses = []
    transcriber = live.LiveTranscriber("key", "en", [], on_line=lambda *a: None,
                                       on_status=statuses.append)
    transcriber.url = "ws://127.0.0.1:9"  # Nothing listens here.
    transcriber.add_track("mic", "Steve", 48000, 1)
    started = time.monotonic()
    for _ in range(live.TrackStream.QUEUE_CHUNKS + 50):  # More than the queue holds.
        transcriber.feed("mic", b"\x00" * 4800)
    assert time.monotonic() - started < 0.5
    deadline = time.monotonic() + 10
    while len(statuses) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    transcriber.stop()
    assert "Retrying" in statuses[0]
    assert "The recording carries on as normal." in statuses[-1]


def test_track_writer_passes_chunks_to_listener_and_survives_its_errors(tmp_path):
    seen = []

    def listener(data):
        seen.append(len(data))
        raise RuntimeError("live transcript broke")

    info = audio.TrackInfo(tmp_path / "raw_mic.wav", 48000, 1)
    writer = audio._TrackWriter(info, fill_gaps=False, on_audio=listener)
    for _ in range(3):
        writer._queue.put((time.monotonic(), b"\x01\x00" * 480))
    writer.close()
    assert seen == [960, 960, 960]
    assert info.frames_written == 1440
