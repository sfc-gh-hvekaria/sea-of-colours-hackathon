"""Snowflake Cortex agent REST client for SOC_RED_REAPER.

Mirrors :mod:`agent_arena_v4.orchestrator.agent_invoker` so the
deployment / token shape stays familiar across both projects. The
heavy lifting (tool plumbing, schema enforcement) lives inside the
Snowflake agent spec; this client just streams the SSE response and
hands the tool calls + final text back to the caller.

Credentials lookup (first hit wins):

* ``SNOWFLAKE_PAT`` environment variable — still preferred, because a
  token in the environment is a token not sitting in a file.
* the resolved Snowflake connection (v1.45): ``token``, or ``password``
  under a PAT authenticator, in the standard ``connections.toml`` /
  ``config.toml``; ``pat=`` / ``access_token=`` in a legacy sf_config.

The account comes from that same connection, so the REST path and the
persistence path can never drift onto two different accounts.

When the PAT is missing this module returns an explanatory error
envelope rather than raising — the orchestrator can fall back to the
heuristic agent without crashing the request.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional


SNOWFLAKE_PAT_ENV = "SNOWFLAKE_PAT"


def _load_sf_props(path: Optional[str] = None) -> Dict[str, str]:
    """Credentials, standard connection store first (v1.45).

    A shim over :mod:`sea_of_colours.snowpark.sfconn`; the name stays
    because V12 and the tests import it.
    """
    from sea_of_colours.snowpark import sfconn

    return sfconn.load_props(path)


class CortexAgentInvoker:
    """Stream a Cortex agent run over REST."""

    def __init__(
        self,
        *,
        # NOTE: callers should pass ``agent_name`` explicitly when
        # running with multiple AI agents. The default kept here keeps
        # back-compat with the original single-agent deployment.
        agent_name: str = "SOC_RED_REAPER",
        # None → resolve from snowpark.naming (SOC_DATABASE / SOC_SCHEMA),
        # so the invoker follows whichever deployment the rest of the
        # process is pointed at instead of pinning a literal.
        database: Optional[str] = None,
        schema: Optional[str] = None,
        config_file: Optional[str] = None,
        pat_token: Optional[str] = None,
        text_completion_predicate: Optional[Any] = None,
    ) -> None:
        from sea_of_colours.snowpark import naming

        self.agent_name = agent_name
        self.database = database or naming.database()
        self.schema = schema or naming.schema()
        # v1.9 — optional early-termination predicate for text-mode agents.
        # When set, the SSE loop calls ``text_completion_predicate(accum)``
        # after each chunk. If it returns True, the invoker closes the
        # socket and stops reading — the same pattern the tool_use branch
        # uses to cut off "same tool call three times" haiku hedging.
        # Predicate takes the running response string and returns bool.
        # Kept optional (defaults to None) so back-compat is preserved
        # for every existing agent — v2/v3/v4 still stream to their cap.
        self.text_completion_predicate = text_completion_predicate
        # v1.45 — None means "resolve normally" (standard connection store,
        # then legacy). Only an explicitly passed file pins one.
        self.config_file = config_file
        self.pat_token = pat_token or os.environ.get(SNOWFLAKE_PAT_ENV, "")

        props = _load_sf_props(self.config_file)
        self.account = props.get("account", "")
        if not self.pat_token:
            self.pat_token = props.get("pat", "") or props.get("access_token", "")

        account_clean = (self.account or "").lower().replace("_", "-")
        self.api_base = (
            f"https://{account_clean}.snowflakecomputing.com" if account_clean else ""
        )
        self.agent_endpoint = (
            f"{self.api_base}/api/v2/databases/{self.database}"
            f"/schemas/{self.schema}/agents/{self.agent_name}:run"
        )
        # Resolve the per-instance wall-clock cap once at construction so
        # callers that don't pass an explicit ``wallclock_cap_s`` to
        # ``invoke()`` still get the agent-specific ceiling.
        self._wallclock_cap_s = self.WALLCLOCK_CAP_OVERRIDES.get(
            self.agent_name, self.DEFAULT_WALLCLOCK_CAP_S,
        )
        self._response_cap_bytes = self.RESPONSE_CAP_OVERRIDES.get(
            self.agent_name, self.DEFAULT_RESPONSE_CAP_BYTES,
        )

    def is_ready(self) -> bool:
        return bool(self.pat_token and self.account)

    # Defaults are deliberate "cap rather than kill" ceilings: thorough
    # reasoning is welcome, but a runaway loop / multi-minute analysis-
    # paralysis turn lands in the heuristic fallback path instead of
    # stalling the game. Override either via kwarg if you really want
    # a longer run.
    #
    # ``DEFAULT_TIMEOUT_S`` is the per-chunk read timeout passed to
    # ``requests`` — once data starts flowing, the stream itself is
    # bounded by ``DEFAULT_WALLCLOCK_CAP_S`` (the *total* wall-clock cap
    # we enforce inside the iter_lines loop). The historical Lux_Hollow
    # bug was that a babbling Cortex turn could stream for 4+ minutes
    # because the per-chunk timeout never fires when chunks keep arriving.
    #
    # 2026-05-26: the cap was raised from 75s → 150s after the Vetus_Beacon
    # audit showed Cortex routinely needs 80-100s to think on Day 3+
    # (when world.live is dense). The user's stance: prefer good decisions
    # over fast ones. The wall-clock cap is a safety net for runaway
    # streams, not an aggressive deadline.
    DEFAULT_TIMEOUT_S = 180
    DEFAULT_WALLCLOCK_CAP_S = 150
    DEFAULT_RESPONSE_CAP_BYTES = 12_000

    # Per-agent overrides. SOC_RED_REAPER_GRID_FAST pins claude-3-5-haiku
    # with a 50s orchestration budget; we cap the SSE stream at 55s so a
    # delayed soc_submit_policy commit (the warehouse-visibility race the
    # default 150s cap was sized for) still has a 5s window to land before
    # the runtime concludes the turn failed and falls back to the heuristic.
    # Deliberately keep the default at 150s so V1/V2 specs are unaffected.
    WALLCLOCK_CAP_OVERRIDES: Dict[str, int] = {
        "SOC_RED_REAPER_GRID_FAST": 55,
        # PILOT trades speed for a clean full-board read: 120s orchestration
        # budget + 5s commit buffer. The point of this agent is to prove the
        # model can process the whole grid, not to land sub-60s.
        "SOC_RED_REAPER_PILOT": 125,
    }

    # Per-agent response-body ceilings. The default 12k keeps audit rows
    # compact for terse agents, but PILOT emits a full chain-of-thought we
    # want to surface verbatim in the AGENT "thinking" tab — so give it a
    # roomier ceiling. Still a safety net against a runaway babble stream.
    RESPONSE_CAP_OVERRIDES: Dict[str, int] = {
        "SOC_RED_REAPER_PILOT": 40_000,
    }

    # SOC tool names the agent spec actually wires up. Anything else the
    # model invokes is a hallucination (typically a `soc_get_view` from
    # the pre-harness era) and we surface it explicitly so the runtime
    # can include it in the rationale instead of silently swallowing it.
    _DECLARED_TOOLS = frozenset({"soc_submit_policy", "soc_submit_orbit_actions", "soc_save_rationale"})

    def invoke(
        self,
        prompt: str,
        *,
        timeout: int = DEFAULT_TIMEOUT_S,
        wallclock_cap_s: Optional[int] = None,
        response_cap_bytes: Optional[int] = None,
        tool_choice: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # ``None`` defers to the per-instance caps resolved in ``__init__``
        # (which honour :data:`WALLCLOCK_CAP_OVERRIDES` /
        # :data:`RESPONSE_CAP_OVERRIDES`). Callers that want to force a
        # specific ceiling can still pass an int explicitly.
        if wallclock_cap_s is None:
            wallclock_cap_s = self._wallclock_cap_s
        if response_cap_bytes is None:
            response_cap_bytes = self._response_cap_bytes
        # tool_choice defaults to "auto" (model decides). Callers can
        # pass {"type": "required"} or {"type": "any"} to force a tool
        # call — useful for execution-focused agents like the TACTICAL
        # sibling that must never skip the submit tool.
        if tool_choice is None:
            tool_choice = {"type": "auto"}
        if not self.is_ready():
            return {
                "ok": False,
                "error": (
                    "Cortex invoker not configured. "
                    "Set SNOWFLAKE_PAT or add 'pat=...' to sf_config."
                ),
            }
        try:
            import requests
        except ImportError:
            return {
                "ok": False,
                "error": "`requests` not installed — required for Cortex REST calls.",
            }

        headers = {
            "Authorization": f"Bearer {self.pat_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": prompt}]}
            ],
            "tool_choice": tool_choice,
        }

        started = time.time()
        try:
            response = requests.post(
                self.agent_endpoint,
                headers=headers,
                json=payload,
                timeout=timeout,
                stream=True,
            )
        except Exception as exc:  # pragma: no cover - depends on live REST
            return {"ok": False, "error": f"transport error: {exc}"}

        if response.status_code != 200:
            return {
                "ok": False,
                "error": f"HTTP {response.status_code}: {response.text[:500]}",
                "status_code": response.status_code,
            }

        agent_response = ""
        tool_calls: List[Dict[str, Any]] = []
        tool_errors: List[Dict[str, Any]] = []
        hallucinated_tools: List[str] = []
        submitted_policy = False
        # When True, the SSE loop has hit the response-body ceiling and
        # we stop appending. We DO keep draining the connection so the
        # remote agent can finish its tool calls cleanly (closing the
        # socket mid-stream is what triggers Snowflake retry storms).
        capped = False
        wallclock_capped = False
        current_event: Optional[str] = None
        # Extended-thinking channel isolation. When an agent has extended
        # thinking enabled, reasoning arrives on its OWN SSE events
        # (``response.thinking.delta`` / ``response.thinking``) and MUST NOT
        # land in ``agent_response`` — that buffer is truncated by the
        # response byte cap and inspected by the completion predicate, so a
        # long think would eat the moves-first budget and truncate before
        # the JSON ever streamed (the v6-era "no moves emitted" failure).
        # We accumulate reasoning into this SEPARATE, un-capped buffer that
        # is surfaced for audit only. Agents without thinking never emit
        # these events, so this is a pure no-op for them.
        thinking_response = ""
        # Bound the audit buffer so a runaway think can't balloon memory;
        # this ceiling is independent of (and never touches) the answer cap.
        _thinking_cap_bytes = 40_000

        for line in response.iter_lines():
            # Hard wall-clock cap — independent of the per-chunk timeout.
            # Once we've burned ``wallclock_cap_s`` seconds we close the
            # iterator and surface whatever we collected. This is the
            # fix for the Lux_Hollow 256s-turn pathology.
            if time.time() - started >= wallclock_cap_s:
                wallclock_capped = True
                try:
                    response.close()
                except Exception:
                    pass
                break

            if not line:
                continue
            line = line.decode("utf-8")
            if line.startswith("event: "):
                # Track the current SSE event type so the following
                # ``data:`` frame can be interpreted in context. The
                # important one is ``error`` — Cortex reports orchestration
                # failures (e.g. an unavailable/unauthorized model) as an
                # ``event: error`` frame whose data is {"message": ...}.
                # Before this, those frames were silently skipped, which is
                # why a dead model surfaced as "executed tools but returned
                # no text" with an empty envelope.
                current_event = line[7:].strip()
                continue
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if current_event == "error":
                err_msg = (
                    chunk.get("message")
                    or chunk.get("error")
                    or json.dumps(chunk)
                )
                tool_errors.append(
                    {"tool": "orchestration", "error": str(err_msg)[:1000]}
                )
                current_event = None
                continue

            # Thinking channel — divert reasoning tokens AWAY from the
            # capped/predicate-checked answer buffer. Per the Cortex schema a
            # ``response.thinking.delta`` carries the token in ``data.text``;
            # the aggregated ``response.thinking`` block-done event carries it
            # under a ``thinking`` object. We capture the delta stream for
            # audit and then ``continue`` so NONE of it reaches
            # ``agent_response``, the byte cap, or the completion predicate.
            if current_event and current_event.startswith("response.thinking"):
                if len(thinking_response) < _thinking_cap_bytes:
                    tv = chunk.get("text")
                    if not isinstance(tv, str):
                        tobj = chunk.get("thinking")
                        if isinstance(tobj, dict) and isinstance(
                            tobj.get("text"), str
                        ):
                            tv = tobj["text"]
                    if isinstance(tv, str):
                        thinking_response += tv
                continue

            if not capped:
                text_val = chunk.get("text")
                if isinstance(text_val, str):
                    agent_response += text_val
                content_val = chunk.get("content")
                if isinstance(content_val, str):
                    agent_response += content_val
                elif isinstance(content_val, list):
                    for item in content_val:
                        if isinstance(item, dict) and item.get("type") == "text":
                            agent_response += item.get("text", "")
                # Soft cap: once we've collected enough text, flip the
                # flag and stop appending. Tool calls (below) continue
                # to be recorded so the audit trail is complete.
                if len(agent_response) >= response_cap_bytes:
                    capped = True
                    agent_response = (
                        agent_response[:response_cap_bytes].rstrip()
                        + f"\n\n[…response capped at {response_cap_bytes} bytes]"
                    )

                # v1.9 — text-mode early termination. When the caller
                # passed a ``text_completion_predicate`` at construction
                # time, run it against the accumulated response after
                # every chunk. If it returns True, the response is
                # "complete enough" (e.g. a full balanced JSON object
                # with the required schema fields has arrived) and we
                # close the SSE socket. This mirrors the tool_use branch
                # below, which cuts off "same tool call three times"
                # haiku hedging on the tool path. Text-mode agents get
                # the same benefit without needing tools.
                if (
                    self.text_completion_predicate is not None
                    and not capped
                    and self.text_completion_predicate(agent_response)
                ):
                    try:
                        response.close()
                    except Exception:
                        pass
                    break

            # Tool invocation tracking.
            #
            # Cortex's SSE stream gives us four signals we care about:
            #
            #   * ``executing_tool`` status events → the model is *about*
            #     to call a tool. We learn the tool name here.
            #   * ``tool_use`` objects → richer payloads (sometimes with
            #     arguments). Same lifecycle stage as the status event.
            #   * ``tool_result`` chunks → the warehouse's response for a
            #     prior tool call. Errors land here (and were silently
            #     dropped before this change, which is why Lux_Hollow's
            #     soc_submit_policy failures were invisible).
            #   * top-level ``error`` chunks → orchestration-level
            #     failures (rate limit, auth, schema). Same treatment.
            #
            # All of the above are surfaced in the return envelope so
            # ``runtime.run_agent_turn`` can decide whether Cortex
            # actually got a submission through.
            current_tool_name: Optional[str] = None
            if chunk.get("status") == "executing_tool":
                msg = chunk.get("message", "")
                if msg.startswith("Running "):
                    current_tool_name = msg[8:].strip()
                    tool_calls.append({"name": current_tool_name})
            if "tool_use" in chunk:
                tu = chunk["tool_use"]
                tool_calls.append(tu)
                if isinstance(tu, dict):
                    current_tool_name = (
                        current_tool_name
                        or tu.get("name")
                        or tu.get("tool")
                    )
                    # v3 two-call fix (2025-07-14): tool_choice=required
                    # makes Cortex fire the submit tool 2-3 times per turn,
                    # each re-executing SOC_SUBMIT_POLICY. We must close
                    # the socket at the FIRST FULL tool_use PAYLOAD for a
                    # submit tool — not on the `executing_tool` status
                    # event (that fires before args are streamed, and
                    # closing there submits an empty policy). The payload
                    # includes the tool name AND the input JSON, so once
                    # we've captured this chunk, we have everything we
                    # need and can drop the rest of the stream.
                    tu_name = tu.get("name") or tu.get("tool") or ""
                    if tu_name in (
                        "soc_submit_policy",
                        "soc_submit_orbit_actions",
                    ):
                        submitted_policy = True
                        try:
                            response.close()
                        except Exception:
                            pass
                        break

            # Did the model just (try to) submit a policy? We mark the
            # flag even if the tool result later reports an error — the
            # runtime needs to distinguish "Cortex didn't attempt to
            # submit" (no flag) from "Cortex tried but the proc rejected
            # it" (flag set, error in tool_errors).
            if current_tool_name == "soc_submit_policy":
                submitted_policy = True
            # v1.8 — orbit-phase tool is also a submission signal. The
            # runtime treats submitted_policy=True as "the seat has a
            # pending decision" regardless of which phase's tool fired.
            if current_tool_name == "soc_submit_orbit_actions":
                submitted_policy = True

            if (
                current_tool_name
                and current_tool_name not in self._DECLARED_TOOLS
                and current_tool_name not in hallucinated_tools
            ):
                hallucinated_tools.append(current_tool_name)

            # ``tool_result`` shape varies a bit by warehouse version, so
            # we be flexible: any chunk with a tool_result + an error /
            # is_error flag gets recorded. Same for top-level ``error``
            # objects on the orchestration stream itself.
            tool_result = chunk.get("tool_result")
            if isinstance(tool_result, dict):
                tr_name = (
                    tool_result.get("name")
                    or tool_result.get("tool")
                    or current_tool_name
                )
                tr_err = tool_result.get("error") or tool_result.get("is_error")
                if tr_err:
                    err_text = (
                        tool_result.get("error")
                        if isinstance(tool_result.get("error"), str)
                        else (
                            tool_result.get("message")
                            or tool_result.get("content")
                            or "tool reported error"
                        )
                    )
                    tool_errors.append(
                        {
                            "tool": tr_name or "unknown",
                            "error": str(err_text)[:500],
                        }
                    )
            if "error" in chunk and chunk.get("error"):
                err = chunk["error"]
                if isinstance(err, dict):
                    err_text = (
                        err.get("message")
                        or err.get("code")
                        or json.dumps(err)[:200]
                    )
                else:
                    err_text = str(err)
                tool_errors.append(
                    {
                        "tool": current_tool_name or "orchestration",
                        "error": str(err_text)[:500],
                    }
                )

        # Make sure the stream is fully closed (no-op if we already
        # broke out via the wall-clock branch).
        try:
            response.close()
        except Exception:
            pass

        elapsed_ms = int((time.time() - started) * 1000)

        return {
            "ok": True,
            "response": agent_response.strip()
            if agent_response
            else "(agent executed tools but returned no text)",
            # Reasoning trace from the isolated thinking channel (empty for
            # agents without extended thinking). Audit-only — it never
            # affected the answer buffer, the byte cap, or the predicate.
            "thinking": thinking_response.strip(),
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "hallucinated_tools": hallucinated_tools,
            "submitted_policy": submitted_policy,
            "capped": capped,
            "wallclock_capped": wallclock_capped,
            "elapsed_ms": elapsed_ms,
        }
