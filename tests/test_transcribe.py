import httpx2

from scribe import transcribe


def utt(start, end, channel, speaker, text):
    return {"start": start, "end": end, "channel": channel, "speaker": speaker,
            "transcript": text, "confidence": 0.9}


def test_labels_channels_and_merges():
    data = {"results": {"utterances": [
        utt(0.0, 2.0, 1, 0, "Morning, thanks for joining."),
        utt(2.5, 3.5, 1, 0, "Shall we start?"),
        utt(4.0, 6.0, 0, 0, "Yes, let's go through the roadmap."),
        utt(7.0, 9.0, 1, 1, "I have the numbers ready."),
    ]}}
    segments = transcribe.parse_response(data, channels=2, user_name="Steve")
    assert [s.speaker for s in segments] == ["Speaker 1", "Steve", "Speaker 2"]
    assert segments[0].text == "Morning, thanks for joining. Shall we start?"


def test_drops_mic_echo_of_speakers():
    data = {"results": {"utterances": [
        utt(0.0, 3.0, 1, 0, "The deadline is next Friday for the launch."),
        utt(0.2, 3.1, 0, 0, "the deadline is next friday for the launch"),
        utt(4.0, 5.0, 0, 0, "Great, I'll book the room."),
    ]}}
    segments = transcribe.parse_response(data, channels=2, user_name="Me")
    assert [(s.speaker, s.text) for s in segments] == [
        ("Speaker 1", "The deadline is next Friday for the launch."),
        ("Me", "Great, I'll book the room."),
    ]


def test_mono_uses_speaker_numbers():
    data = {"results": {"utterances": [utt(0, 1, 0, 0, "Hello."), utt(1.2, 2, 0, 1, "Hi.")]}}
    segments = transcribe.parse_response(data, channels=1, user_name="Me")
    assert [s.speaker for s in segments] == ["Speaker 1", "Speaker 2"]


def test_timestamp_and_markdown():
    assert transcribe.format_timestamp(3725.4) == "01:02:05"
    md = transcribe.to_markdown([transcribe.Segment(0, 1, "Me", "Hi")], "T")
    assert "**[00:00:00] Me:** Hi" in md


def test_request_sends_expected_params(tmp_path, monkeypatch):
    audio = tmp_path / "rec.flac"
    audio.write_bytes(b"fLaC")
    captured = {}

    def fake_post(url, params, headers, content, timeout):
        captured.update(url=url, params=params, headers=headers, body=content.read())
        return httpx2.Response(200, json={"results": {"utterances": []}})

    monkeypatch.setattr(transcribe.httpx2, "post", fake_post)
    transcribe.request_transcript(audio, "key123", "en-GB", channels=2)
    assert captured["params"]["model"] == "nova-3"
    assert captured["params"]["multichannel"] == "true"
    assert captured["params"]["diarize"] == "true"
    assert captured["params"]["language"] == "en-GB"
    assert captured["headers"]["Authorization"] == "Token key123"
    assert captured["body"] == b"fLaC"
