"""Unit tests for the v7 CONTAINED THINKER (inference-API reasoning pass).

The contained thinker runs on the Cortex inference API with a reasoning-FIRST
json-schema and a hard token cap (the containment fix, applied to the thinker
instead of only the mover). These tests lock in the deterministic plumbing that
makes that safe:

  * the DECISION json-schema is reasoning-first and Cortex-strict-mode legal;
  * ``parse_directive_json`` recovers BOTH the decision and the captured
    chain-of-thought from the structured output;
  * the harness selects the contained (chat) thinker by default and surfaces the
    reasoning as a persistable ``sub_invocations`` audit row;
  * the audit writer persists each sub-invocation as its own row.

No network: the inference call is faked. The live thinker is scanned separately
by scripts/scan_thinker.py.
"""

from __future__ import annotations

import os

from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import (
    chat_schema,
    directive as dm,
    harness,
)


# ── DECISION schema shape ──────────────────────────────────────────────
def test_decision_schema_is_decision_first_and_strict_legal():
    schema = chat_schema.DECISION_RESPONSE_FORMAT["json_schema"]["schema"]
    props = list(schema["properties"].keys())
    # CONTAINMENT: the actionable decision MUST lead so it is emitted before the
    # model can ramble; ``reasoning`` is LAST so a clipped tail can never cost us
    # the decision. (Was reasoning-first — that let haiku ramble past the token
    # cap and drop the whole plan on dense redsign nights.)
    assert props[0] == "posture"
    assert props[-1] == "reasoning"
    # ``plan`` (what the mover executes) must precede ``reasoning``.
    assert props.index("plan") < props.index("reasoning")
    # Only a posture is forced now — a decisive call with no prose still parses.
    assert set(schema["required"]) == {"posture"}
    # Cortex strict-mode legality: additionalProperties false, no type-arrays.
    assert schema["additionalProperties"] is False
    for name, spec in schema["properties"].items():
        assert isinstance(spec.get("type"), str), name


def test_decision_posture_enum_matches_valid_postures():
    schema = chat_schema.DECISION_RESPONSE_FORMAT["json_schema"]["schema"]
    enum = schema["properties"]["posture"]["enum"]
    assert set(enum) == set(dm.VALID_POSTURES)


# ── parse_directive_json ───────────────────────────────────────────────
def test_parse_json_recovers_reasoning_and_decision():
    content = (
        '{"reasoning":"Beacon is a public pure. Enemy probe discovered it and '
        'they hold chaff, so this is contested. Supersede their probe, commit '
        'H1+H2, exfil by H13.","posture":"redsign_race","targets":[[9,9],[10,9]],'
        '"chaff_react":true,"avoid":[[3,3]],"note":"contest: supersede then comb"}'
    )
    directive, reasoning = dm.parse_directive_json(content)
    assert directive is not None
    assert directive.posture == "redsign_race"
    assert directive.targets == [(9, 9), (10, 9)]
    assert directive.chaff_react is True
    assert directive.avoid == [(3, 3)]
    assert "supersede" in reasoning.lower()


def test_parse_json_tolerates_prose_wrapper():
    content = 'Here is my decision:\n{"reasoning":"short","posture":"aggressive"}'
    directive, reasoning = dm.parse_directive_json(content)
    assert directive is not None
    assert directive.posture == "aggressive"
    assert reasoning == "short"


def test_parse_json_none_on_garbage():
    directive, reasoning = dm.parse_directive_json("not json at all")
    assert directive is None
    assert reasoning == ""


def test_parse_json_empty_on_empty():
    assert dm.parse_directive_json("") == (None, "")


def test_parse_json_salvages_reasoning_from_truncated_object():
    # reasoning-first CoT that ran past the token cap: object never closed, so
    # it won't parse — but we must still recover the chain-of-thought.
    truncated = (
        '{"reasoning":"The beacon is contested. I should supersede their probe '
        'then commit H1+H2 and exfil by hour 13 before they can EMP the'
    )
    directive, reasoning = dm.parse_directive_json(truncated)
    assert directive is None  # no usable directive from a truncated object
    assert "supersede" in reasoning.lower()
    assert "beacon" in reasoning.lower()


def test_parse_json_salvages_reasoning_when_only_first_field_closed():
    truncated = (
        '{"reasoning":"short but complete thought","posture":"redsi'
    )
    directive, reasoning = dm.parse_directive_json(truncated)
    assert directive is None
    assert reasoning == "short but complete thought"


def test_parse_json_salvages_DECISION_from_truncated_decision_first_object():
    # The real failure mode now: decision fields lead, the trailing ``reasoning``
    # string runs past the token cap so the object never closes. The DECISION
    # must still survive — this is the guard that stops a clipped thinker from
    # silently dropping the plan and letting the mover freelance.
    truncated = (
        '{"posture":"redsign_race","plan":["SMASH_GRAB","PR2"],'
        '"situational":{"mine":true,"players":3,"chaff":false,"emp":false},'
        '"targets":[[31,18],[32,17]],"note":"grab the pure",'
        '"reasoning":"The pure at (31,18) is worth 2295 so I drop directly on'
    )
    directive, reasoning = dm.parse_directive_json(truncated)
    assert directive is not None
    assert directive.posture == "redsign_race"
    assert directive.plan == ["SMASH_GRAB", "PR2"]
    assert directive.targets == [(31, 18), (32, 17)]
    assert directive.situational.get("mine") is True
    assert directive.situational.get("players") == 3
    assert directive.note == "grab the pure"


def test_parse_json_salvages_decision_when_plan_array_truncated():
    # Clipped even earlier — inside the ``plan`` array. Posture + whatever IDs
    # already landed are still recovered.
    truncated = '{"posture":"aggressive","plan":["BLIND_GRAB","UNBEATEN_FLANK"'
    directive, reasoning = dm.parse_directive_json(truncated)
    assert directive is not None
    assert directive.posture == "aggressive"
    assert directive.plan == ["BLIND_GRAB", "UNBEATEN_FLANK"]


# ── harness thinker-api selector ───────────────────────────────────────
def test_thinker_api_defaults_to_chat(monkeypatch):
    monkeypatch.delenv("TABULA_V7_THINKER_API", raising=False)
    assert harness._thinker_api() == "chat"


def test_thinker_api_override_to_agents(monkeypatch):
    monkeypatch.setenv("TABULA_V7_THINKER_API", "agents")
    assert harness._thinker_api() == "agents"


# ── audit multi-row persistence ────────────────────────────────────────
def test_audit_writes_sub_invocation_row_for_thinker():
    from sea_of_colours.snowpark.store import InMemorySocStore
    from sea_of_colours.orchestrator_2 import audit
    from sea_of_colours.orchestrator_2.binding_registry import AgentBinding
    from sea_of_colours.orchestrator_2.dispatcher import DispatchResult

    store = InMemorySocStore()
    binding = AgentBinding(
        kind="harness_in_process", locator="x:run", agent_label="TABULA_V7",
    )
    result = DispatchResult(
        ok=True,
        elapsed_ms=1200,
        submitted_policy=True,
        rationale="[mover] moves=7",
        response="{...mover json...}",
        extras={
            "sub_invocations": [
                {
                    "label": "SOC_RED_REAPER_TABULA_V7_THINKER",
                    "kind": "thinker",
                    "response_text": "the beacon is contested; supersede then comb",
                    "rationale": "[posture=redsign_race] targets=[(9,9)]",
                    "ms_elapsed": 800,
                    "prompt_excerpt": "THINKER PROMPT ...",
                },
            ],
        },
    )

    audit.write_invocation(
        store=store, session_id="S1", player="p1", day=4,
        binding=binding, result=result,
    )

    rows = store.list_agent_invocations("S1", day=4)
    labels = [r.get("agent_id") for r in rows]
    # Both the thinker sub-row and the primary mover row are persisted...
    assert "SOC_RED_REAPER_TABULA_V7_THINKER" in labels
    assert "TABULA_V7" in labels
    # ...and the thinker row is ordered BEFORE the mover row (lower seq).
    thinker_idx = labels.index("SOC_RED_REAPER_TABULA_V7_THINKER")
    mover_idx = labels.index("TABULA_V7")
    assert thinker_idx < mover_idx
    # The thinker's captured reasoning is the row's response_text.
    thinker_row = rows[thinker_idx]
    assert "supersede" in str(thinker_row.get("response_text"))


def test_audit_no_sub_rows_when_absent():
    from sea_of_colours.snowpark.store import InMemorySocStore
    from sea_of_colours.orchestrator_2 import audit
    from sea_of_colours.orchestrator_2.binding_registry import AgentBinding
    from sea_of_colours.orchestrator_2.dispatcher import DispatchResult

    store = InMemorySocStore()
    binding = AgentBinding(kind="cortex_agent", locator="A", agent_label="A")
    result = DispatchResult(
        ok=True, elapsed_ms=10, submitted_policy=True, rationale="r",
        response="x", extras={},
    )
    audit.write_invocation(
        store=store, session_id="S2", player="p1", day=1,
        binding=binding, result=result,
    )
    rows = store.list_agent_invocations("S2", day=1)
    assert len(rows) == 1
    assert rows[0].get("agent_id") == "A"
