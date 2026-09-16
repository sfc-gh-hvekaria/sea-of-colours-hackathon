"""v12 — don't blind your own later drop by crushing the probe that lights it.

Reproduces the failure seen three times in the board-pass captures. Night 5 of
session 871128e7 ordered:

    H01 drop (16,22)   ok   <- (16,22) IS probe_p1_7's cell, so the probe dies
    ...
    H05 drop (14,20)   FAILED  drop: (14,20) has no live sensor beacon

Both chains were legal when the menu was built; the first one destroyed the only
disk lighting the second. The engine reports it the next morning, and the agent's
reflection blames itself for "misreading DROP-LEGAL zones" — but the prompt DID
list (14,20) as legal, because it was, until H01.

Legality is not a judgement call, so the packager reorders: the dependent run
goes first. Nothing is added, dropped or re-aimed.
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import packager


class _Opt:
    """Minimal stand-in for agency.Option (kind + payload is all we read)."""

    def __init__(self, option_id, kind, drop_at, cells=()):
        self.option_id = option_id
        self.kind = kind
        self.payload = {"drop_at": list(drop_at), "cells": [list(c) for c in cells]}


def _view(probe_cells, *, nights=2):
    return {
        "world": {"width": 40, "height": 28},
        "entities": {"mine": [
            {"id": f"probe_p1_{i}", "type": "probe", "pos": list(c),
             "nights_remaining": nights}
            for i, c in enumerate(probe_cells)
        ]},
    }


def _ids(opts):
    return [o.option_id for o in opts]


# ── the captured failure ────────────────────────────────────────────────
def test_dependent_run_is_moved_ahead_of_the_crushing_run():
    av = _view([(16, 22)])
    crusher = _Opt("CH1S", "chain", (16, 22), [(16, 21), (17, 21)])
    dependent = _Opt("CH2S", "chain", (14, 20), [(15, 20), (15, 21)])
    out, log = packager._order_for_probe_support([crusher, dependent], av)
    assert _ids(out) == ["CH2S", "CH1S"]
    assert log and "probe crush" in log[0]


def test_untouched_when_the_order_is_already_safe():
    av = _view([(16, 22)])
    dependent = _Opt("CH2S", "chain", (14, 20), [(15, 20)])
    crusher = _Opt("CH1S", "chain", (16, 22), [(16, 21)])
    out, log = packager._order_for_probe_support([dependent, crusher], av)
    assert _ids(out) == ["CH2S", "CH1S"]
    assert log == []


def test_walking_over_a_probe_also_counts_as_a_crush():
    # 871128e7 night 4: the chain STEPPED onto probe_p1_6@(24,4) at H06, then
    # dropped (27,4) at H08 — same class, crush by step rather than by drop.
    av = _view([(24, 4)])
    crusher = _Opt("CH1", "chain", (26, 7), [(26, 6), (26, 5), (25, 5), (24, 4)])
    dependent = _Opt("CH2", "chain", (27, 4), [(27, 5)])
    out, _ = packager._order_for_probe_support([crusher, dependent], av)
    assert _ids(out) == ["CH2", "CH1"]


# ── things it must NOT do ───────────────────────────────────────────────
def test_a_second_disk_means_no_reorder():
    # (14,20) is also lit by a probe nobody crushes, so the plan is fine as-is.
    av = _view([(16, 22), (13, 19)])
    crusher = _Opt("CH1S", "chain", (16, 22), [(16, 21)])
    dependent = _Opt("CH2S", "chain", (14, 20), [(15, 20)])
    out, log = packager._order_for_probe_support([crusher, dependent], av)
    assert _ids(out) == ["CH1S", "CH2S"]
    assert log == []


def test_dropping_onto_your_own_probe_is_not_self_blocking():
    # The drop resolves, THEN the probe dies — landing on your own probe is a
    # legal (and deliberate) crush, not something to reorder around.
    av = _view([(16, 22)])
    solo = _Opt("CH1S", "chain", (16, 22), [(16, 21)])
    out, log = packager._order_for_probe_support([solo], av)
    assert _ids(out) == ["CH1S"] and log == []


def test_non_run_options_keep_their_slots():
    av = _view([(16, 22)])
    crusher = _Opt("CH1S", "chain", (16, 22), [(16, 21)])
    probe = _Opt("PR1", "probe", (5, 5))
    dependent = _Opt("CH2S", "chain", (14, 20), [(15, 20)])
    out, _ = packager._order_for_probe_support([crusher, probe, dependent], av)
    # runs swap; the probe stays in the middle slot it was given.
    assert _ids(out) == ["CH2S", "PR1", "CH1S"]


def test_expired_probes_do_not_constrain_anything():
    av = _view([(16, 22)], nights=0)
    crusher = _Opt("CH1S", "chain", (16, 22), [(16, 21)])
    dependent = _Opt("CH2S", "chain", (14, 20), [(15, 20)])
    out, log = packager._order_for_probe_support([crusher, dependent], av)
    assert _ids(out) == ["CH1S", "CH2S"] and log == []


def test_mutual_dependency_leaves_the_thinkers_order_alone():
    av = _view([(16, 22), (14, 20)])
    a = _Opt("CH1S", "chain", (16, 22), [(15, 21), (14, 20)])
    b = _Opt("CH2S", "chain", (14, 20), [(15, 21), (16, 22)])
    out, _ = packager._order_for_probe_support([a, b], av)
    assert _ids(out) == ["CH1S", "CH2S"]
