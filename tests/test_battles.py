"""The redsign battles — is the suite fair, and does it still discriminate?

The single most important test here is
:func:`test_the_canonical_play_scores_clean`. A scenario suite nobody can
pass is worse than no suite: attendees tune against noise, conclude the
kit is broken, and stop trusting every other number it prints. So every
board ships the canonical play written out as moves, and every one of
them must score 100%.

That test also keeps the boards and the predicates honest with each
other. It has already earned its place — writing the canonical for the
two echo boards is what surfaced that ``probes_after_harvesters``
forbade the hot-drop probe those very boards require.

The second concern is the opposite failure: a suite so loose that
anything passes. :func:`test_a_lazy_night_is_caught` plays the obvious
wrong answers and asserts they are rejected.
"""

from __future__ import annotations

import os

import pytest

from sea_of_colours.evals.battles import boards, ladder, predicates, stage
from sea_of_colours.snowpark import engine as soc_engine

os.environ.setdefault("SOC_BACKEND", "memory")


def _drop(unit, x, y):
    return {"a": "drop", "unit": unit, "at": [x, y]}


def _step(unit, x, y):
    return {"a": "step", "unit": unit, "to": [x, y]}


def _lift(unit):
    return {"a": "pickup", "unit": unit}


def _probe(x, y):
    return {"a": "probe", "at": [x, y]}


H1, H2 = "harvester_p1_1", "harvester_p1_2"

# The canonical play for each board, as moves. These are the prose
# canonicals in ``boards.py`` reduced to what the engine would receive —
# if you change a board's geometry, this is the other half of the edit.
CANONICAL: dict[str, list] = {
    # Drop ON the pure, take the mass, keep going: nothing here can
    # punish the extra hours.
    "early_solo_seam": [
        _drop(H1, 15, 22), _step(H1, 15, 23), _step(H1, 14, 23),
        _step(H1, 14, 24), _lift(H1),
    ],
    # Both units out with zero probes; the second goes outward at the
    # rival seam rather than following the first through its own wake.
    "no_probes_two_units": [
        _drop(H1, 31, 18), _step(H1, 31, 17), _step(H1, 32, 17), _lift(H1),
        _drop(H2, 14, 22), _step(H2, 15, 22), _lift(H2),
    ],
    # Bank the echo pure and blind the rival's finder in the same night.
    "echo_pure_blind_rival": [
        _probe(33, 17),
        _drop(H1, 31, 18), _step(H1, 31, 17), _lift(H1),
        _drop(H2, 17, 6), _step(H2, 17, 7), _lift(H2),
        _probe(16, 9),
    ],
    # Double smash: each unit lands on a pure, both lift at hour two.
    "two_pures_poker": [
        _drop(H1, 31, 18), _lift(H1),
        _drop(H2, 16, 6), _lift(H2),
    ],
    # Shortest line to the pure; second unit lands on the mass, crushes
    # their finder and walks OUTWARD away from the first unit's wake.
    "race_the_watched_pure": [
        _probe(30, 18),
        _drop(H1, 31, 18), _lift(H1),
        _drop(H2, 32, 17), _step(H2, 32, 16), _step(H2, 31, 16), _lift(H2),
    ],
    # Reorder so the landing banks a pure and the chain ends on one.
    "three_pures_one_unit": [
        _drop(H1, 18, 2), _step(H1, 19, 2), _step(H1, 19, 3),
        _step(H1, 20, 3), _lift(H1),
    ],
    # Blind the finder, then comb inward from the edge of the beacon.
    "blind_grab_rival_seam": [
        _probe(20, 3),
        _drop(H1, 19, 4), _step(H1, 19, 3), _step(H1, 18, 3), _lift(H1),
    ],
    # One probe lights the echo pure, both units commit to the seam,
    # the mass is taken DOWN the column away from the stripped cells,
    # and the remaining probes wait until the harvesters are away.
    "crowded_echo_seam": [
        _probe(19, 6),
        _drop(H1, 16, 6), _lift(H1),
        _drop(H2, 16, 7), _step(H2, 16, 8), _lift(H2),
        _probe(16, 9), _probe(24, 7),
    ],
    # Smash the pure, then a separate unit strips the ring.
    "final_night_ring": [
        _drop(H1, 31, 18), _lift(H1),
        _drop(H2, 31, 17), _step(H2, 32, 17), _lift(H2),
    ],
    # No pure, no sign — just work the seam properly with both units.
    # Deliberately fires nothing: on this board holding the charge is a
    # legitimate doctrine, so the canonical must pass without a salvo or
    # the suite would be scoring an opinion.
    "plain_night_armed": [
        _drop(H1, 16, 10), _step(H1, 17, 10), _step(H1, 17, 11),
        _step(H1, 17, 12), _step(H1, 16, 12), _lift(H1),
        _drop(H2, 18, 11), _step(H2, 18, 12), _step(H2, 18, 13),
        _step(H2, 19, 13), _step(H2, 19, 12), _lift(H2),
        _probe(24, 7),
    ],
}


def _play_for(board, moves, rung="quiet", loadout="empty"):
    battle = stage.stage(
        board, ladder.get_rung(rung), ladder.get_loadout(loadout)
    )
    sess = soc_engine._hydrate_session(battle.store, battle.session_id)
    return predicates.Play(moves=moves, session=sess)


# ── fairness: the canonical must pass ───────────────────────────────


@pytest.mark.parametrize("board", boards.BOARDS, ids=lambda b: b.id)
def test_the_canonical_play_scores_clean(board):
    """Every board is winnable by the play its own analysis prescribes.

    If this fails, the board and its predicates disagree and the board
    is unpassable — fix one of them before anyone tunes an agent
    against it.
    """
    moves = CANONICAL[board.id]
    checks = predicates.evaluate(_play_for(board, moves), board)
    failed = [c for c in checks if not c.passed]
    assert not failed, (
        f"the canonical play for {board.id!r} does not satisfy its own "
        f"predicates:\n"
        + "\n".join(f"  {c}" for c in failed)
        + f"\n\nCanonical was:\n  {board.canonical}"
    )


def test_every_board_has_a_canonical():
    """A board without one is a board nobody has proved is passable."""
    missing = [b.id for b in boards.BOARDS if b.id not in CANONICAL]
    assert not missing, f"boards with no canonical play: {missing}"


# ── discrimination: the obvious wrong answers must fail ─────────────


def test_a_lazy_night_is_caught():
    """Deploying one unit onto nothing must not score well."""
    board = boards.get("two_pures_poker")
    lazy = [_drop(H1, 5, 5), _lift(H1)]
    checks = predicates.evaluate(_play_for(board, lazy), board)
    failed = {c.name for c in checks if not c.passed}
    assert "take_pure" in failed
    assert "deploy_all" in failed


def test_walking_to_the_pure_fails_pure_first():
    """The cells can be right and the ordering still lose the night."""
    board = boards.get("two_pures_poker")
    walked = [
        _drop(H1, 33, 18), _step(H1, 32, 18), _step(H1, 31, 18), _lift(H1),
        _drop(H2, 16, 6), _lift(H2),
    ]
    checks = {c.name: c for c in predicates.evaluate(_play_for(board, walked), board)}
    assert checks["take_pure"].passed
    assert not checks["pure_first"].passed
    assert "step 2" in checks["pure_first"].detail


def test_the_second_wave_through_the_first_ones_wake_is_caught():
    board = boards.get("final_night_ring")
    through = [
        _drop(H1, 31, 18), _step(H1, 31, 17), _lift(H1),
        _drop(H2, 32, 17), _step(H2, 31, 17), _lift(H2),
    ]
    checks = {c.name: c for c in predicates.evaluate(_play_for(board, through), board)}
    assert not checks["no_wake_reentry"].passed
    assert "(31,17)" in checks["no_wake_reentry"].detail


def test_natural_green_is_not_punished():
    """Only harvested ground costs — ordinary green terrain is fine.

    Worth pinning because the two are indistinguishable on the grid and
    an over-eager check here would fail correct plays for walking over
    perfectly ordinary terrain.
    """
    board = boards.get("crowded_echo_seam")
    battle = stage.stage(board, ladder.get_rung("quiet"), ladder.get_loadout("empty"))
    sess = soc_engine._hydrate_session(battle.store, battle.session_id)
    play = predicates.Play(moves=[], session=sess)

    # (17,7) was stripped by a rival in the captured board.
    assert play.is_green((17, 7)), "staged synthetic green is not registering"

    natural = [
        (x, y)
        for y in range(sess.height) for x in range(sess.width)
        if int(sess.grid[y][x].tile) == 1
        and (sess.ledger.record(x, y) or {}).get("lineage") == "natural"
    ]
    assert natural, "no natural green on the map to check against"
    assert not play.is_green(natural[0])


# ── the ladder ──────────────────────────────────────────────────────


def test_rungs_get_harder_monotonically():
    """The ladder has to actually be a ladder."""
    rungs = [ladder.get_rung(r) for r in ladder.ORDER]
    for easier, harder in zip(rungs, rungs[1:]):
        assert harder.opponents >= easier.opponents
        assert (harder.rival_chaff + harder.rival_emp) >= (
            easier.rival_chaff + easier.rival_emp
        )
        assert harder.extra_rival_probes >= easier.extra_rival_probes


def test_up_to_returns_easiest_first():
    got = [r.id for r in ladder.up_to("armed")]
    assert got == ["quiet", "watched", "armed"]


@pytest.mark.parametrize("board", boards.BOARDS, ids=lambda b: b.id)
@pytest.mark.parametrize("rung_id", ladder.ORDER)
def test_every_board_stages_at_every_rung(board, rung_id):
    """45 combinations, all of which have to produce a real board."""
    battle = stage.stage(
        board, ladder.get_rung(rung_id), ladder.get_loadout("both")
    )
    sess = soc_engine._hydrate_session(battle.store, battle.session_id)

    from sea_of_colours.game.session import Tile

    for x, y in board.pures:
        cell = sess.grid[y][x]
        assert cell.tile == Tile.RED and cell.purity == 255, (
            f"{board.id}@{rung_id}: the pure at ({x},{y}) did not survive "
            f"staging"
        )

    # And nothing else is a pure. Staging used to paint the declared
    # terrain over the noise generator's output without clearing it, so
    # a "one pure, and it is watched" board could ship four of them —
    # and an agent taking a real, visible pure was scored as ducking the
    # question. A board has to be only the position it claims to be.
    found = {
        (x, y)
        for y in range(sess.height) for x in range(sess.width)
        if sess.grid[y][x].tile == Tile.RED and sess.grid[y][x].purity >= 255
    }
    assert found == set(board.pures), (
        f"{board.id}@{rung_id}: undeclared pure(s) at "
        f"{sorted(found - set(board.pures))}"
    )
    mine = [
        e for e in sess.entities.values()
        if e.entity_type == "harvester" and e.owner == "p1"
    ]
    assert len(mine) == board.harvesters


def test_a_harder_rung_puts_more_on_the_board():
    board = boards.get("two_pures_poker")

    def opposition(rung_id):
        battle = stage.stage(
            board, ladder.get_rung(rung_id), ladder.get_loadout("empty")
        )
        sess = soc_engine._hydrate_session(battle.store, battle.session_id)
        return sum(
            1 for e in sess.entities.values()
            if e.owner != "p1" and e.x is not None
        )

    assert opposition("siege") > opposition("quiet")


# ── the suite runs offline ──────────────────────────────────────────


def test_a_harness_that_never_reached_its_model_is_flagged():
    """Otherwise the league silently ranks a safety net under a team's name.

    An LLM harness that cannot reach its model plays a built-in
    fallback. It says so in its rationale, but nothing consumed that, so
    a credentials outage produced a real-looking score — and could rank
    a broken agent above a working one with nothing in the numbers to
    show it.
    """
    from sea_of_colours.evals.battles.runner import _detect_fallback

    assert _detect_fallback(
        {}, "[plan=[fallback] follow top heuristic chain] [fallback=True:parse]"
    )
    assert _detect_fallback({"fallback": True}, "")
    assert not _detect_fallback({"fallback": False}, "picked SMASH_GRAB")
    assert not _detect_fallback({}, "[plan=SMASH_GRAB] [exec=mover] moves=7")


def test_the_whole_suite_runs_without_credentials():
    """The heuristic needs no PAT, so the kit works on a train.

    This is a usability guarantee as much as a correctness one: if the
    only way to try the suite is to have Snowflake set up first, most
    of a hackathon room never gets to try it.
    """
    from sea_of_colours.evals.battles import runner

    result = runner.run_suite(
        list(boards.BOARDS)[:3],
        [ladder.get_rung("quiet")],
        [ladder.get_loadout("empty")],
        agent="red_harvest",
        runs=1,
    )
    assert len(result.battles) == 3
    errors = [r.error for b in result.battles for r in b.runs if r.error]
    assert not errors, f"suite errored offline: {errors}"
