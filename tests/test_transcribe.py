import httpx2

from scribe import transcribe


def response(*channels):
    """Build a Deepgram multichannel response from (start, speaker, text) words per channel."""
    out = []
    for words in channels:
        out.append({"alternatives": [{"words": [
            {"word": t.lower().strip(".,?!"), "punctuated_word": t, "start": s, "end": s + 0.3,
             "speaker": spk, "confidence": 0.99}
            for s, spk, t in words
        ]}]})
    return {"results": {"channels": out}}


def say(start, speaker, sentence, step=0.35):
    return [(start + i * step, speaker, w) for i, w in enumerate(sentence.split())]


def lines(segments):
    return [(s.speaker, s.text) for s in segments]


def test_labels_channels_and_groups_turns():
    data = response(
        say(4.0, 0, "Yes, let's go through the roadmap."),
        say(0.0, 0, "Morning, thanks for joining.") + say(2.0, 0, "Shall we start?")
        + say(7.0, 1, "I have the numbers ready."),
    )
    segments = transcribe.parse_response(data, channels=2, user_name="Steve")
    assert lines(segments) == [
        ("Speaker 1", "Morning, thanks for joining. Shall we start?"),
        ("Steve", "Yes, let's go through the roadmap."),
        ("Speaker 2", "I have the numbers ready."),
    ]


def test_short_reply_leaking_into_mic_goes_to_the_other_person():
    # Steve thanks them; their "You're welcome." is heard by his mic as well.
    data = response(
        say(9.5, 0, "So thank you very much.") + say(11.3, 0, "You're welcome.")
        + say(12.6, 0, "Bye."),
        say(11.2, 0, "You're welcome."),
    )
    segments = transcribe.parse_response(data, channels=2, user_name="Steve")
    assert lines(segments) == [
        ("Steve", "So thank you very much."),
        ("Speaker 1", "You're welcome."),
        ("Steve", "Bye."),
    ]


def test_echo_inside_own_speech_is_removed_but_shared_single_word_kept():
    data = response(
        # Steve talks; the mic also catches "next Friday" from the call mid-sentence,
        # and he happens to say "yes" while the other person does too.
        say(0.0, 0, "I think") + say(0.7, 0, "next Friday") + say(1.4, 0, "works, yes and more"),
        say(0.75, 0, "next Friday") + say(2.1, 0, "yes"),
    )
    segments = transcribe.parse_response(data, channels=2, user_name="Me")
    mine = " ".join(s.text for s in segments if s.speaker == "Me")
    assert "next Friday" not in mine
    assert "yes" in mine


def test_extra_voice_on_mic_is_numbered_after_call_speakers():
    data = response(
        say(0.0, 0, "Hello everyone, this is my update.") + say(5.0, 1, "And me too."),
        say(3.0, 0, "Thanks."),
    )
    segments = transcribe.parse_response(data, channels=2, user_name="Steve")
    assert [s.speaker for s in segments] == ["Steve", "Speaker 1", "Speaker 2"]


def test_mono_uses_speaker_numbers():
    data = response(say(0, 0, "Hello.") + say(2.0, 1, "Hi."))
    segments = transcribe.parse_response(data, channels=1, user_name="Me")
    assert [s.speaker for s in segments] == ["Speaker 1", "Speaker 2"]


def test_rename_speakers_merges_adjacent_lines():
    segs = [transcribe.Segment(0, 1, "Speaker 1", "Hi."), transcribe.Segment(1.2, 2, "Speaker 3", "Hello.")]
    renamed = transcribe.rename_speakers(segs, {"Speaker 1": "Sarah", "Speaker 3": "Sarah"})
    assert lines(renamed) == [("Sarah", "Hi. Hello.")]


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
        return httpx2.Response(200, json={"results": {"channels": []}})

    monkeypatch.setattr(transcribe.httpx2, "post", fake_post)
    transcribe.request_transcript(audio, "key123", "en-GB", channels=2)
    assert captured["params"]["model"] == "nova-3"
    assert captured["params"]["multichannel"] == "true"
    assert captured["params"]["diarize"] == "true"
    assert captured["params"]["language"] == "en-GB"
    assert captured["headers"]["Authorization"] == "Token key123"
    assert captured["body"] == b"fLaC"
