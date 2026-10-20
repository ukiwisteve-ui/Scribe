import json

import httpx2

from scribe import summarize


def sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def test_summarize_request_and_text(monkeypatch):
    seen = {}

    def handler(request: httpx2.Request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        message = {"id": "msg_1", "type": "message", "role": "assistant",
                   "model": "claude-opus-5-5", "content": [], "stop_reason": None,
                   "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}
        events = [
            {"type": "message_start", "message": message},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "# Weekly sync"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
             "usage": {"output_tokens": 5}},
            {"type": "message_stop"},
        ]
        return httpx2.Response(200, text=sse(events), headers={"content-type": "text/event-stream"})

    real_client = summarize.anthropic.Anthropic

    def fake_client(**kwargs):
        return real_client(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    monkeypatch.setattr(summarize.anthropic, "Anthropic", fake_client)
    text = summarize.summarize("[00:00:01] Steve: Hi", api_key="k", model="claude-opus-5-5",
                               user_name="Steve", context="Weekly sync", recorded_at="2026-10-20 09:00",
                               duration="30 min")
    assert text == "# Weekly sync"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert "Weekly sync" in body["messages"][0]["content"]
    assert "Steve: Hi" in body["messages"][0]["content"]
