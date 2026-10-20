import json

from scribe import pipeline
from scribe.config import Settings
from tests.test_mixdown import write_wav


def test_process_session_end_to_end(tmp_path, monkeypatch):
    settings = Settings(deepgram_api_key="d", anthropic_api_key="a", user_name="Steve",
                        language="en", output_dir=tmp_path, claude_model="claude-opus-5-5",
                        keep_raw_audio=False)
    folder = pipeline.new_session_dir(settings, "Weekly sync with Acme")
    assert folder.name.endswith("Weekly-sync-with-Acme")
    write_wav(folder / "raw_system.wav", 48000, 2, 2.0, 300)
    write_wav(folder / "raw_mic.wav", 48000, 1, 2.0, 500)
    meta = pipeline.SessionMeta(started="2026-10-20 09:00", context="Weekly sync with Acme", tracks={
        "system": {"path": "raw_system.wav", "rate": 48000, "channels": 2, "first_frame_time": 10.0},
        "mic": {"path": "raw_mic.wav", "rate": 48000, "channels": 1, "first_frame_time": 10.1},
    })
    meta.save(folder)

    def fake_request(path, key, language, channels):
        assert channels == 2
        return {"results": {"utterances": [
            {"start": 0.1, "end": 1.0, "channel": 1, "speaker": 0, "transcript": "Can you send the deck?"},
            {"start": 1.2, "end": 1.9, "channel": 0, "speaker": 0, "transcript": "Yes, by Friday."},
        ]}}

    calls = {}

    def fake_summarize(transcript, **kwargs):
        calls["transcript"] = transcript
        calls.update(kwargs)
        return "# Notes"

    monkeypatch.setattr(pipeline.transcribe, "request_transcript", fake_request)
    monkeypatch.setattr(pipeline.summarize, "summarize", fake_summarize)
    summary = pipeline.process_session(folder, settings, log=lambda m: None)

    assert summary.read_text() == "# Notes\n"
    assert "Steve: Yes, by Friday." in calls["transcript"]
    assert calls["context"] == "Weekly sync with Acme"
    assert (folder / "recording.flac").exists()
    assert not (folder / "raw_system.wav").exists()
    assert json.loads((folder / "session.json").read_text())["channels"] == 2
