import json

import httpx2

from scribe import history, summarize
from tests.test_summarize import sse


def make_call(root, name, started, summary=None, transcript="", context=""):
    folder = root / name
    folder.mkdir()
    (folder / "session.json").write_text(json.dumps(
        {"started": started, "context": context, "duration_seconds": 600}))
    if summary is not None:
        (folder / "summary.md").write_text(summary, encoding="utf-8")
    if transcript:
        (folder / "transcript.md").write_text(transcript, encoding="utf-8")
    return folder


def test_lists_calls_newest_first_with_titles(tmp_path):
    make_call(tmp_path, "a", "2026-10-14 10:00", "# Q4 planning with Acme\n\n## Summary\nx")
    make_call(tmp_path, "b", "2026-10-19 09:15", context="Weekly team call\nmore")
    make_call(tmp_path, "c", "2026-10-16 14:30")
    (tmp_path / "not-a-session").mkdir()
    calls = history.list_calls(tmp_path)
    assert [(c.started, c.title) for c in calls] == [
        ("2026-10-19 09:15", "Weekly team call"),
        ("2026-10-16 14:30", "Untitled call"),
        ("2026-10-14 10:00", "Q4 planning with Acme"),
    ]
    assert calls[2].has_summary and not calls[0].has_summary
    assert history.list_calls(tmp_path / "missing") == []


def test_search_matches_every_word_in_notes_or_transcript(tmp_path):
    make_call(tmp_path, "a", "2026-10-14 10:00", "# Launch\nThe launch moves to November.",
              "Steve: Sarah will check the Budget.")
    make_call(tmp_path, "b", "2026-10-16 14:30", "# Hiring\nTwo candidates.", "Priya: budget")
    calls = history.list_calls(tmp_path)
    assert [c.title for c in history.search(calls, "budget")] == ["Hiring", "Launch"]
    assert [c.title for c in history.search(calls, "BUDGET november")] == ["Launch"]
    assert history.search(calls, "  ") == calls
    assert history.search(calls, "nothing-like-this") == []


def test_question_prompt_prefers_newer_calls_within_budget(tmp_path):
    make_call(tmp_path, "old", "2026-10-01 09:00", "# Old call\nOld notes", "old words " * 50)
    make_call(tmp_path, "new", "2026-10-19 09:00", "# New call\nNew notes", "new words")
    calls = history.list_calls(tmp_path)
    prompt, count = history.build_question_prompt("What did we agree?", calls)
    assert count == 2
    assert prompt.index('title="New call"') < prompt.index('title="Old call"')
    assert "<transcript>\nnew words\n</transcript>" in prompt
    assert prompt.endswith("Question about these calls: What did we agree?")
    # A tight budget keeps the old call's notes but drops its transcript.
    assert "The question is from" not in prompt
    prompt, _ = history.build_question_prompt("q", calls, "Steve")
    assert "The question is from Steve, who recorded these calls" in prompt
    prompt, count = history.build_question_prompt("q", calls, budget=320)
    assert count == 2 and "old words" not in prompt and "Old notes" in prompt
    _, count = history.build_question_prompt("q", calls, budget=150)
    assert count == 1


def test_ask_streams_the_answer(tmp_path, monkeypatch):
    make_call(tmp_path, "a", "2026-10-14 10:00", "# Launch\nLaunch on 18 November.")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        message = {"id": "msg_1", "type": "message", "role": "assistant",
                   "model": "claude-opus-5-5", "content": [], "stop_reason": None,
                   "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}
        events = [{"type": "message_start", "message": message},
                  {"type": "content_block_start", "index": 0,
                   "content_block": {"type": "text", "text": ""}}]
        for piece in ("You agreed ", "18 November."):
            events.append({"type": "content_block_delta", "index": 0,
                           "delta": {"type": "text_delta", "text": piece}})
        events += [{"type": "content_block_stop", "index": 0},
                   {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                                                       "stop_sequence": None},
                    "usage": {"output_tokens": 5}},
                   {"type": "message_stop"}]
        return httpx2.Response(200, text=sse(events), headers={"content-type": "text/event-stream"})

    real_client = summarize.anthropic.Anthropic
    monkeypatch.setattr(summarize.anthropic, "Anthropic", lambda **kw: real_client(
        **kw, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    settings = type("S", (), {"anthropic_api_key": "k", "claude_model": "claude-opus-5-5"})()
    chunks = []
    answer = history.ask("When is the launch?", history.list_calls(tmp_path), settings,
                         on_text=chunks.append)
    assert answer == "You agreed 18 November."
    assert chunks == ["You agreed ", "18 November."]
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["stream"] is True
    assert body["thinking"] == {"type": "adaptive"}
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert body["system"] == history.ASK_SYSTEM_PROMPT
    assert "Launch on 18 November." in body["messages"][0]["content"]
