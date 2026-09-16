"""Snowpark Python stored-procedure handlers.

Each function in this module is the HANDLER for one ``SOC_*`` stored
procedure declared in ``snowflake/soc_procedures.sql``. The signatures
mirror the SF wrappers (``session, *args -> Variant``); all real work
delegates to :mod:`sea_of_colours.snowpark.engine`, which is
storage-agnostic and fully unit-testable.

The package is uploaded to ``@SOC_PY_STAGE`` by
``scripts/deploy_soc_schema.py`` so the IMPORTS clause on every
procedure resolves to a working zip.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from sea_of_colours.snowpark import engine
from sea_of_colours.snowpark.snowpark_store import SnowparkSocStore


def _store_for(session: Any) -> SnowparkSocStore:
    return SnowparkSocStore(session)


def _null(value: Any) -> Any:
    """Normalise Snowflake's NULL-wrapper sentinels to ``None``.

    A VARIANT NULL handed to a Snowpark Python proc does NOT arrive as
    Python's ``None`` — it shows up as a ``sqlNullWrapper`` instance
    that is *truthy* and rejects most operations (``list(x)``,
    ``int(x)``, etc.). Without this coercion, callers like
    ``engine.save_agent_rationale`` see ``tool_calls=<sqlNullWrapper>``
    and explode with ``TypeError: 'sqlNullWrapper' object is not
    iterable``. We probe by class name so the helper works regardless
    of which exact wrapper type Snowflake ships in this runtime.
    """
    if value is None:
        return None
    cls = type(value).__name__
    if cls in ("sqlNullWrapper", "SqlNullWrapper", "NullValue"):
        return None
    return value


def soc_init_session(
    session, p_seed: int, p_width: int = 40, p_height: int = 28
) -> dict:
    return engine.init_session(
        _store_for(session), int(p_seed), int(p_width), int(p_height),
    )


def soc_submit_policy(
    session, p_session_id: str, p_player: str, p_policy: Any
) -> dict:
    """Submit one seat's move queue.

    ``p_policy`` is declared as STRING (not VARIANT) in the proc DDL —
    Cortex tool calls cannot pass OBJECT-typed parameters in warehouse
    mode. We accept any of:

    * a JSON string of the canonical envelope: ``'{"moves":[...]}'``
    * a JSON string of a bare moves array: ``'[{"a":"probe",...}]'``
    * (legacy / in-process callers) the already-parsed dict / list

    All three normalise to a ``list[dict]`` that
    :func:`engine.submit_policy` knows how to validate.
    """
    import json as _json

    policy = _null(p_policy)
    if policy is None:
        moves: Any = []
    elif isinstance(policy, str):
        # Cortex hands us a JSON string here.
        s = policy.strip()
        if not s:
            moves = []
        else:
            try:
                policy = _json.loads(s)
            except _json.JSONDecodeError:
                # Treat malformed JSON as an empty submission — the
                # engine will record it as such and the agent will see
                # a 0-move WasteMove rather than crashing the proc.
                policy = []
            moves = policy
    else:
        moves = policy

    if isinstance(moves, Mapping) and "moves" in moves:
        moves = moves.get("moves") or []
    if not isinstance(moves, list):
        moves = []

    return engine.submit_policy(
        _store_for(session), str(p_session_id), str(p_player), moves,
    )


def soc_run_night(session, p_session_id: str) -> dict:
    return engine.run_night(_store_for(session), str(p_session_id))


def soc_submit_orbit_actions(
    session, p_session_id: str, p_player: str, p_actions: Any,
) -> dict:
    """Submit one seat's Orbit action queue (v0.8.0).

    ``p_actions`` mirrors the ``p_policy`` contract on
    :func:`soc_submit_policy`: STRING in the proc DDL, accepts either
    a JSON envelope ``{"actions":[...]}``, a bare array, or (for
    in-process callers) a pre-parsed Python list / dict.
    """
    import json as _json

    actions = _null(p_actions)
    if actions is None:
        out: Any = []
    elif isinstance(actions, str):
        s = actions.strip()
        if not s:
            out = []
        else:
            try:
                actions = _json.loads(s)
            except _json.JSONDecodeError:
                actions = []
            out = actions
    else:
        out = actions

    if isinstance(out, Mapping) and "actions" in out:
        out = out.get("actions") or []
    if not isinstance(out, list):
        out = []

    return engine.submit_orbit_actions(
        _store_for(session), str(p_session_id), str(p_player), out,
    )


def soc_get_view(session, p_session_id: str, p_player: str) -> dict:
    return engine.get_view(
        _store_for(session), str(p_session_id), str(p_player),
    )


def soc_get_observer(session, p_session_id: str) -> dict:
    return engine.get_observer(_store_for(session), str(p_session_id))


def soc_get_replay(
    session,
    p_session_id: str,
    p_day_from: Optional[int] = None,
    p_day_to: Optional[int] = None,
) -> dict:
    df = _null(p_day_from)
    dt = _null(p_day_to)
    return engine.get_replay(
        _store_for(session),
        str(p_session_id),
        int(df) if df is not None else None,
        int(dt) if dt is not None else None,
    )


def soc_get_log(
    session,
    p_session_id: str,
    p_day_from: Optional[int] = None,
    p_day_to: Optional[int] = None,
    p_limit: Optional[int] = None,
) -> dict:
    df = _null(p_day_from)
    dt = _null(p_day_to)
    lim = _null(p_limit)
    return engine.get_log(
        _store_for(session),
        str(p_session_id),
        int(df) if df is not None else None,
        int(dt) if dt is not None else None,
        int(lim) if lim is not None else None,
    )


def soc_get_inventory(
    session, p_session_id: str, p_player: str
) -> dict:
    return engine.get_inventory(
        _store_for(session), str(p_session_id), str(p_player),
    )


def soc_get_session_status(session, p_session_id: str) -> dict:
    return engine.get_session_status(
        _store_for(session), str(p_session_id),
    )


def soc_list_sessions(session) -> dict:
    return engine.list_sessions(_store_for(session))


def soc_save_rationale(
    session,
    p_session_id: str,
    p_day: int,
    p_agent_id: str,
    p_player: str,
    p_rationale: str,
    p_prompt_excerpt: Optional[str] = None,
    p_tool_calls: Optional[list] = None,
    p_response_text: Optional[str] = None,
    p_ms_elapsed: Optional[int] = None,
) -> dict:
    """Audit-trail handler for the Cortex agent's ``soc_save_rationale`` tool.

    All four optional params can arrive as Snowflake's NULL wrapper
    instead of Python's ``None``; we route every one of them through
    :func:`_null` so ``engine.save_agent_rationale`` only ever sees
    real Python values or ``None``.
    """
    prompt = _null(p_prompt_excerpt)
    tools = _null(p_tool_calls)
    if tools is not None and not isinstance(tools, list):
        tools = list(tools) if hasattr(tools, "__iter__") else [tools]
    resp = _null(p_response_text)
    elapsed = _null(p_ms_elapsed)
    return engine.save_agent_rationale(
        _store_for(session),
        str(p_session_id),
        int(p_day),
        str(p_agent_id),
        str(p_player),
        str(p_rationale),
        prompt_excerpt=str(prompt) if prompt is not None else None,
        tool_calls=tools,
        response_text=str(resp) if resp is not None else None,
        ms_elapsed=int(elapsed) if elapsed is not None else None,
    )
