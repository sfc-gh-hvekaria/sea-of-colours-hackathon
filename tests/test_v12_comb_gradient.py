"""OBS-56 — the seam comb must walk TOWARD the value it has not banked yet.

The turn that exposed this is `own_seam_three_pures`: three pures in a row, the
sweep landed on the first and walked away from the other two. Two independent
faults produced it, so both are pinned separately — fixing either alone still
leaves the walk pointing the wrong way.
"""

from __future__ import annotations

from sea_of_colours.orchestrator_2.harnesses.tabula_v12.comb_shapes import comb_path


def _path(start, value_cells, *, centre=(20, 3), steps=2, bad=frozenset()):
    return [tuple(c) for c in comb_path(
        centre[0], centre[1], start, 48, 32, set(bad), value_cells,
        max_steps=steps,
    )]


def test_the_walk_does_not_chase_the_cell_it_is_standing_on():
    # Standing ON the richest cell, every neighbour is one step from it. If the
    # banked cell still counts, the gradient is flat and the tie-break decides.
    pures = [(18, 2), (19, 3), (20, 3)]
    path = _path((18, 2), pures)
    assert (18, 2) not in path, "never re-walk the drop cell"
    assert path, "a two-step budget must produce a walk"
    # Whatever route it takes, it has to end up on unbanked value.
    assert set(path) & {(19, 3), (20, 3)}, (
        f"walked {path}, banking none of the remaining pures {pures[1:]}"
    )


def test_a_tie_on_distance_is_broken_by_which_cell_is_worth_more():
    # Both directions reach a value cell in two steps. `value_cells` arrives
    # richest-first, so east (toward the pure) must win over north (the vein).
    #   start (18,2);  east -> (19,2) -> (19,3) PURE
    #                  north -> (18,1) -> (18,0) vein
    richest_first = [(19, 3), (20, 3), (18, 0)]
    path = _path((18, 2), richest_first)
    assert (19, 3) in path, f"took {path} instead of the pure at (19,3)"
    assert (18, 0) not in path


def test_reversing_the_ranking_reverses_the_choice():
    # Guards against the test passing for a geometric reason rather than
    # because the ordering is being read.
    vein_first = [(18, 0), (19, 3), (20, 3)]
    assert (18, 0) in _path((18, 2), vein_first)


def test_known_green_and_revisits_are_still_refused():
    path = _path((18, 2), [(19, 3), (20, 3)], steps=5, bad={(18, 3)})
    assert (18, 3) not in path
    assert len(path) == len(set(path))
