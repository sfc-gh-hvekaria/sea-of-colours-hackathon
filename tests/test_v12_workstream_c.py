"""Workstream C — compiler authority and feedback.

These are deterministic: they build the menu (or run the compiler) over a fixed
board and assert a property. No model call, so a fix is confirmed in
milliseconds rather than by a pass rate over six stochastic replays. The
behavioural half of each observation — what the agent *chooses* once it is told
the truth — is measured separately by ``scripts/turn_suite.py``.

Each test names the observation it pins.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

import pytest

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import agency, packager


def _chain_hint(cells: List[List[int]]) -> Dict[str, Any]:
    """A juice-chain hint shaped exactly as ``top_chain_hints`` emits one.

    Note ``cells[0]`` IS the drop cell — the walk includes the cell landed on.
    """
    return {
        "unit": "harvester_p1",
        "drop_at": list(cells[0]),
        "cells": cells,
        "length": len(cells),
        "purities": [80] * len(cells),
        "tiers": ["vein"] * len(cells),
    }


def _view() -> Dict[str, Any]:
    return {"day": 6, "grid": [], "probe_stock": 4}


def _registry(**kw: Any) -> "Mapping[str, agency.Option]":
    return agency.build_registry(agent_view=_view(), **kw)


# ── OBS-22 — the hazard check belongs at menu build, not in the compiler ──
def test_hazard_drop_cell_is_never_offered():
    """An option that lands a harvester on a cell WE stripped to green is
    withheld from the menu entirely — it banks nothing and costs -100."""
    hint = _chain_hint([[18, 3], [18, 2], [18, 1]])

    clean = _registry(chain_hints=[hint])
    assert any((18, 3) in agency._option_drop_cells(o) for o in clean.values()), (
        "sanity: without a hazard set, something must drop on (18,3) — "
        "otherwise this test proves nothing"
    )

    reg = _registry(chain_hints=[hint], hazard_cells=[(18, 3)])
    assert len(reg) < len(clean), "the hazard must remove at least one option"
    for opt in reg.values():
        assert (18, 3) not in agency._option_drop_cells(opt), (
            f"{opt.option_id} still drops on the hazard cell"
        )


def test_hazard_inside_a_walk_is_priced_not_refused():
    """Green is legal and merely costs -100, so a hazard the walk merely crosses
    keeps the option on the menu and states the penalty (OBS-22, OBS-11)."""
    hint = _chain_hint([[10, 10], [10, 11], [10, 12]])
    reg = _registry(chain_hints=[hint], hazard_cells=[(10, 12)])

    hits = [o for o in reg.values() if (o.payload or {}).get("hazard_in_walk")]
    assert hits, "an option crossing a hazard should stay on the menu, annotated"
    assert [10, 12] in hits[0].payload["hazard_in_walk"]

    econ = agency.option_economics.annotate(hits[0].payload or {}, _view(), {})
    lines = "\n".join(agency._econ_detail_lines(hits[0], econ))
    assert "HAZARD in walk" in lines, "the penalty must be stated on the row"
    assert "(10,12)" in lines


def test_the_drop_cell_is_not_double_reported_as_a_walk_hazard():
    """A chain's ``cells[0]`` is its drop cell. Removing the option already
    covers that case, so it must not also surface under the walk heading."""
    hint = _chain_hint([[4, 4], [4, 5]])
    reg = _registry(chain_hints=[hint], hazard_cells=[(4, 4)])
    for opt in reg.values():
        assert [4, 4] not in ((opt.payload or {}).get("hazard_in_walk") or [])


def test_no_hazard_set_leaves_the_menu_untouched():
    """The common case: no stripped green yet, so nothing is filtered."""
    hint = _chain_hint([[18, 3], [18, 2], [18, 1]])
    assert set(_registry(chain_hints=[hint])) == set(
        _registry(chain_hints=[hint], hazard_cells=[])
    )


# ── OBS-15 / OBS-20 — hazard is an engine fact, `avoid` is a guess ──────
def _packer(**kw: Any) -> "packager._Packer":
    view = {
        "day": 6,
        "orbit": {"harvesters": ["harvester_p1"], "probe_stock": 2},
        "probe_stock": 2,
    }
    return packager._Packer(view, **kw)


def test_avoid_never_cancels_an_option_the_agent_picked():
    """The agent selected an option and listed its own drop cell in `avoid`.
    The pick wins, and the contradiction is reported rather than swallowed."""
    pk = _packer(avoid_cells={(18, 3)})
    ok = pk.emit_chain("harvester_p1", (18, 3), [(17, 3)])

    assert ok, "a soft avoid must not delete an explicit pick"
    assert any(m["a"] == "drop" and m["at"] == [18, 3] for m in pk.moves)
    assert any("CONTRADICTS your own pick" in line for line in pk.log)


def test_hazard_relocates_within_the_options_own_footprint():
    """A hazardous drop cell moves to the nearest legal cell the option already
    names — deleting the run is the last resort, not the first."""
    pk = _packer(forbidden_cells={(18, 3)})
    ok = pk.emit_chain("harvester_p1", (18, 3), [(18, 2), (18, 1)])

    assert ok, "the run should survive by relocating"
    drops = [m["at"] for m in pk.moves if m["a"] == "drop"]
    assert drops == [[18, 2]], f"expected relocation to (18,2), got {drops}"
    assert any("relocated drop" in line for line in pk.log)


def test_hazard_never_deletes_the_run_even_with_nowhere_clean_to_land():
    """Revised by fix 2.1. A green landing costs -100; it is not ILLEGAL, and the
    compiler only refuses what the engine refuses. When the option's whole
    footprint is hazardous the run still ships, priced, because a harvester left
    in orbit banks nothing at all — strictly worse than one that eats a penalty
    and then works (OBS-27)."""
    pk = _packer(forbidden_cells={(18, 3), (18, 2)})
    ok = pk.emit_chain("harvester_p1", (18, 3), [(18, 2)])

    assert ok, "a costly-but-legal run must not be deleted"
    assert any(m["a"] == "drop" and m["at"] == [18, 3] for m in pk.moves)
    log = " ".join(pk.log)
    assert "KEPT" in log and "-100" in log, log


def test_chaff_react_no_longer_shortens_anything():
    """Fix 2.10. The flag used to mean "cap EVERY chain at 2 steps". A step past
    the second is not illegal — the engine takes a 5-step walk under chaff
    happily — so route length is a value judgement and belongs to the agent. It
    was also the costliest thing the compiler did: on the d4 walk-in the agent
    routed correctly onto a pure(255) four steps out, the cap stopped it at two
    and banked trace, in every run of four consecutive suite sweeps."""
    view = {
        "day": 4,
        "my_assets": [{"kind": "harvester", "state": "orbit",
                       "id": "harvester_p1"}],
        "orbit": {"probe_stock": 0},
    }
    route = [(29, 19), (30, 19), (31, 19), (31, 18), (31, 17)]
    pk = packager._Packer(view, chaff_short=True)
    assert pk.emit_chain("harvester_p1", (29, 18), route)

    walked = [tuple(m["to"]) for m in pk.moves if m["a"] == "step"]
    assert walked == route, f"every ordered step must ship, got {walked}"


def test_chaff_react_is_still_reported_so_the_intent_is_visible():
    """Removing the cap must not make the flag vanish: the agent said it
    expected a jam, and the card should say so — just without acting on it."""
    opts = [_Opt("chain", {"drop_at": [5, 5], "cells": [[5, 5]]}, "CH1")]
    _, log = packager.pack_recipe(
        opts, _fleet_view(1, 0), complete=False, chaff_short=True,
    )
    assert any("chaff_react" in line and "NOTHING was shortened" in line
               for line in log), log


def test_a_second_landing_on_one_cell_is_priced_not_refused():
    """The engine only collides drops made in the SAME HOUR, and a seat acts once
    per hour, so two of our own drops on one cell never collide. Deleting the
    second run cost the contested ring on SNAP_ac1c55bf_d6_p4 (OBS-42)."""
    pk = _packer()
    pk.harvesters = ["harvester_p1", "harvester_p1_2"]
    assert pk.emit_chain("harvester_p1", (18, 3), [])
    assert pk.emit_chain("harvester_p1_2", (18, 3), [(18, 2)]), \
        "the second landing is legal and must ship"

    drops = [m["at"] for m in pk.moves if m["a"] == "drop"]
    assert drops == [[18, 3], [18, 3]]
    assert any("SECOND landing" in line for line in pk.log)


def test_the_two_sets_produce_distinguishable_log_lines():
    """One message covering two unrelated causes is what made OBS-5 unreadable
    for weeks. A reader must be able to tell an engine fact from a guess."""
    haz = _packer(forbidden_cells={(5, 5)})
    haz.emit_chain("harvester_p1", (5, 5), [])
    avo = _packer(avoid_cells={(5, 5)})
    avo.emit_chain("harvester_p1", (5, 5), [])

    haz_txt, avo_txt = " ".join(haz.log), " ".join(avo.log)
    assert "hazard memory" in haz_txt and "hazard memory" not in avo_txt
    assert "avoid" in avo_txt and "avoid" not in haz_txt


# ── OBS-21 — seams and singles rank in ONE pool ─────────────────────────
def _board(harvesters: int, red: Mapping[Any, int]) -> Dict[str, Any]:
    """A view with ``harvesters`` in orbit and RED cells at stated purities."""
    return {
        "day": 5,
        "orbit": {"probe_stock": 4},
        "my_assets": [
            {"kind": "harvester", "state": "orbit", "id": f"harvester_p1_{i}"}
            for i in range(harvesters)
        ],
        "world": {
            "live": [
                {"x": x, "y": y, "tile": "RED", "purity": p}
                for (x, y), p in red.items()
            ]
        },
    }


def _opt(oid: str, kind: str, payload: Dict[str, Any]) -> "agency.Option":
    return agency.Option(option_id=oid, kind=kind, title=oid, detail="",
                         payload=payload)


def _seam(oid: str, waves: List[Dict[str, Any]]) -> "agency.Option":
    return _opt(oid, "seam", {"waves": waves})


def _wave(drop: List[int], comb: List[List[int]], **kw: Any) -> Dict[str, Any]:
    return {"drop_at": drop, "comb_path": comb, "deny_only": False, **kw}


def test_a_rich_chain_beats_a_thin_two_wave_seam():
    """OBS-21 exactly: the seam used to reserve both harvesters before any value
    was compared, so a 70-pt campaign cut a 253-pt chain sight unseen."""
    view = _board(2, {(3, 3): 90, (3, 4): 90, (3, 5): 90,   # the rich chain
                      (9, 9): 20, (9, 8): 20})              # the thin seam
    chain = _opt("CH1", "chain",
                 {"drop_at": [3, 3], "cells": [[3, 3], [3, 4], [3, 5]]})
    seam = _seam("SS1", [_wave([9, 9], []), _wave([9, 8], [])])

    kept, report = packager.reconcile_selected([seam, chain], view)

    assert chain in kept, "the higher-value run must survive"
    assert seam not in kept
    dropped = [r for r in report if r["status"] == "dropped"]
    assert [r["id"] for r in dropped] == ["SS1"]


def test_the_denial_premium_can_carry_a_seam_over_a_marginal_chain():
    """The premium is a real thumb on the scale, not decoration: a seam that
    blinds a finder outranks a chain it only narrowly trails on raw red."""
    view = _board(1, {(3, 3): 30, (9, 9): 25})
    chain = _opt("CH1", "chain", {"drop_at": [3, 3], "cells": [[3, 3]]})
    seam = _seam("SS1", [_wave([9, 9], [], supersede=[9, 10])])

    kept, _ = packager.reconcile_selected([seam, chain], view)

    assert seam in kept and chain not in kept


def test_the_premium_cannot_rescue_a_worthless_seam():
    """A premium that outweighs the board is an exemption wearing a disguise.
    One pure cell must still beat a blinding campaign over empty ground."""
    view = _board(1, {(3, 3): 255})
    chain = _opt("CH1", "chain", {"drop_at": [3, 3], "cells": [[3, 3]]})
    seam = _seam("SS1", [_wave([9, 9], [], supersede=[9, 10])])

    kept, _ = packager.reconcile_selected([seam, chain], view)

    assert chain in kept and seam not in kept


def test_a_deny_only_campaign_never_competes_for_a_harvester():
    """CONTEST_DENY spends probes, not units, so it costs the harvest nothing
    and both plays run on a one-harvester night."""
    view = _board(1, {(3, 3): 90})
    chain = _opt("CH1", "chain", {"drop_at": [3, 3], "cells": [[3, 3]]})
    deny = _seam("CD1", [{"drop_at": [9, 9], "comb_path": [],
                          "deny_only": True, "supersede": [9, 10]}])

    kept, _ = packager.reconcile_selected([deny, chain], view)

    assert chain in kept and deny in kept


def test_the_drop_reason_states_what_was_kept_instead():
    """The old text claimed 'kept the higher-value run(s)' while dropping a 253
    for a 70. This is the only compiler decision the agent ever reads."""
    view = _board(1, {(3, 3): 90, (9, 9): 20})
    rich = _opt("CH1", "chain", {"drop_at": [3, 3], "cells": [[3, 3]]})
    thin = _opt("CH2", "chain", {"drop_at": [9, 9], "cells": [[9, 9]]})

    _, report = packager.reconcile_selected([thin, rich], view)

    reason = next(r["reason"] for r in report if r["id"] == "CH2")
    assert "CH1" in reason, f"the reason must name the survivor: {reason!r}"
    assert "kept the higher-value run" not in reason


def test_probe_reconciliation_is_untouched_by_the_pooling():
    """Probes are a separate budget; pooling harvester runs must not disturb
    which probes survive a stock shortage."""
    view = _board(2, {})
    view["orbit"]["probe_stock"] = 1
    p1 = _opt("PR1", "probe", {"probe_at": [1, 1], "area_gain": 30})
    p2 = _opt("PR2", "probe", {"probe_at": [2, 2], "area_gain": 5})

    kept, _ = packager.reconcile_selected([p2, p1], view)

    assert kept == [p1], "the better probe survives; the pool is per-resource"


# ── OBS-17 / OBS-20 — the two passes that ADD plays answer to a switch ──
def test_the_completion_pass_can_be_turned_off():
    """With autofill off the compiled night contains the agent's plan and
    nothing else — no filler probes, no idle harvester on a stray chain."""
    view = _board(2, {(3, 3): 90, (9, 9): 40})
    view["orbit"]["probe_stock"] = 3
    chain = _opt("CH1", "chain", {"drop_at": [3, 3], "cells": [[3, 3]]})
    spare = {"drop_at": [9, 9], "cells": [[9, 9]]}
    probes = [{"probe_at": [1, 1], "area_gain": 9}]

    on, log_on = packager.pack_recipe(
        [chain], view, chain_hints=[spare], probe_hints=probes, complete=True,
    )
    off, log_off = packager.pack_recipe(
        [chain], view, chain_hints=[spare], probe_hints=probes, complete=False,
    )

    assert any("completion:" in line for line in log_on), (
        "sanity: autofill must actually fire here, or the test proves nothing"
    )
    assert not any("completion:" in line for line in log_off)
    assert [m for m in on if m["a"] == "probe"], "on: filler probes are spent"
    assert not [m for m in off if m["a"] == "probe"], (
        "off: the agent asked for no probes, so none are launched"
    )
    assert len(off) < len(on)


def test_autofill_is_off_by_default_now_that_coverage_runs_upstream(
    monkeypatch: Any,
):
    """Fix 2.3, and the reason the default could flip. The requirement autofill
    served — use every harvester — did not go away; it moved to the coverage
    check between THINK and PLAN (2.7), where the AGENT spends the shortfall.
    The old guarantee stays reachable so the two can be measured head to head."""
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import harness as h

    monkeypatch.delenv("TABULA_V12_AUTOFILL", raising=False)
    assert h._autofill_on() is False
    for on in ("1", "true", "on", "YES"):
        monkeypatch.setenv("TABULA_V12_AUTOFILL", on)
        assert h._autofill_on() is True, f"{on!r} should restore autofill"
    monkeypatch.setenv("TABULA_V12_AUTOFILL", "0")
    assert h._autofill_on() is False


# ── OBS-17 / OBS-20 fix 2.7 — the coverage check that replaces autofill ──
def test_the_coverage_check_names_what_the_plan_left_idle():
    """The count is deterministic and lists the options still available, so the
    agent can act on it rather than being overruled after the fact."""
    reg = {
        "CH1": _Opt("chain", {"drop_at": [5, 5], "cells": [[5, 5]]}, "CH1"),
        "CH2": _Opt("chain", {"drop_at": [9, 9], "cells": [[9, 9]]}, "CH2"),
        "SS1": _Opt("supersede", {"probe_at": [2, 2]}, "SS1"),
    }
    note = packager.coverage_note([reg["CH1"]], _fleet_view(2, 3), reg)

    assert "1 of your 2 harvester(s) stay in ORBIT" in note
    assert "CH2" in note and "SS1" in note
    assert "CH1" not in note, "an option already chosen must not be re-offered"


def test_the_coverage_check_is_silent_when_the_plan_spends_everything():
    """It must not become boilerplate — silence is the normal case."""
    reg = {
        "CH1": _Opt("chain", {"drop_at": [5, 5], "cells": [[5, 5]]}, "CH1"),
        "CH2": _Opt("chain", {"drop_at": [9, 9], "cells": [[9, 9]]}, "CH2"),
        "SS1": _Opt("supersede", {"probe_at": [2, 2]}, "SS1"),
    }
    full = list(reg.values())
    assert packager.coverage_note(full, _fleet_view(2, 1), reg) == ""


def test_probes_buried_inside_a_run_count_against_stock():
    """A seam wave's supersede and a hot-drop's enabling probe are real stock
    draws. Missing them would report spare probes that do not exist."""
    seam = _Opt("seam", {"waves": [
        {"wave": 1, "drop_at": [16, 6], "probe_at": [19, 6], "comb_path": []},
        {"wave": 2, "deny_only": True, "supersede": [16, 9]},
    ]}, "SMASH_GRAB")
    assert packager._probe_demand(seam) == 2
    assert packager.coverage_note([seam], _fleet_view(1, 2), {"S": seam}) == ""


def test_the_sanitizer_deploy_all_guard_answers_to_the_same_switch():
    """T6 is the sanitizer's only ADDING guard. Measuring autofill means moving
    both passes together, so it takes the flag too — while every legality fix
    the sanitizer performs stays on regardless."""
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import move_sanitizer

    view = {
        "day": 5,
        "world": {
            "width": 30, "height": 30,
            # T6 only lands on cells in LIVE vision, so the spare chain has to
            # be lit for the guard to have anything to fire on.
            "live": [
                {"x": 3, "y": 3, "tile": "RED", "purity": 90},
                {"x": 9, "y": 9, "tile": "RED", "purity": 40},
                {"x": 9, "y": 10, "tile": "RED", "purity": 40},
            ],
        },
        "my_assets": [
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1"},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1_2"},
        ],
    }
    plan = [
        {"a": "drop", "unit": "harvester_p1", "at": [3, 3]},
        {"a": "pickup", "unit": "harvester_p1"},
    ]
    hints = [{"drop_at": [9, 9], "cells": [[9, 9], [9, 10]],
              "unit": "harvester_p1_2"}]

    on, log_on = move_sanitizer.sanitize_moves(
        list(plan), view, chain_hints=hints, deploy_idle=True,
    )
    off, log_off = move_sanitizer.sanitize_moves(
        list(plan), view, chain_hints=hints, deploy_idle=False,
    )

    assert any("deployed idle harvester" in line for line in log_on), (
        "sanity: T6 must fire here, or this test proves nothing"
    )
    assert not any("deployed idle harvester" in line for line in log_off)
    assert off == plan, "with T6 off a legal plan passes through untouched"


# ── OBS-20 — every intervention is reported back to the agent ───────────
def test_the_compilers_edits_reach_the_agent():
    """The compiler was the largest of the three rewrites and the only one
    never shown. An edit it makes must appear in next turn's recap."""
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import digest

    entry = {
        "day": 5,
        "plan_ids": ["BLIND_GRAB", "PR1"],
        "compiler_notes": [
            "relocated drop [18, 3] -> [18, 2]: original is known stripped/GREEN",
            "completion: spent a leftover probe at [23, 4] — you did not pick this",
        ],
        "moves_executed": 7,
    }
    text = digest.format_self_execution_block(entry, {"last_night": {}})

    assert "THE COMPILER CHANGED YOUR NIGHT (2 edit(s))" in text
    assert "[18, 3] -> [18, 2]" in text
    assert "[23, 4]" in text


def test_the_compiler_and_the_corrector_are_told_apart():
    """One heading for two subsystems is how OBS-15 stayed misdiagnosed. They
    changed different things and the agent has to be able to tell which."""
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import digest

    text = digest.format_self_execution_block(
        {
            "day": 5,
            "compiler_notes": ["skip drop [9, 9]: no legal cell"],
            "corrector_notes": ["harvester_p1: rerouted around a friendly probe"],
            "moves_executed": 4,
        },
        {"last_night": {}},
    )

    assert text.index("THE COMPILER CHANGED YOUR NIGHT") < text.index(
        "THE CORRECTOR REWROTE YOUR PLAN"
    ), "what was attempted comes before how it was expressed"
    assert "skip drop [9, 9]" in text and "rerouted around a friendly probe" in text


# ── OBS-27 fix 2.4 / 2.5 — order, and don't spend for a run that died ───
class _Opt:
    """The minimum ``pack_recipe`` reads off a resolved option."""

    def __init__(self, kind: str, payload: Dict[str, Any], oid: str = "X") -> None:
        self.kind, self.payload, self.option_id = kind, payload, oid


def _fleet_view(harvesters: int = 2, probes: int = 4) -> Dict[str, Any]:
    return {
        "day": 6,
        "my_assets": [
            {"kind": "harvester", "state": "orbit",
             "id": "harvester_p1" if i == 0 else f"harvester_p1_{i + 1}"}
            for i in range(harvesters)
        ],
        "orbit": {"probe_stock": probes},
        "probe_stock": probes,
    }


def test_the_agents_own_execution_order_survives_compilation():
    """Fix 2.4, corrected. An earlier cut hoisted every outing ahead of every
    standalone probe, reasoning that a probe buys tomorrow while an outing banks
    tonight. Sound as ADVICE, not the compiler's to impose:
    harvester -> probe -> harvester is a legal night and an agent may want it.
    The plan is the order the agent gave."""
    opts = [
        _Opt("chain", {"drop_at": [5, 5], "cells": [[5, 5], [5, 6]]}, "CH1"),
        _Opt("probe", {"at": [20, 20]}, "PR1"),
        _Opt("chain", {"drop_at": [8, 8], "cells": [[8, 8], [8, 9]]}, "CH2"),
    ]
    moves, _ = packager.pack_recipe(opts, _fleet_view(), complete=False)

    seq = [m["a"] for m in moves]
    probe_i = seq.index("probe")
    drops = [i for i, a in enumerate(seq) if a == "drop"]
    assert drops[0] < probe_i < drops[1], (
        f"the probe must stay BETWEEN the two outings as ordered, got {seq}"
    )


def test_a_probe_that_enables_its_own_drop_still_fires_first():
    """The 2.4 reordering must not break the fogged smash: a hot-drop's probe
    lives inside its own run and has to open its disk before the landing."""
    opts = [
        _Opt("hotdrop", {"drop_at": [16, 6], "probe_at": [19, 6],
                         "comb_path": []}, "SMASH_GRAB"),
        _Opt("probe", {"at": [20, 20]}, "PR1"),
    ]
    moves, _ = packager.pack_recipe(opts, _fleet_view(), complete=False)

    drop_i = next(i for i, m in enumerate(moves) if m["a"] == "drop")
    enabling_i = next(i for i, m in enumerate(moves)
                      if m["a"] == "probe" and m["at"] == [19, 6])
    assert enabling_i < drop_i, "the enabling probe must precede its own drop"


def test_a_run_that_cannot_compile_returns_its_probes_to_the_pool():
    """SECURE_MASS was deleted on SNAP_ac1c55bf_d6_p4 but its probe still fired.
    A run is all-or-nothing: if the chain dies, its probes are unspent."""
    opts = [
        _Opt("hotdrop", {"drop_at": None, "probe_at": [19, 6],
                         "comb_path": []}, "DEAD"),
    ]
    moves, log = packager.pack_recipe(opts, _fleet_view(), complete=False)

    assert moves == [], f"nothing should reach the board, got {moves}"
    assert any("RETURNED to the pool" in line for line in log), log


def test_a_walked_over_cell_is_free_once_its_occupant_has_left():
    """The collision map used to hold the whole night's TRAIL, so a second
    harvester could never cross ground the first had walked. A seat acts once
    per hour, so by then the first unit has moved on. The trail model truncated
    the run carrying the pure on SNAP_408ddd46_d4_p1 in 6 baseline runs of 6."""
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import move_sanitizer

    view = {
        "day": 4,
        "world": {
            "width": 40, "height": 40,
            "live": [{"x": x, "y": 18, "tile": "RED", "purity": 90}
                     for x in (29, 30, 31)],
        },
        "my_assets": [
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1"},
            {"kind": "harvester", "state": "orbit", "id": "harvester_p1_2"},
        ],
    }
    # h1 walks (29,18)->(30,18) and lifts. h2 then wants to cross (29,18).
    moves = [
        {"a": "drop", "unit": "harvester_p1", "at": [29, 18]},
        {"a": "step", "unit": "harvester_p1", "to": [30, 18]},
        {"a": "pickup", "unit": "harvester_p1"},
        {"a": "drop", "unit": "harvester_p1_2", "at": [31, 18]},
        {"a": "step", "unit": "harvester_p1_2", "to": [30, 18]},
        {"a": "step", "unit": "harvester_p1_2", "to": [29, 18]},
        {"a": "pickup", "unit": "harvester_p1_2"},
    ]

    trail, _ = move_sanitizer.sanitize_moves(
        list(moves), view, deploy_idle=False, release_vacated_cells=False,
    )
    occupancy, log = move_sanitizer.sanitize_moves(
        list(moves), view, deploy_idle=False, release_vacated_cells=True,
    )

    def _steps(out):
        return [tuple(m["to"]) for m in out
                if m["a"] == "step" and m["unit"] == "harvester_p1_2"]

    assert _steps(trail) == [], "sanity: the trail model must truncate here"
    assert _steps(occupancy) == [(30, 18), (29, 18)], (
        f"the walk must survive once its occupant has lifted, got {log}"
    )


def test_a_clean_night_reports_no_interventions():
    """The recap must stay quiet when nothing was changed, or the heading
    stops meaning anything."""
    from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import digest

    text = digest.format_self_execution_block(
        {"day": 5, "plan_ids": ["CH1"], "compiler_notes": [], "moves_executed": 6},
        {"last_night": {}},
    )
    assert "THE COMPILER CHANGED YOUR NIGHT" not in text


def test_completion_says_what_it_spent_and_where():
    """'spent leftover probe on offered target' names neither the cell nor the
    fact that the agent did not ask for it (OBS-17)."""
    view = _board(1, {(3, 3): 90})
    view["orbit"]["probe_stock"] = 1
    chain = _opt("CH1", "chain", {"drop_at": [3, 3], "cells": [[3, 3]]})

    _, log = packager.pack_recipe(
        [chain], view, probe_hints=[{"probe_at": [23, 4], "area_gain": 9}],
        complete=True,
    )

    line = next(l for l in log if "completion:" in l and "probe" in l)
    assert "[23, 4]" in line, f"the cell must be named: {line!r}"
    assert "you did not pick this" in line


def test_probe_options_survive_a_hazard_cell():
    """Only harvester DROPS are vetoed. A probe landing on stripped green is
    harmless and may still be the right denial."""
    reg = _registry(
        probe_hints=[{"probe_at": [7, 7], "area_gain": 12}],
        hazard_cells=[(7, 7)],
    )
    assert any(o.kind == "probe" for o in reg.values()), (
        "a probe placement must not be filtered by the harvester hazard rule"
    )
