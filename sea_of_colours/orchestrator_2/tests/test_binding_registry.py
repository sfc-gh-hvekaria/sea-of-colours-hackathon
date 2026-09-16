"""Tests for the binding registry — the fairness gate.

Pins:
1. The default with nothing set is heuristic.
2. SOC_BINDING_<player> env vars are honoured.
3. KNOWN_AGENT_BINDINGS maps SOC_CORTEX_AGENT correctly for V12.
4. Unknown SOC_CORTEX_AGENT names with runtime_override="cortex"
   produce a bare cortex_agent binding.
5. Heuristic override short-circuits everything.
"""

from __future__ import annotations

import os

import pytest

from sea_of_colours.orchestrator_2 import binding_registry as br


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "SOC_BINDING_P1", "SOC_BINDING_P2",
        "SOC_CORTEX_AGENT", "SOC_AGENT_RUNTIME",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def test_default_resolves_to_heuristic():
    b = br.resolve_binding(store=None, session_id="s", player="p1")
    assert b is br.HEURISTIC_BINDING
    assert b.kind == "heuristic"


def test_env_per_player_override_parses(monkeypatch):
    """v1.12 — a harness locator survives the env round-trip.

    The old parser split on colons, so ``my_mod.path:run`` came back as
    locator ``my_mod.path`` with the label ``run``: the import failed and
    the seat silently fell back to the heuristic. The label separator is
    now ``#``, leaving the locator intact.
    """
    monkeypatch.setenv(
        "SOC_BINDING_P1",
        "harness_in_process:my_mod.path:run#MY_LABEL",
    )
    b = br.resolve_binding(store=None, session_id="s", player="p1")
    assert b.kind == "harness_in_process"
    assert b.locator == "my_mod.path:run"
    assert b.agent_label == "MY_LABEL"


def test_env_per_player_override_without_label(monkeypatch):
    monkeypatch.setenv("SOC_BINDING_P2", "harness_in_process:my_mod.path:run")
    b = br.resolve_binding(store=None, session_id="s", player="p2")
    assert b.locator == "my_mod.path:run"
    assert b.agent_label is None


def test_known_agent_maps_v12(monkeypatch):
    monkeypatch.setenv("SOC_CORTEX_AGENT", "SOC_RED_REAPER_TABULA_V12")
    b = br.resolve_binding(store=None, session_id="s", player="p1")
    assert b.kind == "harness_in_process"
    assert b.locator.endswith("tabula_v12.harness:run")
    assert b.agent_label == "TABULA_V12"


def test_retired_harness_labels_no_longer_resolve(monkeypatch):
    """The R&D lineage (the pilot_* and tabula_v2..v11 agents) was
    deleted. Their labels must not silently resolve to anything."""
    for name in ("SOC_RED_REAPER_PILOT_V2", "SOC_RED_REAPER_TABULA_V9"):
        monkeypatch.setenv("SOC_CORTEX_AGENT", name)
        b = br.resolve_binding(store=None, session_id="s", player="p1")
        assert b.kind == "heuristic", f"{name} should not resolve to a harness"


def test_shipped_roster_is_exactly_the_documented_four():
    """The roster is the New Game dropdown (served at /api/meta/agents),
    so an accidental addition ships a selectable agent to players.

    Only the *shipped* four are pinned. Forks join the dropdown too —
    that is the whole design, and this used to fail for everyone who
    minted one, on a day when minting one is step two of the guide."""
    assert set(br.SHIPPED_AGENT_LABELS) == {
        "human", "red_harvest", "red_harvest_lite", "tabula_v12",
    }


def test_anything_beyond_the_shipped_four_arrived_via_a_manifest():
    """The other half of the guarantee above.

    Loosening the roster test is only safe if the extras are accounted
    for. A label that is neither shipped nor declared by an
    ``agent.json`` got in by someone editing this registry, which is
    exactly what forks are told not to do."""
    from sea_of_colours.orchestrator_2 import agent_manifest

    manifests, _ = agent_manifest.discover()
    accounted = set(br.SHIPPED_AGENT_LABELS) | {m.label for m in manifests}
    assert set(br.AGENT_LABEL_BINDINGS) <= accounted, (
        "an agent is in the roster that neither shipped nor declared "
        "itself — register a fork with an agent.json, don't edit "
        "binding_registry.py"
    )


def test_human_is_listed_but_never_dispatched():
    """``human`` is in the roster so the menu can be built from one list,
    but resolving it must not hand the dispatcher a kind it cannot route."""
    b = br.resolve_binding(store=None, session_id="s", player="p1",
                           agent_label="human")
    assert b.kind == "heuristic"


def test_selectable_agents_matches_the_registry():
    roster = br.selectable_agents()
    assert [a["value"] for a in roster] == [
        k for k, v in br.AGENT_LABEL_BINDINGS.items() if v.menu_label
    ]
    assert all(a["label"] for a in roster), "every listed agent needs a menu label"
    # No heuristic may demand a PAT. Checked over the shipped agents only:
    # a fork of V12 needs one too, and that is correct, not a regression.
    shipped = [a for a in roster if a["value"] in br.SHIPPED_AGENT_LABELS]
    assert {a["value"] for a in shipped if a["needs_llm"]} == {"tabula_v12"}


def test_unknown_cortex_agent_with_override_becomes_bare(monkeypatch):
    monkeypatch.setenv("SOC_CORTEX_AGENT", "SOC_HYPOTHETICAL_FUTURE_AGENT")
    b = br.resolve_binding(
        store=None, session_id="s", player="p1",
        runtime_override="cortex",
    )
    assert b.kind == "cortex_agent"
    assert b.locator == "SOC_HYPOTHETICAL_FUTURE_AGENT"


def test_heuristic_override_short_circuits(monkeypatch):
    """Even with a known agent registered, runtime_override='heuristic' wins."""
    monkeypatch.setenv("SOC_CORTEX_AGENT", "SOC_RED_REAPER_PILOT_V2")
    b = br.resolve_binding(
        store=None, session_id="s", player="p1",
        runtime_override="heuristic",
    )
    assert b is br.HEURISTIC_BINDING


def test_legacy_agents_not_claimed_by_orchestrator_2(monkeypatch):
    """PILOT v1 / GRID_FAST / etc. should NOT be claimed — they live in v1."""
    for agent_name in (
        "SOC_RED_REAPER", "SOC_RED_REAPER_PILOT",
        "SOC_RED_REAPER_GRID_FAST", "SOC_RED_REAPER_LIST_V2",
    ):
        monkeypatch.setenv("SOC_CORTEX_AGENT", agent_name)
        b = br.resolve_binding(store=None, session_id="s", player="p1")
        # Falls through to heuristic — orchestrator_2 doesn't own these.
        assert b.kind == "heuristic", f"{agent_name} unexpectedly claimed by orchestrator_2"
