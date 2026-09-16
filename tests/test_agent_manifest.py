"""Agent discovery — the contract the hackathon's submission model rests on.

v1.39. A fork is one directory with an ``agent.json`` in it. Everything
downstream depends on that being true and on it staying true: the New
Game roster, the scenario suite, ``soc push``'s "your folder only" check,
and the end-of-day league collation all enumerate agents this way.

The tests that matter most here are the negative ones. Discovery runs at
import, on every server start and every test collection, in a room where
forty people are editing manifests through a coding agent. If one bad
file could raise, one team's trailing comma would stop everybody's
server — so "a broken manifest is reported and skipped" is a hard
requirement, not a nicety.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sea_of_colours.orchestrator_2 import agent_manifest


def _write(root: Path, dirname: str, payload) -> Path:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    p = d / agent_manifest.MANIFEST_NAME
    p.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    return p


def _ok(team="redwatch", name="reaper", **over):
    payload = {
        "team": team,
        "name": name,
        # Required since v1.41 — see test_agent_participants.py for the
        # rules. Present in every fixture here so these tests keep
        # testing what they are about rather than the roster.
        "participants": ["Ada Lovelace"],
        "menu_label": "REDWATCH_REAPER — redwatch's agent",
        "entry": "harness:run",
        "needs_llm": True,
    }
    payload.update(over)
    return payload


# ── the happy path ──────────────────────────────────────────────────


def test_a_manifest_names_the_agent_and_its_import_path(tmp_path):
    _write(tmp_path, "redwatch_reaper", _ok())
    found, problems = agent_manifest.discover(tmp_path)

    assert problems == []
    assert len(found) == 1
    man = found[0]
    assert man.label == "redwatch_reaper"
    assert man.const == "SOC_REDWATCH_REAPER"
    assert man.locator == (
        "sea_of_colours.orchestrator_2.harnesses.redwatch_reaper.harness:run"
    )


def test_entry_defaults_so_the_common_manifest_is_who_you_are(tmp_path):
    """The only fields anyone must write are identity: team, name, people."""
    _write(tmp_path, "a_b",
           {"team": "a", "name": "b", "participants": ["Ada"]})
    found, problems = agent_manifest.discover(tmp_path)
    assert problems == []
    assert found[0].locator.endswith(".a_b.harness:run")


def test_discovery_order_is_stable(tmp_path):
    """The menu and the league table must not reshuffle between runs."""
    for d in ("z_z", "a_a", "m_m"):
        _write(tmp_path, d, _ok(team=d.split("_")[0], name=d.split("_")[1]))
    labels = [m.label for m in agent_manifest.discover(tmp_path)[0]]
    assert labels == ["a_a", "m_m", "z_z"]


# ── the negative cases, which are the point ─────────────────────────


def test_a_broken_manifest_is_reported_not_raised(tmp_path):
    """One team's typo must not stop the room's server from booting."""
    _write(tmp_path, "good_one", _ok(team="good", name="one"))
    _write(tmp_path, "bad_one", "{'team': 'bad',}")  # not JSON

    found, problems = agent_manifest.discover(tmp_path)

    assert [m.label for m in found] == ["good_one"]
    assert len(problems) == 1
    # The reader is often an agent with no other context, so the message
    # has to carry the path and the likely fix.
    assert "bad_one" in problems[0]
    assert "JSON" in problems[0]


def test_a_manifest_must_match_its_directory(tmp_path):
    """Otherwise it registers a binding pointing at a package that isn't there.

    Caught at discovery rather than at dispatch, where it would show up
    as a seat quietly falling back to the heuristic in the middle of a
    game — one of the harder failures to trace back to its cause.
    """
    _write(tmp_path, "wrong_folder", _ok(team="redwatch", name="reaper"))
    found, problems = agent_manifest.discover(tmp_path)

    assert found == []
    assert "redwatch_reaper" in problems[0]
    assert "wrong_folder" in problems[0]


@pytest.mark.parametrize(
    "bad", ["Redwatch", "red-watch", "9team", "team_", "", "red watch"]
)
def test_identifiers_are_restricted_because_they_become_packages(tmp_path, bad):
    _write(tmp_path, "x_y", {"team": bad, "name": "y"})
    found, problems = agent_manifest.discover(tmp_path)
    assert found == []
    assert problems


def test_two_agents_cannot_share_a_label(tmp_path):
    """Two teams with the same label would silently overwrite on the table."""
    _write(tmp_path, "redwatch_reaper", _ok())
    dup = tmp_path / "redwatch_reaper_copy"
    dup.mkdir()
    # A second directory whose manifest claims the first one's identity.
    (dup / agent_manifest.MANIFEST_NAME).write_text(
        json.dumps(_ok()), encoding="utf-8"
    )
    found, problems = agent_manifest.discover(tmp_path)
    assert len(found) == 1
    assert any("duplicate" in p or "must be named" in p for p in problems)


def test_directories_without_a_manifest_are_ignored(tmp_path):
    """`_v7` and `__pycache__` live under harnesses/ and are not agents."""
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "_v7").mkdir()
    (tmp_path / "notanagent").mkdir()
    found, problems = agent_manifest.discover(tmp_path)
    assert found == []
    assert problems == []


# ── the registry wiring ─────────────────────────────────────────────


def test_the_shipped_registry_comes_up_clean():
    """No stray manifest in the tree, and the built-ins are all present."""
    from sea_of_colours.orchestrator_2 import binding_registry as br

    assert br.DISCOVERY_PROBLEMS == []
    for builtin in ("human", "red_harvest", "red_harvest_lite", "tabula_v12"):
        assert builtin in br.AGENT_LABEL_BINDINGS


def test_a_fork_cannot_shadow_the_baseline(tmp_path, monkeypatch):
    """Take the ``tabula_v12`` label and you become the control group.

    The whole scoring model is "your fork versus stock V12", so a fork
    that could register itself as V12 would quietly make every
    comparison in the room meaningless.
    """
    from sea_of_colours.orchestrator_2 import binding_registry as br

    _write(tmp_path, "tabula_v12",
           {"team": "tabula", "name": "v12", "participants": ["Ada"]})
    monkeypatch.setattr(agent_manifest, "harness_root", lambda: tmp_path)

    before = br.AGENT_LABEL_BINDINGS["tabula_v12"]
    problems = br._register_discovered()

    assert br.AGENT_LABEL_BINDINGS["tabula_v12"] is before
    assert any("built-in" in p for p in problems)
