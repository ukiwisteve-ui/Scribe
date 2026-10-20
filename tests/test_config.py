import os

from scribe import config


def test_save_setting_replaces_uncomments_or_appends(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DEEPGRAM_API_KEY=abc\n# SCRIBE_LIVE_TRANSCRIPT=true\nSCRIBE_RECORD_PROMPT=true\n",
                   encoding="utf-8")
    monkeypatch.delenv("SCRIBE_LIVE_TRANSCRIPT", raising=False)
    monkeypatch.delenv("SCRIBE_RECORD_PROMPT", raising=False)
    monkeypatch.delenv("SCRIBE_NEW", raising=False)
    config.save_setting("SCRIBE_LIVE_TRANSCRIPT", "false", env)
    config.save_setting("SCRIBE_RECORD_PROMPT", "false", env)
    config.save_setting("SCRIBE_NEW", "1", env)
    assert env.read_text(encoding="utf-8") == (
        "DEEPGRAM_API_KEY=abc\nSCRIBE_LIVE_TRANSCRIPT=false\nSCRIBE_RECORD_PROMPT=false\n"
        "SCRIBE_NEW=1\n")
    assert os.environ["SCRIBE_LIVE_TRANSCRIPT"] == "false"
    settings = config.Settings.load()
    assert settings.live_transcript is False and settings.record_prompt is False


def test_live_and_prompt_default_on(monkeypatch):
    monkeypatch.delenv("SCRIBE_LIVE_TRANSCRIPT", raising=False)
    monkeypatch.delenv("SCRIBE_RECORD_PROMPT", raising=False)
    settings = config.Settings.load()
    assert settings.live_transcript and settings.record_prompt
