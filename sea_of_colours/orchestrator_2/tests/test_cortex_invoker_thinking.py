"""Extended-thinking channel isolation in the shared Cortex invoker.

These tests drive the SSE parser in ``sea_of_colours.agent.cortex_invoker``
with a mocked streaming ``requests`` response. They lock in the v7 guarantee:
reasoning tokens that arrive on ``response.thinking.delta`` events must be
routed to a SEPARATE audit buffer and must NEVER

  * count against the response byte cap, or
  * be fed to the JSON completion predicate,

so that the moves-first answer always gets the full byte budget — even after a
long think in the SAME agent call.
"""

from __future__ import annotations

import json

import pytest

from sea_of_colours.agent.cortex_invoker import CortexAgentInvoker


class _FakeStreamResponse:
    """Minimal stand-in for a streaming ``requests`` response."""

    def __init__(self, lines):
        self.status_code = 200
        self._lines = lines
        self.closed = False
        self.text = ""

    def iter_lines(self):
        for line in self._lines:
            if self.closed:
                break
            yield line

    def close(self):
        self.closed = True


def _sse(*events):
    """Build a byte SSE stream from (event, data_dict) pairs."""
    out = []
    for event, data in events:
        out.append(f"event: {event}".encode())
        out.append(f"data: {json.dumps(data)}".encode())
        out.append(b"")
    out.append(b"data: [DONE]")
    return out


def _ready_invoker(monkeypatch, lines, **kwargs):
    inv = CortexAgentInvoker(agent_name="TEST_AGENT", pat_token="fake-pat", **kwargs)
    inv.account = "acct"  # force is_ready() True without a real config file

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeStreamResponse(lines))
    return inv


def _json_moves_complete(s: str) -> bool:
    s = s.strip()
    if '"moves"' not in s:
        return False
    try:
        json.loads(s)
    except Exception:
        return False
    return True


def test_thinking_diverted_from_answer_buffer(monkeypatch):
    answer = '{"moves":[{"a":"pickup","unit":"harvester_p1"}],"plan_this_turn":"lift"}'
    lines = _sse(
        ("response.thinking.delta", {"content_index": 0, "text": "let me score "}),
        ("response.thinking.delta", {"content_index": 0, "text": "the red cells"}),
        ("response.text.delta", {"content_index": 1, "text": answer}),
    )
    inv = _ready_invoker(monkeypatch, lines, text_completion_predicate=_json_moves_complete)
    res = inv.invoke("prompt")

    assert res["ok"] is True
    # Answer channel holds ONLY the JSON, no reasoning prose.
    assert res["response"] == answer
    assert "score" not in res["response"]
    # Reasoning captured separately for audit.
    assert res["thinking"] == "let me score the red cells"
    assert res["capped"] is False


def test_long_thinking_does_not_consume_answer_byte_cap(monkeypatch):
    # Thinking is far larger than the response cap; the moves-first answer is
    # tiny. With channel isolation the answer must survive intact and uncapped.
    big_think = "x" * 5000
    answer = '{"moves":[{"a":"pickup","unit":"h"}]}'
    lines = _sse(
        ("response.thinking.delta", {"content_index": 0, "text": big_think}),
        ("response.text.delta", {"content_index": 1, "text": answer}),
    )
    inv = _ready_invoker(monkeypatch, lines, text_completion_predicate=_json_moves_complete)
    res = inv.invoke("prompt", response_cap_bytes=200)

    assert res["capped"] is False, "answer cap must not be tripped by thinking bytes"
    assert res["response"] == answer  # moves-first fired despite a 5KB think
    assert "x" not in res["response"]
    assert res["thinking"].startswith("x" * 100)


def test_thinking_never_trips_completion_predicate(monkeypatch):
    # Adversarial: the THINKING text itself looks like a complete moves JSON.
    # Because thinking is diverted before the predicate runs, the socket must
    # NOT close on it — the real answer that follows must still be captured.
    fake_json_in_think = '{"moves":[{"a":"probe","at":[1,2]}]}'
    real_answer = '{"moves":[{"a":"drop","unit":"harvester_p1","at":[5,5]},{"a":"pickup","unit":"harvester_p1"}]}'
    lines = _sse(
        ("response.thinking.delta", {"content_index": 0, "text": fake_json_in_think}),
        ("response.text.delta", {"content_index": 1, "text": real_answer}),
    )
    inv = _ready_invoker(monkeypatch, lines, text_completion_predicate=_json_moves_complete)
    res = inv.invoke("prompt")

    assert res["response"] == real_answer
    assert "probe" not in res["response"]  # the think's fake JSON never leaked


def test_no_thinking_events_is_a_noop(monkeypatch):
    # Agents without extended thinking never emit thinking events; behaviour
    # must be byte-for-byte the pre-change path (answer accumulates, empty
    # thinking buffer).
    answer = '{"moves":[{"a":"pickup","unit":"h"}]}'
    lines = _sse(("response.text.delta", {"content_index": 0, "text": answer}))
    inv = _ready_invoker(monkeypatch, lines, text_completion_predicate=_json_moves_complete)
    res = inv.invoke("prompt")

    assert res["response"] == answer
    assert res["thinking"] == ""
