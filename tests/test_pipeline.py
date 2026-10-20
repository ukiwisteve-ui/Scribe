import json

from scribe import pipeline
from scribe.config import Settings
from tests.test_mixdown import write_wav
from tests.test_transcribe import response, say


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
    meta.participants = "Sarah from Acme"
    meta.save(folder)
    settings.vocabulary = ["OKRs"]
    calls = {}

    def fake_request(path, key, language, channels, keyterms=None):
        assert channels == 2
        calls["keyterms"] = keyterms
        return response(say(1.2, 0, "Yes, by Friday."), say(0.1, 0, "Can you send the deck?"))


    def fake_summarize(transcript, **kwargs):
        calls["transcript"] = transcript
        calls.update(kwargs)
        return "# Notes"

    monkeypatch.setattr(pipeline.transcribe, "request_transcript", fake_request)
    monkeypatch.setattr(pipeline.summarize, "summarize", fake_summarize)
    summary = pipeline.process_session(folder, settings, log=lambda m: None)

    assert summary.read_text() == "# Notes\n"
    assert "Steve: Yes, by Friday." in calls["transcript"]
    assert "Speaker 1: Can you send the deck?" in calls["transcript"]
    assert calls["context"] == "Weekly sync with Acme"
    assert calls["recording_note"] == ""
    assert calls["keyterms"] == ["Sarah", "Acme", "Steve", "OKRs"]
    assert (folder / "recording.flac").exists()
    assert not (folder / "raw_system.wav").exists()
    assert json.loads((folder / "session.json").read_text())["channels"] == 2


def make_session(tmp_path, data, **meta_fields):
    settings = Settings(deepgram_api_key="d", anthropic_api_key="a", user_name="Steve",
                        language="en", output_dir=tmp_path, claude_model="claude-opus-5-5",
                        keep_raw_audio=False)
    folder = pipeline.new_session_dir(settings, "")
    (folder / pipeline.AUDIO_FILE).write_bytes(b"already mixed")
    (folder / pipeline.RESPONSE_FILE).write_text(json.dumps(data))
    pipeline.SessionMeta(started="2026-10-20 10:06", channels=2, duration_seconds=17,
                         **meta_fields).save(folder)
    return settings, folder


def test_silent_call_channel_is_flagged(tmp_path, monkeypatch):
    data = response(say(0.5, 0, "Thank you very much.") + say(2.0, 0, "You're welcome."), [])
    settings, folder = make_session(tmp_path, data)
    calls, logs = {}, []
    monkeypatch.setattr(pipeline.summarize, "summarize", lambda t, **k: calls.update(k) or "# N")
    pipeline.process_session(folder, settings, log=logs.append)
    assert "No speech was heard on the call-audio channel" in calls["recording_note"]
    assert any("no speech was heard from the call audio" in m for m in logs)


def test_names_and_participants_reach_transcript_and_summary(tmp_path, monkeypatch):
    data = response(say(0.0, 0, "Morning."), say(1.0, 0, "Hi Steve, it's Sarah."))
    settings, folder = make_session(tmp_path, data, participants="Sarah from Acme",
                                    speaker_names={"Speaker 1": "Sarah"})
    calls = {}

    def fake_summarize(transcript, **kwargs):
        calls["transcript"] = transcript
        calls.update(kwargs)
        return "# N"

    monkeypatch.setattr(pipeline.summarize, "summarize", fake_summarize)
    pipeline.process_session(folder, settings, log=lambda m: None)
    assert "Sarah: Hi Steve, it's Sarah." in calls["transcript"]
    assert calls["participants"] == "Sarah from Acme"
    assert "Sarah:** Hi Steve" in (folder / "transcript.md").read_text()
    # Samples for the naming dialog use the original labels.
    assert set(pipeline.speaker_samples(folder)) == {"Steve", "Speaker 1"}
