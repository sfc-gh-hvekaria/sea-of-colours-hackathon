"""Generator invariant (v1.24, RULEBOOK §2.2): pures are plural and far apart.

v1.21 stopped pures *touching*. That left two legal boards that both flatten
the opening:

* **One jackpot.** Nothing to choose between, so the season's first real
  decision — which seam to commit to — never gets asked.
* **Two jackpots four cells apart.** One probe disk lights both, so finding
  either hands you the pair. Same non-decision, dressed up.

v1.24 therefore guarantees ``min_pure_count`` (2) pures at a Chebyshev
distance of at least ``min_pure_separation`` (12). Chebyshev because the
question the metric has to answer is "can one probe see both", and probe
vision is a disk measured in king moves.

The separation is best-effort and the COUNT is the hard floor — see
``test_a_degenerate_board_keeps_the_count``. That asymmetry is deliberate
and these tests pin it in both directions, because the tempting "just retry
the seed until it fits" implementation livelocks on a board whose RED is one
small blob.
"""

from __future__ import annotations

from sea_of_colours.generator import (
    Cell,
    GenerationParams,
    Tile,
    _spread_pure_red,
    generate_grid,
)

# The board /api/game/new actually serves. GenerationParams defaults to
# 80x50; measuring that would overstate how easy the constraint is to meet.
_W, _H = 40, 28

_MIN_COUNT = 2
_MIN_SEP = 12


def _pure_cells(grid):
    return [
        (x, y)
        for y, row in enumerate(grid)
        for x, c in enumerate(row)
        if c.tile == Tile.RED and c.purity == 255
    ]


def _chebyshev(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _closest_pair(cells):
    """Smallest Chebyshev gap between any two cells, or None if fewer than 2."""
    if len(cells) < 2:
        return None
    return min(
        _chebyshev(cells[i], cells[j])
        for i in range(len(cells))
        for j in range(i + 1, len(cells))
    )


def _params(seed, **kw):
    """Params for the spread rule, with v1.29 GRADING off by default.

    Grading (§2.2, :func:`_grade_pure_red`) runs immediately after this rule
    and enriches the ground around each pure, promoting bare cells to RED.
    That is intended behaviour, but it is a *different* layer: leaving it on
    would break the "spread moved nothing but purities" tests below on a
    change those tests are not measuring. Grading has its own module,
    ``test_generator_pure_halo.py``.

    It does not touch the pure set itself — grading can never write 255 —
    so the count and separation tests here read the same either way.
    """
    kw.setdefault("grade_pure_red", False)
    return GenerationParams(width=_W, height=_H, seed=seed, **kw)


def test_every_board_carries_at_least_two_pures():
    """The count floor, over enough seeds to be meaningful."""
    for seed in range(200):
        pures = _pure_cells(generate_grid(_params(seed)))
        assert len(pures) >= _MIN_COUNT, (
            f"seed {seed} has {len(pures)} pure(s) — a board with one jackpot "
            "gives the opening nothing to decide"
        )


def test_no_two_pures_stand_closer_than_the_separation():
    """The distance rule. Subsumes the v1.21 no-touching invariant."""
    for seed in range(200):
        pures = _pure_cells(generate_grid(_params(seed)))
        gap = _closest_pair(pures)
        assert gap is not None and gap >= _MIN_SEP, (
            f"seed {seed} has two pures {gap} apart (need {_MIN_SEP}); "
            f"pures at {pures}"
        )


def test_the_separation_still_forbids_touching():
    """v1.21's rule must survive inside v1.24's, not be replaced by it.

    Stated separately because the two rules live in different functions and
    a future retune of ``min_pure_separation`` to something tiny would
    silently give back the slabs.
    """
    for seed in range(120):
        pures = set(_pure_cells(generate_grid(_params(seed))))
        for (x, y) in pures:
            neighbours = {
                (x + dx, y + dy)
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                if (dx, dy) != (0, 0)
            }
            assert not (neighbours & pures), f"seed {seed}: touching pure at {(x, y)}"


def test_the_rule_bites_on_boards_that_would_otherwise_fail():
    """Guard against the rule quietly becoming a no-op.

    If the raw generator already met the constraint on every seed, every
    test above would pass while measuring nothing. Pin that the un-spread
    path really does produce boards the rule has to correct — both failure
    modes, too few pures AND pures too close.
    """
    too_few = too_close = 0
    for seed in range(200):
        pures = _pure_cells(generate_grid(_params(seed, spread_pure_red=False)))
        if len(pures) < _MIN_COUNT:
            too_few += 1
        gap = _closest_pair(pures)
        if gap is not None and gap < _MIN_SEP:
            too_close += 1
    assert too_few > 0, (
        "no seed produced fewer than two pures without the rule — the count "
        "floor may no longer be testing anything"
    )
    assert too_close > 0, (
        "no seed produced pures closer than the separation without the rule — "
        "the distance guarantee may no longer be testing anything"
    )


def test_the_toggle_restores_the_v121_behaviour():
    """``spread_pure_red=False`` must be a true off switch."""
    off = generate_grid(_params(31, spread_pure_red=False))
    on = generate_grid(_params(31))
    assert _pure_cells(off) != _pure_cells(on) or _closest_pair(
        _pure_cells(off)
    ) in (None, *range(_MIN_SEP, 99)), "seed 31 chosen to exercise the rule"
    # The off path is free to violate both halves; the on path never is.
    assert len(_pure_cells(on)) >= _MIN_COUNT


def test_only_purities_move_and_only_red_cells():
    """Spread may promote and demote, but must not re-terraform the board.

    The demotion band is shared with declustering; the promotion writes 255.
    Nothing may change tile, and no non-RED cell may be touched — that would
    mean the rule is consuming a live RNG stream or writing the wrong cell.
    """
    for seed in range(60):
        before = generate_grid(_params(seed, spread_pure_red=False))
        after = generate_grid(_params(seed))
        for y in range(_H):
            for x in range(_W):
                a, b = before[y][x], after[y][x]
                assert a.tile == b.tile, f"seed {seed}: tile moved at {(x, y)}"
                if a.purity == b.purity:
                    continue
                assert a.tile == Tile.RED, (
                    f"seed {seed}: changed a non-RED cell at {(x, y)}"
                )
                # Either a demotion out of pure, or a promotion up to pure.
                assert (a.purity == 255 and 220 <= b.purity <= 254) or (
                    b.purity == 255
                ), f"seed {seed}: unexpected {a.purity} -> {b.purity} at {(x, y)}"


def test_seed_stability_no_other_layer_moves():
    """The rule draws from a FRESH stream (seed + 5_000).

    RED/GREEN/BLUE take 1_000/2_000/3_000 and declustering 4_000. Reusing a
    live stream would shift tile placement for every seed ever generated;
    this is the test that catches it.
    """
    for seed in range(60):
        before = generate_grid(_params(seed, spread_pure_red=False))
        after = generate_grid(_params(seed))
        assert [[c.tile for c in row] for row in before] == [
            [c.tile for c in row] for row in after
        ], f"seed {seed}: tile layout moved — a shared RNG stream was consumed"


def test_it_is_deterministic_for_a_seed():
    a = generate_grid(_params(99))
    b = generate_grid(_params(99))
    assert [[(c.tile, c.purity) for c in row] for row in a] == [
        [(c.tile, c.purity) for c in row] for row in b
    ]


def test_a_degenerate_board_keeps_the_count():
    """COUNT is the hard floor; SEPARATION is best-effort.

    A board whose RED is one small vein cannot host two pures 12 apart. The
    rule must still deliver two — two contested jackpots close together beat
    one uncontested — and must not spin looking for a placement that does
    not exist. Built by hand rather than by seed, because the generator does
    not produce a board this degenerate.

    The fixture is a 9-long strip with the pure at one end, so the candidate
    cells sit at Chebyshev 1..8 and the "take the furthest available" rule
    has something to actually choose between. A square blob would leave every
    candidate equidistant and the test would pass on a fallback that simply
    grabbed the first cell it saw.
    """
    grid = [[Cell(Tile.EMPTY, 0) for _ in range(_W)] for _ in range(_H)]
    for x in range(5, 14):
        grid[6][x] = Cell(Tile.RED, 200)
    grid[6][5] = Cell(Tile.RED, 255)

    _spread_pure_red(grid, _params(1))

    pures = _pure_cells(grid)
    assert len(pures) == _MIN_COUNT, "the count floor holds on a cramped board"
    gap = _closest_pair(pures)
    assert gap is not None and gap < _MIN_SEP, (
        "this fixture cannot satisfy the separation — the test is only "
        "meaningful if the fallback actually had to fire"
    )
    assert gap == 8, (
        f"the fallback must take the FURTHEST available cell, got {gap} "
        "(the far end of the strip is 8 from the existing pure)"
    )
    assert (13, 6) in pures, "the far end is the only correct choice here"


def test_no_red_at_all_is_a_no_op():
    """A board with no RED gets no pures and does not spin."""
    grid = [[Cell(Tile.EMPTY, 0) for _ in range(_W)] for _ in range(_H)]
    _spread_pure_red(grid, _params(1))
    assert _pure_cells(grid) == []


# ── v1.28: the count is a band, sized by the table ──────────────────
#
# The floor above is the GENERATOR's default of 2 with no draw. v1.28
# sizes the real count at the call site in ``GameSession.new``, which
# passes a ``(low, high)`` band from ``pure_count_range`` and lets the
# generator draw uniformly inside it. So these go through a whole session
# rather than ``generate_grid`` — the plumbing is the thing under test,
# and a generator-level test cannot see it. The dataclass default stays a
# plain floor of 2, so every test above keeps its meaning.
#
# NOTE FOR ANYONE TIGHTENING THESE. The count is deliberately NOT a
# function of the seat count, so `len(pures) >= len(seats)` is the wrong
# assertion however natural it looks: at four Houses the band is 3-6 and
# a board with 3 is correct and intended — a House is allowed to find
# itself with no jackpot to reach. An earlier cut of this file asserted
# `>= n` and passed only because seed 7 happened to draw high.


def _session_pures(seats, seed=7):
    from sea_of_colours.game.session import GameSession

    sess = GameSession.new(_W, _H, seed=seed, players=seats)
    return sess, _pure_cells(sess.grid)


def test_the_pure_count_lands_inside_the_band_for_the_seat_count():
    """Every seat count gets a board inside its own band, still spread out."""
    from sea_of_colours.generator import pure_count_range

    for n in (1, 2, 3, 4):
        lo, hi = pure_count_range(n)
        seats = [f"p{i}" for i in range(1, n + 1)]
        for seed in range(25):
            sess, pures = _session_pures(seats, seed=seed)
            assert len(sess.players) == n, "fixture built the wrong seat count"
            assert len(pures) >= lo, (
                f"{n} seats, seed {seed}: {len(pures)} pure(s), under the "
                f"band floor of {lo} — the floor is the hard guarantee"
            )
            assert len(pures) <= hi, (
                f"{n} seats, seed {seed}: {len(pures)} pure(s), over the band "
                f"ceiling of {hi}. The draw cannot exceed it, so this means "
                f"the terrain itself carried more separated pures than the "
                f"band allows for — the top-up is a floor, it never demotes"
            )
            gap = _closest_pair(pures)
            assert gap is not None and gap >= _MIN_SEP, (
                f"{n} seats, seed {seed}: closest pure pair is {gap} apart, "
                f"under the {_MIN_SEP} separation — one probe disk lights "
                f"both, so raising the count re-created the cluster §2.2 "
                f"exists to remove"
            )


def test_the_count_actually_varies():
    """It is a RANDOM band, not a floor wearing a band's clothes.

    Without this, a draw that always returned ``low`` would satisfy every
    other test in this file — and the whole point of the band is that the
    number of jackpots must not be a tell for the number of seats.
    """
    seats = ["p1", "p2", "p3", "p4"]
    counts = {len(_session_pures(seats, seed=s)[1]) for s in range(40)}
    assert len(counts) > 1, (
        f"40 seeds all produced {counts} jackpots — the count is constant, "
        f"so finding one jackpot still tells a seat how many others exist"
    )


def test_the_draw_is_deterministic_for_a_seed():
    """Same seed, same seats, same board — the draw must not be ambient.

    A bare ``random.randint`` here would reroll the map on every reload of
    a persisted season, which is the one thing a seed exists to prevent.
    """
    seats = ["p1", "p2", "p3"]
    first = _session_pures(seats, seed=11)[1]
    second = _session_pures(seats, seed=11)[1]
    assert first == second


def test_the_band_never_drops_below_two():
    """A solo season still gets a choice to make.

    One jackpot is the original no-decision board, and a single-seat game
    is the one place a naive seat-sized band would produce it.
    """
    from sea_of_colours.generator import pure_count_range

    assert pure_count_range(1)[0] >= 2
    for seed in range(10):
        _, pures = _session_pures(["p1"], seed=seed)
        assert len(pures) >= 2, (
            f"seed {seed}: a 1-seat season fell to a single jackpot"
        )


def test_four_houses_may_be_short_of_one_jackpot_each():
    """Deliberate, and the reason the band is not ``(seats, seats + 2)``.

    Pinned because it looks like an off-by-one and WILL be "corrected" by
    someone reading the table cold. Scarcity at a full table is the point:
    four Houses can be made to fight over three jackpots.
    """
    from sea_of_colours.generator import pure_count_range

    assert pure_count_range(4)[0] == 3


def test_duplicate_seats_do_not_inflate_the_floor():
    """Counted off the NORMALISED seats, not the raw argument.

    ``GameSession.new`` dedupes and clamps ``players`` (v0.9.6, for a UI
    that double-posts a seat). Reading ``len(players)`` instead would ask
    for three jackpots on a two-House board, which is why the seat
    normalisation now runs BEFORE the map is generated.
    """
    dup_sess, dup = _session_pures(["p1", "p1", "p2"])
    _, plain = _session_pures(["p1", "p2"])
    assert len(dup_sess.players) == 2
    assert len(dup) == len(plain), (
        f"a duplicated seat asked for {len(dup)} pures where the same two "
        f"Houses got {len(plain)} — the count read the raw list"
    )


def test_the_default_seat_list_still_gets_the_v124_board():
    """``players=None`` is the legacy 2-seat default and must not move.

    Pins that the reorder did not change the map any existing caller gets:
    identical cell for cell to an explicit two-seat game.
    """
    _, implicit = _session_pures(None)
    _, explicit = _session_pures(["p1", "p2"])
    assert implicit == explicit


# The two below drive ``generate_grid`` with an explicit count rather than
# a seat list, deliberately. Comparing a 2-seat board against a 4-seat one
# stopped being a valid way to isolate the count the moment the count
# became a random draw — the two boards can differ because they drew
# differently, which proves nothing about the top-up. Setting
# ``min_pure_count`` with no ``max_pure_count`` disables the draw, so the
# count is the only variable.


def test_raising_the_count_only_adds_jackpots():
    """The top-up may only ADD pures, never move the ones already there.

    ``_spread_pure_red`` tops up by promotion, so the N+2 board must be the
    N board plus two. If this fails the count is being met by re-running
    the thinning pass with different survivors, which would mean the seat
    count silently re-rolls terrain the seed is supposed to fix.
    """
    for seed in range(15):
        two = _pure_cells(generate_grid(_params(seed, min_pure_count=2)))
        four = _pure_cells(generate_grid(_params(seed, min_pure_count=4)))
        assert set(two) <= set(four), (
            f"seed {seed}: jackpots {sorted(set(two) - set(four))} vanished "
            f"when the count was raised from 2 to 4"
        )


def test_raising_the_count_moves_nothing_but_the_pures():
    """Everything else on the board is identical.

    The count feeds exactly one dial. Any tile change, or a purity change
    on a cell that is not a newly promoted pure, means it reached a shared
    RNG stream — the same failure ``test_the_tile_layout_is_unchanged``
    guards at the generator level.
    """
    a_grid = generate_grid(_params(7, min_pure_count=2))
    b_grid = generate_grid(_params(7, min_pure_count=4))
    added = set(_pure_cells(b_grid)) - set(_pure_cells(a_grid))
    for y in range(_H):
        for x in range(_W):
            a, b = a_grid[y][x], b_grid[y][x]
            assert a.tile == b.tile, f"tile moved at ({x},{y}) — shared RNG"
            if a.purity != b.purity:
                assert (x, y) in added, (
                    f"purity moved at ({x},{y}) ({a.purity} -> {b.purity}) on "
                    f"a cell that is not one of the added jackpots"
                )


def test_the_band_draw_does_not_disturb_the_terrain():
    """A drawn count of N must give the same board as a flat floor of N.

    The draw runs on its own RNG stream (``seed + 6_000``) precisely so
    that it cannot perturb the demote/promote passes. If someone folds it
    into the shared stream to save a line, this catches it: the board
    would still be legal, just silently different from every board the
    same seed produced before.
    """
    for seed in range(12):
        drawn_grid = generate_grid(
            _params(seed, min_pure_count=2, max_pure_count=6)
        )
        n = len(_pure_cells(drawn_grid))
        flat_grid = generate_grid(_params(seed, min_pure_count=n))
        assert drawn_grid == flat_grid, (
            f"seed {seed}: a drawn count of {n} produced a different board "
            f"from a flat floor of {n} — the draw is perturbing a shared "
            f"RNG stream"
        )
