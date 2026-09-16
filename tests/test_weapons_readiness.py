"""The weapons readiness probe (v1.40).

``soc weapons`` tells an attendee which of the four firing rungs their
agent is stuck on. Two ways it could be useless:

* it only ever says FAIL, so it never confirms progress; or
* it says PASS on prose that merely mentions a weapon, so it confirms
  progress that has not happened.

The second is the dangerous one. The first version of rung 4 passed the
shipped V12 doctrine because it matched "deny their" — which is in there
about landing a spare probe on a rival's probe, nothing to do with
ordnance. Both directions are pinned below.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("SOC_BACKEND", "memory")

from sea_of_colours.evals.battles import readiness  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_V12 = _REPO / "sea_of_colours/orchestrator_2/harnesses/tabula_v12"


def _fork(tmp_path: Path, **files: str) -> Path:
    """A throwaway fork directory with just the files a rung reads."""
    root = tmp_path / "myteam_myagent"
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        dest = root / name.replace("__", "/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
    return root


def _by_n(rungs):
    return {r.n: r for r in rungs}


# ── the shipped baseline ──────────────────────────────────────────


def test_v12_fails_every_rung():
    """The baseline cannot fire. If this ever passes, either someone
    taught V12 to fight or the probe has gone blind."""
    rungs = readiness.check(_V12)
    assert len(rungs) == 4
    for r in rungs:
        assert not r.passed, f"rung {r.n} ({r.name}) unexpectedly passed"
    assert readiness.first_gap(rungs).n == 1


def test_the_baseline_report_names_the_next_action():
    out = readiness.render("tabula_v12", readiness.check(_V12))
    assert "START AT RUNG 1" in out
    assert "world_view.py" in out


# ── rung 1: knows its own rack ────────────────────────────────────


def test_buying_code_alone_does_not_count(tmp_path):
    """orbit_policy.py reads weapon_stock in every fork by construction —
    counting it would pass every agent on day zero."""
    root = _fork(
        tmp_path,
        **{"orbit_policy.py": "weapon_stock = view['orbit']['weapon_stock']"},
    )
    assert not _by_n(readiness.check(root))[1].passed


def test_printing_the_rack_on_a_diagnostic_does_not_count(tmp_path):
    """card.py renders the turn for a human, after the decision.

    v1.38 gave the card an arsenal line so a lab card says who was armed
    — which is a reporting change and must not move this rung. A fork
    handed a green rung 1 by a debug render would be sent up the ladder
    with the bottom rung missing, which is the exact misdiagnosis this
    module exists to prevent.
    """
    root = _fork(
        tmp_path,
        **{"card.py": "stock = agent_view['orbit']['weapon_stock']"},
    )
    assert not _by_n(readiness.check(root))[1].passed


def test_the_night_view_carrying_the_rack_passes(tmp_path):
    root = _fork(
        tmp_path,
        **{
            "orbit_policy.py": "weapon_stock = {}",
            "world_view.py": "out['weapon_stock'] = seat_stock(sess, seat)",
        },
    )
    rung = _by_n(readiness.check(root))[1]
    assert rung.passed
    assert "world_view.py" in rung.detail


# ── rung 2: offerable and compilable ──────────────────────────────


def test_all_three_of_schema_menu_and_packager_are_required(tmp_path):
    partial = _fork(
        tmp_path,
        **{"chat_schema.py": '"enum": ["drop", "step", "emp_launch"]'},
    )
    rung = _by_n(readiness.check(partial))[2]
    assert not rung.passed
    # It should name what is still missing, not just say no.
    assert "agency.py" in rung.detail
    assert "packager" in rung.detail


def test_the_full_path_passes(tmp_path):
    root = _fork(
        tmp_path,
        **{
            "chat_schema.py": '"enum": ["drop", "emp_launch", "chaff_flare"]',
            "agency.py": 'Option(kind="emp", moves=[{"a": "emp_launch"}])',
            "packager.py": 'if tag == "emp_launch": keep(move)',
        },
    )
    assert _by_n(readiness.check(root))[2].passed


def test_the_frozen_v7_lineage_does_not_count(tmp_path):
    """A fork that only edits the frozen baseline has not changed its
    live agent, so the attendee would see no behaviour change."""
    root = _fork(
        tmp_path,
        **{
            "_v7__chat_schema.py": '"enum": ["emp_launch"]',
            "_v7__agency.py": '{"a": "emp_launch"}',
            "_v7__packager.py": '"emp_launch"',
        },
    )
    assert not _by_n(readiness.check(root))[2].passed


# ── rung 3: the offer explains itself ─────────────────────────────


def test_geometry_without_prose_fails(tmp_path):
    root = _fork(
        tmp_path,
        **{"agency.py": 'Option(option_id="EMP1", moves=[{"a": "emp_launch"}])'},
    )
    rung = _by_n(readiness.check(root))[3]
    assert not rung.passed
    assert "rationale" in rung.fix


def test_detail_and_rationale_pass(tmp_path):
    root = _fork(
        tmp_path,
        **{
            "agency.py": (
                'Option(option_id="EMP1", moves=[{"a": "emp_launch"}], '
                'detail="3 rival probes under 2 clouds", '
                'rationale="blinds their eye on your pure for 8h")'
            ),
        },
    )
    assert _by_n(readiness.check(root))[3].passed


def test_rung_three_defers_to_rung_two(tmp_path):
    """With no weapon option to grade, say so rather than blaming prose."""
    rung = _by_n(readiness.check(_fork(tmp_path, **{"agency.py": "pass"})))[3]
    assert not rung.passed
    assert "rung 2" in rung.fix


# ── rung 4: doctrine says when ────────────────────────────────────


def test_defensive_doctrine_does_not_count(tmp_path):
    """The exact false positive the first version shipped with."""
    root = _fork(
        tmp_path,
        **{
            "doctrine.py": (
                "spend it landing on a rival's probe to blind it — deny "
                "their next landing + vision. BEWARE EMP: if they launch "
                "an EMP over your drop, re-time the landing."
            ),
        },
    )
    rung = _by_n(readiness.check(root))[4]
    assert not rung.passed, "rival-subject weapon prose must not pass"


@pytest.mark.parametrize("prose", [
    "fire your EMP at the probe that found the redsign",
    "spend a chaff to cancel their lift and strand the cargo",
    "launch an EMP over the seam at hour one",
    "flare chaff when their harvester is loaded",
])
def test_offensive_doctrine_passes(tmp_path, prose):
    root = _fork(tmp_path, **{"doctrine.py": prose})
    assert _by_n(readiness.check(root))[4].passed, prose


# ── the report ────────────────────────────────────────────────────


def test_a_finished_agent_is_pointed_at_the_suite(tmp_path):
    """All four built is not the end — firing well is a separate question."""
    root = _fork(
        tmp_path,
        **{
            "world_view.py": "out['weapon_stock'] = stock",
            "chat_schema.py": '"enum": ["emp_launch", "chaff_flare"]',
            "agency.py": (
                'Option(moves=[{"a": "emp_launch"}], detail="d", '
                'rationale="r")'
            ),
            "packager.py": '"emp_launch"',
            "doctrine.py": "fire your EMP at their eye on the pure",
        },
    )
    rungs = readiness.check(root)
    assert readiness.first_gap(rungs) is None
    out = readiness.render("myteam_myagent", rungs)
    assert "loadout empty,both" in out
    assert "firing into space" in out
