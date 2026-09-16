"""Cortex *inference* client — the ``/api/v2/cortex/v1/chat/completions``
endpoint, as opposed to the Cortex **Agents** endpoint
(``.../agents/{name}:run``) that :mod:`sea_of_colours.agent.cortex_invoker`
drives.

Why this exists (the containment fix)
------------------------------------
The Agents API wraps the model in a server-side orchestration loop. When a
prompt invites open-ended reasoning, that loop triggers **unbounded extended
thinking** (measured: haiku ~27 KB / ~30-44 s) which shares ONE opaque budget
with the answer + tools — so the agent routinely burns the budget before it
emits a complete plan. That is the project-wide "ran out before it finished"
failure; v6's "reason in your head" only papered over it by *suppressing*
thinking entirely.

The inference chat/completions API does NOT do this. It behaves like a normal
chat model:

  * ``max_completion_tokens`` is a HARD, honored answer budget — the model
    self-budgets to fit and always returns a complete answer (measured: haiku
    returns a full moves plan in ~6-12 s, never runs away).
  * ``reasoning`` is a SEPARATE, explicitly-budgeted thinking channel for
    thinking-capable models (sonnet-4-6 / opus). ``reasoning.max_tokens`` /
    ``reasoning.effort`` bound the think; the answer budget is untouched. haiku
    has no native thinking here (``reasoning_tokens`` = 0) but doesn't need it —
    it simply answers within the cap.
  * ``response_format={"type":"json_schema", ...}`` hard-guarantees the output
    conforms to a schema, so a valid ``moves`` object is guaranteed at the API
    level (no more "prose ate the budget, no moves emitted").

This client is deliberately non-streaming: the calls are short and bounded, so
a single POST with ``timeout=wallclock_cap_s`` is simpler and more robust than
SSE parsing. The return envelope mirrors
:class:`~sea_of_colours.agent.cortex_invoker.CortexAgentInvoker` so callers can
swap between the two paths with no downstream changes.

Credentials: same lookup as the Agents client — ``SNOWFLAKE_PAT`` env, else
the PAT on the resolved Snowflake connection (standard ``connections.toml``
/ ``config.toml``, legacy sf_config last). See ``snowpark.sfconn``.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from sea_of_colours.agent.cortex_invoker import _load_sf_props, SNOWFLAKE_PAT_ENV


class CortexChatInvoker:
    """Stream-free client for the Cortex inference chat/completions API."""

    DEFAULT_MODEL = "claude-haiku-4-5"
    # Answer-token ceiling. This is the HARD budget the model self-limits to.
    # ~2000 tokens comfortably fits a 21-move plan + terse prose.
    DEFAULT_MAX_COMPLETION_TOKENS = 2000

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        response_format: Optional[Dict[str, Any]] = None,
        reasoning: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        max_completion_tokens: Optional[int] = None,
        config_file: Optional[str] = None,
        pat_token: Optional[str] = None,
    ) -> None:
        self.model = model
        self.response_format = response_format
        # ``reasoning`` (e.g. {"max_tokens": 1500} or {"effort": "low"}) enables
        # the separate bounded thinking channel on thinking-capable models.
        # Cortex requires temperature=1 whenever reasoning is enabled.
        self.reasoning = reasoning
        self.temperature = 1.0 if (reasoning and temperature is None) else temperature
        self.max_completion_tokens = (
            max_completion_tokens or self.DEFAULT_MAX_COMPLETION_TOKENS
        )
        # v1.45 — None means "resolve normally"; see snowpark.sfconn.
        self.config_file = config_file
        if config_file:
            props = _load_sf_props(config_file)
            self.account = props.get("account", "")
            self.pat_token = pat_token or os.environ.get(SNOWFLAKE_PAT_ENV, "") or (
                props.get("pat", "") or props.get("access_token", "")
            )
            self.cred_source = config_file
        else:
            from sea_of_colours.snowpark import sfconn

            account, token, where = sfconn.resolve_cortex()
            self.account = account
            self.pat_token = pat_token or token
            self.cred_source = where
        account_clean = (self.account or "").lower().replace("_", "-")
        self.api_base = (
            f"https://{account_clean}.snowflakecomputing.com" if account_clean else ""
        )
        self.endpoint = f"{self.api_base}/api/v2/cortex/v1/chat/completions"

    def is_ready(self) -> bool:
        return bool(self.pat_token and self.account)

    def invoke(
        self,
        prompt: str,
        *,
        wallclock_cap_s: float = 60.0,
        response_cap_bytes: Optional[int] = None,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        """Run one chat/completion. Returns an envelope compatible with the
        Agents invoker: ``ok``, ``response``, ``thinking``, ``wallclock_capped``,
        ``capped``, ``elapsed_ms``.

        ``response_cap_bytes`` (if given) softly informs the token ceiling when
        one wasn't set explicitly at construction (bytes/4 ≈ tokens), keeping
        call sites that pass a byte cap working.
        """
        try:
            import requests
        except Exception:  # pragma: no cover
            return {"ok": False, "error": "`requests` not installed."}

        if not self.is_ready():
            return {"ok": False, "error": "missing PAT or account for Cortex inference."}

        max_tokens = self.max_completion_tokens
        if response_cap_bytes and not self.max_completion_tokens:
            max_tokens = max(512, int(response_cap_bytes) // 4)

        headers = {
            "Authorization": f"Bearer {self.pat_token}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": max_tokens,
            "stream": False,
        }
        if self.response_format is not None:
            payload["response_format"] = self.response_format
        if self.reasoning is not None:
            payload["reasoning"] = self.reasoning
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        started = time.time()
        try:
            resp = requests.post(
                self.endpoint, headers=headers, json=payload, timeout=wallclock_cap_s,
            )
        except Exception as exc:
            # Timeout (wallclock) or transport error. Surface as a wallclock cap
            # so the caller's finisher/heuristic safety net engages.
            elapsed_ms = int((time.time() - started) * 1000)
            is_timeout = exc.__class__.__name__.lower().find("timeout") >= 0
            return {
                "ok": False,
                "error": f"transport error: {exc}",
                "response": "",
                "thinking": "",
                "wallclock_capped": is_timeout,
                "capped": False,
                "elapsed_ms": elapsed_ms,
            }

        elapsed_ms = int((time.time() - started) * 1000)
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
                "status_code": resp.status_code,
                "response": "",
                "thinking": "",
                "wallclock_capped": False,
                "capped": False,
                "elapsed_ms": elapsed_ms,
            }

        try:
            body = resp.json()
            choice = (body.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            reasoning = (
                message.get("reasoning_content")
                or message.get("reasoning")
                or ""
            )
            finish_reason = choice.get("finish_reason") or ""
        except Exception as exc:  # pragma: no cover - malformed body
            return {
                "ok": False,
                "error": f"bad response body: {exc}",
                "response": "",
                "thinking": "",
                "wallclock_capped": False,
                "capped": False,
                "elapsed_ms": elapsed_ms,
            }

        return {
            "ok": True,
            "response": content.strip() if isinstance(content, str) else "",
            # Bounded reasoning trace (audit-only; empty for haiku).
            "thinking": str(reasoning).strip(),
            # The answer hit the token ceiling — a truncation signal.
            "capped": finish_reason == "length",
            "wallclock_capped": False,
            "finish_reason": finish_reason,
            "usage": body.get("usage"),
            "elapsed_ms": elapsed_ms,
        }


def credentials_status() -> tuple[bool, str]:
    """Can an LLM seat actually reach Cortex? If not, say what's missing.

    Exists because the failure is otherwise **invisible**. A seat bound
    to an LLM harness with no credentials doesn't raise: the harness's
    per-turn fallback absorbs the miss and the agent passes the night
    with zero moves, reporting ``ok=True`` and ``error=None``. To a
    player that reads as "the AI is broken", not "I never set a PAT".

    The per-turn fallback is right for a call that fails *mid-game*; it
    is the wrong answer for a seat that could never have worked. So
    callers use this as a **creation-time preflight** and refuse the
    game up front, leaving the in-game fallback untouched.

    Returns ``(ready, reason)`` — ``reason`` is empty when ready.
    """
    probe = CortexChatInvoker()
    if probe.is_ready():
        return True, ""
    # v1.45 — name the connection we actually looked at. Telling someone
    # to "add a token to the config file" is useless advice when they
    # have five connections and we will not say which one we read.
    where = getattr(probe, "cred_source", "") or str(probe.config_file)
    missing = []
    if not probe.pat_token:
        missing.append(
            f"a PAT (set the {SNOWFLAKE_PAT_ENV} env var, or add "
            f"token = \"<pat>\" to your Snowflake connection — "
            f"resolved: {where})"
        )
    if not probe.account:
        missing.append(
            f"a Snowflake account (add account = \"<account>\" to your "
            f"Snowflake connection — resolved: {where})"
        )
    return False, " and ".join(missing)
