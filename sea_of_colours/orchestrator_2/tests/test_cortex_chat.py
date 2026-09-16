"""Unit tests for the Cortex inference (chat/completions) client.

These lock in the containment-fix envelope: a hard-capped, schema-guaranteed
answer with a clean return shape compatible with the Agents invoker, and safe
degradation (non-200 / timeout) so the harness's finisher/heuristic net can
engage.
"""

from __future__ import annotations

import json

import pytest

from sea_of_colours.orchestrator_2.cortex_chat import CortexChatInvoker
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import chat_schema


class _FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def _ready(monkeypatch, resp=None, exc=None, capture=None):
    inv = CortexChatInvoker(pat_token="fake-pat")
    inv.account = "acct"  # force is_ready() True without a real config
    inv.api_base = "https://acct.snowflakecomputing.com"
    inv.endpoint = inv.api_base + "/api/v2/cortex/v1/chat/completions"

    import requests

    def fake_post(url, headers=None, json=None, timeout=None):
        if capture is not None:
            capture["url"] = url
            capture["payload"] = json
            capture["timeout"] = timeout
        if exc is not None:
            raise exc
        return resp

    monkeypatch.setattr(requests, "post", fake_post)
    return inv


def test_happy_path_parses_content_and_channels(monkeypatch):
    body = {
        "choices": [{
            "message": {"content": '{"moves":[{"a":"pickup","unit":"h"}],"plan_this_turn":"lift"}',
                        "reasoning_content": "thought about it"},
            "finish_reason": "stop",
        }],
        "usage": {"completion_tokens": 40},
    }
    cap = {}
    inv = _ready(monkeypatch, resp=_FakeResp(200, body), capture=cap)
    res = inv.invoke("prompt", wallclock_cap_s=30)

    assert res["ok"] is True
    assert '"moves"' in res["response"]
    assert res["thinking"] == "thought about it"
    assert res["capped"] is False
    # payload wiring
    assert cap["payload"]["model"] == "claude-haiku-4-5"
    assert cap["payload"]["max_completion_tokens"] == 2000
    assert cap["payload"]["stream"] is False
    assert cap["timeout"] == 30


def test_response_format_and_reasoning_are_forwarded(monkeypatch):
    cap = {}
    inv = CortexChatInvoker(
        pat_token="fake-pat",
        response_format=chat_schema.MOVES_RESPONSE_FORMAT,
        reasoning={"max_tokens": 1500},
    )
    inv.account = "acct"
    inv.endpoint = "https://acct.snowflakecomputing.com/api/v2/cortex/v1/chat/completions"
    import requests
    body = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: (cap.update(k) or _FakeResp(200, body)))
    inv.invoke("p", wallclock_cap_s=20)
    payload = cap["json"]
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["reasoning"] == {"max_tokens": 1500}
    # reasoning enabled → temperature forced to 1
    assert payload["temperature"] == 1.0


def test_finish_reason_length_flags_capped(monkeypatch):
    body = {"choices": [{"message": {"content": "{...truncated"}, "finish_reason": "length"}]}
    inv = _ready(monkeypatch, resp=_FakeResp(200, body))
    res = inv.invoke("p")
    assert res["capped"] is True


def test_non_200_degrades_safely(monkeypatch):
    inv = _ready(monkeypatch, resp=_FakeResp(400, "bad request"))
    res = inv.invoke("p")
    assert res["ok"] is False
    assert res["response"] == ""
    assert res["status_code"] == 400


def test_timeout_marks_wallclock_capped(monkeypatch):
    class _Timeout(Exception):
        pass

    _Timeout.__name__ = "ReadTimeout"
    inv = _ready(monkeypatch, exc=_Timeout("timed out"))
    res = inv.invoke("p", wallclock_cap_s=5)
    assert res["ok"] is False
    assert res["wallclock_capped"] is True


def test_not_ready_without_creds():
    inv = CortexChatInvoker(pat_token="")
    inv.account = ""
    res = inv.invoke("p")
    assert res["ok"] is False


def test_schema_constant_wellformed():
    rf = chat_schema.MOVES_RESPONSE_FORMAT
    assert rf["type"] == "json_schema"
    schema = rf["json_schema"]["schema"]
    assert schema["required"] == ["moves", "plan_this_turn"]
    item = schema["properties"]["moves"]["items"]
    assert item["required"] == ["a"]
    assert item["additionalProperties"] is False
    # Grounded reflection must be EXPRESSIBLE (present in the schema) so the
    # chat mover can emit it — but OPTIONAL (not required) so night 1 can omit.
    refl = schema["properties"]["reflection_on_last_night"]
    assert refl["type"] == "object"
    assert refl["additionalProperties"] is False
    assert set(refl["required"]) == {"actual", "gap_reason"}
    assert "reflection_on_last_night" not in schema["required"]
    assert refl["properties"]["actual"]["type"] == "integer"
