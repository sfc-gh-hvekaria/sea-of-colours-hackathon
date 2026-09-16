"""Generator invariant (v1.29, RULEBOOK §2.2): a jackpot sits in real ground.

v1.24/v1.28 guaranteed how MANY pures a board carries and how far apart they
stand, but said nothing about the terrain around them — and measured on the
v1.28 output that terrain was ordinary. The top-up in :func:`_spread_pure_red`
promotes the richest cell that clears the separation, separated ``mass`` is
vanishingly rare (~3 cells on a 40x28 board, some boards none), so it
routinely lifted a cell of purity 80-150 straight to 255. Census over seeds
0-4 at 2 seats: ``mass`` averaged 3.4 squares a board, and the five extra
jackpots minted for a third seat came from cells of purity 115, 149, 183, 80
and 85.

A jackpot with no shoulder is a spike, and it costs the player the one read
prospecting should give them: thickening ground means you are getting warmer.

:func:`_grade_pure_red` therefore lays a ``mass`` core and a ``vein`` shoulder
around every pure. These tests pin the three rules that stop it doing damage
(only ever raises / never touches GREEN or BLUE / never reaches 255), the
stream isolation, and — the part that is easy to get wrong and impossible to
see from a passing assertion — that the result does not look generated.
"""

from __future__ import annotations

import math

from sea_of_colours.game.session import GameSession
from sea_of_colours.generator import (
    Cell,
    GenerationParams,
    Tile,
    generate_grid,
)

_W, _H = 40, 28


def _params(seed, **kw):
    return GenerationParams(width=_W, height=_H, seed=seed, **kw)


def _ungraded(seed, **kw):
    return generate_grid(_params(seed, grade_pure_red=False, **kw))


def _graded(seed, **kw):
    return generate_grid(_params(seed, **kw))


def _cells(grid):
    for y, row in enumerate(grid):
        for x, c in enumerate(row):
            yield x, y, c


def _pures(grid):
    return [(x, y) for x, y, c in _cells(grid) if c.tile == Tile.RED and c.purity == 255]


def _count(grid, pred):
    return sum(1 for _x, _y, c in _cells(grid) if pred(c))


def _mass(grid):
    return _count(grid, lambda c: c.tile == Tile.RED and 151 <= c.purity <= 254)


# ── the point of the change ──────────────────────────────────────────


def _mass_near(grid, px, py, radius):
    return sum(
        1
        for x, y, c in _cells(grid)
        if (x, y) != (px, py)
        and math.hypot(x - px, y - py) <= radius
        and c.tile == Tile.RED
        and 151 <= c.purity <= 254
    )


def test_every_jackpot_gets_a_deposit():
    """The complaint, pinned: a pure must not sit in bare or trace ground.

    Measured on the neighbourhood rather than the board total, so a board
    that happens to be rich elsewhere cannot satisfy it by accident.

    v1.29.1 tuned the shape from a solid mass core to *a few chunks clinging
    to the pure plus a scatter further out*, so the figures here are small
    and that is the intent — measured over 40 seeds, a jackpot carries a
    median of 2 mass cells within r1.5 and 5 across the whole deposit.
    """
    attached, whole = [], []
    for seed in range(60):
        grid = _graded(seed)
        for (px, py) in _pures(grid):
            attached.append(_mass_near(grid, px, py, 1.5))
            whole.append(_mass_near(grid, px, py, 4.5))

    # NOT "every jackpot": measured over 200 seeds, 6 of 599 get no mass at
    # all — a pure hard against the board edge, or one ringed by GREEN and
    # BLUE that rule 2 forbids overwriting. Those are correct outcomes, so
    # this is a RATE. An absolute assertion here passed at 40 seeds and
    # would have failed the first time anyone widened the sample.
    barren = sum(1 for w in whole if w == 0)
    assert barren / len(whole) < 0.03, (
        f"{barren}/{len(whole)} jackpots have no mass in their deposit — "
        "above the rate explained by board edges and GREEN/BLUE crowding"
    )

    attached.sort()
    whole.sort()
    assert attached[len(attached) // 2] >= 2, (
        f"median attached mass {attached[len(attached) // 2]} — too little "
        "clinging to the jackpot itself"
    )
    assert whole[len(whole) // 2] >= 4, (
        f"median deposit mass {whole[len(whole) // 2]} — too thin overall"
    )
    # The other half of the tuning: it must not go back to a solid core.
    assert attached[len(attached) // 2] <= 5, (
        f"median attached mass {attached[len(attached) // 2]} — the core is "
        "filling in solidly again, which is what v1.29.1 tuned away"
    )


def test_the_shoulder_thins_outward():
    """Mass core, then vein, then ordinary ground — not a cliff.

    Averaged over every jackpot on many seeds: a single deposit is ragged by
    design, so this is only meaningful in aggregate.
    """
    rings: dict[int, list[int]] = {1: [], 2: [], 3: [], 4: [], 5: []}
    for seed in range(30):
        grid = _graded(seed)
        for (px, py) in _pures(grid):
            for x, y, c in _cells(grid):
                d = math.hypot(x - px, y - py)
                if (x, y) == (px, py) or d > 5.5:
                    continue
                band = int(math.ceil(d))
                if band in rings:
                    rings[band].append(c.purity if c.tile == Tile.RED else 0)

    avg = {k: sum(v) / len(v) for k, v in rings.items() if v}
    assert avg[1] > avg[2] > avg[3] > avg[4], (
        f"purity must fall off with distance from a jackpot, got {avg}"
    )

    # NOT "ring 1 is mass". Since v1.29.1 only ~1 cell in 3 of the inner
    # ring is mass by design, so a mean above 151 would mean the core had
    # gone solid again. The real claim is that the ground beside a jackpot
    # is dramatically richer than the board at large — that is the read the
    # whole pass exists to give the player. Measured: ring means
    # 140/105/56/35 against a board mean of ~12.5.
    board = [
        c.purity if c.tile == Tile.RED else 0
        for seed in range(10)
        for _x, _y, c in _cells(_graded(seed))
    ]
    background = sum(board) / len(board)
    assert avg[1] > background * 5, (
        f"the ring beside a jackpot ({avg[1]:.0f}) is not meaningfully richer "
        f"than the board at large ({background:.1f})"
    )


def test_grading_lifts_the_mass_tier_off_the_floor():
    """The census that motivated this: mass was ~3 squares a board."""
    before = [_mass(_ungraded(s)) for s in range(20)]
    after = [_mass(_graded(s)) for s in range(20)]
    b_avg, a_avg = sum(before) / 20, sum(after) / 20
    assert b_avg < 8, (
        "the v1.28 baseline is supposed to be mass-starved; if this fails the "
        f"premise of the change has moved (got {b_avg:.1f})"
    )
    # A RATIO, not an absolute floor. The absolute figure is a tuning dial —
    # v1.29.1 cut it by a third on purpose — so pinning it would turn every
    # future density tweak into a test edit and teach the next person to
    # move the number rather than think about it. What must not regress is
    # that grading substantially lifts the tier off the floor.
    assert a_avg > b_avg * 2.5, (
        f"grading added too little mass ({b_avg:.1f} -> {a_avg:.1f})"
    )
    for s, (b, a) in enumerate(zip(before, after)):
        assert a >= b, f"seed {s}: grading REMOVED mass ({b} -> {a})"


# ── the three safety rules ───────────────────────────────────────────


def test_grading_only_ever_raises_purity():
    """Rule 1. Grading may enrich, never flatten."""
    for seed in range(40):
        before, after = _ungraded(seed), _graded(seed)
        for y in range(_H):
            for x in range(_W):
                a, b = before[y][x], after[y][x]
                if a.tile == Tile.RED and b.tile == Tile.RED:
                    assert b.purity >= a.purity, (
                        f"seed {seed} ({x},{y}): grading demoted RED "
                        f"{a.purity} -> {b.purity}"
                    )


def test_grading_never_touches_green_or_blue():
    """Rule 2. Another colour is a deliberate feature, not ours to overwrite.

    Asserted cell by cell, not as a total — equal counts would also be
    satisfied by grading eating a GREEN and the generator gaining one
    somewhere else.
    """
    for seed in range(40):
        before, after = _ungraded(seed), _graded(seed)
        for y in range(_H):
            for x in range(_W):
                a, b = before[y][x], after[y][x]
                if a.tile in (Tile.GREEN, Tile.BLUE):
                    assert b.tile == a.tile and b.purity == a.purity, (
                        f"seed {seed} ({x},{y}): grading overwrote {a.tile.name}"
                    )


def test_grading_never_mints_a_pure():
    """Rule 3, and the one with teeth.

    A halo cell reaching 255 would sit adjacent to the jackpot it came from —
    exactly the touching pair :func:`_decluster_pure_red` exists to remove,
    and it would break the separation guarantee too. The pure set must come
    out of grading byte-identical.
    """
    for seed in range(60):
        assert _pures(_ungraded(seed)) == _pures(_graded(seed)), (
            f"seed {seed}: the pure set moved — grading must not write 255"
        )


def test_grading_only_claims_bare_ground():
    """The only tile change permitted is EMPTY -> RED (growing the seam)."""
    for seed in range(40):
        before, after = _ungraded(seed), _graded(seed)
        for y in range(_H):
            for x in range(_W):
                a, b = before[y][x], after[y][x]
                if a.tile != b.tile:
                    assert a.tile == Tile.EMPTY and b.tile == Tile.RED, (
                        f"seed {seed} ({x},{y}): {a.tile.name} -> {b.tile.name}"
                    )


def test_grading_stays_near_its_jackpot():
    """Nothing may change far from a pure — a stray write is a bug, not a look."""
    for seed in range(40):
        before, after = _ungraded(seed), _graded(seed)
        pures = _pures(after)
        # Derived, not written down: the shipped vein radius at its fattest
        # lobe, plus half a cell. A literal here silently went stale the
        # moment v1.29.1 widened the reach from 3.5 to 4.2.
        _d = GenerationParams(width=_W, height=_H, seed=0)
        limit = _d.pure_vein_radius * (1.0 + _d.pure_halo_lobe) + 0.5
        for y in range(_H):
            for x in range(_W):
                if before[y][x] == after[y][x]:
                    continue
                assert any(math.hypot(x - px, y - py) <= limit for px, py in pures), (
                    f"seed {seed} ({x},{y}) changed but is nowhere near a jackpot"
                )


# ── determinism and isolation ────────────────────────────────────────


def test_the_toggle_is_a_true_off_switch():
    for seed in range(10):
        assert _ungraded(seed) != _graded(seed), f"seed {seed}: grading did nothing"
    off_a, off_b = _ungraded(3), _ungraded(3)
    assert off_a == off_b


def test_the_draw_is_deterministic_for_a_seed():
    for seed in (0, 7, 41):
        assert _graded(seed) == _graded(seed), f"seed {seed} is not reproducible"


def test_grading_does_not_disturb_the_other_layers():
    """It draws off its OWN stream (seed + 7_000).

    RED/GREEN/BLUE take 1_000/2_000/3_000, decluster 4_000, spread 5_000 and
    the count draw 6_000. Reusing a live stream would move terrain on every
    seed ever generated. GREEN and BLUE placement is the tell, since grading
    is forbidden from touching those cells at all.
    """
    for seed in range(40):
        before, after = _ungraded(seed), _graded(seed)
        for tile in (Tile.GREEN, Tile.BLUE):
            b = [(x, y, c.purity) for x, y, c in _cells(before) if c.tile == tile]
            a = [(x, y, c.purity) for x, y, c in _cells(after) if c.tile == tile]
            assert a == b, f"seed {seed}: {tile.name} moved when grading ran"


# ── it must not LOOK generated ───────────────────────────────────────


def _halo_roundness(**kw):
    """Mean min/max reach by octant over the cells GRADING CHANGED.

    Measuring the changed set and not the finished board is the whole point.
    The first version of this test scored the RED around each pure, which is
    dominated by natural terrain — a deliberate perfect disc scored 0.42
    against a 0.82 threshold, so the test passed while measuring nothing.
    """
    ratios = []
    for seed in range(30):
        before, after = _ungraded(seed, **kw), _graded(seed, **kw)
        changed = [
            (x, y)
            for y in range(_H)
            for x in range(_W)
            if before[y][x] != after[y][x]
        ]
        for (px, py) in _pures(after):
            reach: dict[int, float] = {}
            for (x, y) in changed:
                d = math.hypot(x - px, y - py)
                if d > 6:
                    continue
                oct_ = int(
                    (math.atan2(y - py, x - px) + math.tau)
                    % math.tau
                    / (math.tau / 8)
                )
                reach[oct_] = max(reach.get(oct_, 0.0), d)
            if len(reach) == 8:
                ratios.append(min(reach.values()) / max(reach.values()))
    assert ratios, "no deposits measured"
    return sum(ratios) / len(ratios)


def test_deposits_are_not_circles():
    """A correct halo that reads as a bullseye is worse than none at all.

    It would advertise the jackpot's position from across the board, which is
    the opposite of what fog is for.

    THRESHOLD IS CALIBRATED, not guessed — measured over seeds 0-29:
    shipped 0.40, lobe disabled 0.54, lobe disabled AND bare-ground bias
    removed (i.e. a true disc) 0.63. The second assertion below re-measures
    the disc every run, so this cannot decay into a test that passes on any
    shape.
    """
    assert _halo_roundness() < 0.48, (
        "deposits are too round — the halo will read as a generated bullseye"
    )


def test_the_roundness_check_can_actually_fail():
    """Guard on the guard: a deliberate disc must trip the threshold above."""
    disc = _halo_roundness(pure_halo_lobe=0.0, pure_halo_bare_bias=1.0)
    assert disc >= 0.48, (
        f"a lobe-less, seam-blind halo scored {disc:.2f} — the roundness "
        "threshold no longer discriminates, so the test above is vacuous"
    )


def _shoulder_gaps(**kw):
    """Poor cells left standing in the shoulder ring, over 30 boards."""
    gaps = 0
    for seed in range(30):
        grid = _graded(seed, **kw)
        for (px, py) in _pures(grid):
            for x, y, c in _cells(grid):
                d = math.hypot(x - px, y - py)
                if 1.5 < d <= 3.0 and (c.tile != Tile.RED or c.purity < 60):
                    gaps += 1
    return gaps


def test_deposit_edges_are_broken_not_solid():
    """The outline must be ragged, and this has to be an A/B to mean anything.

    A bare count of gaps is NOT a test of raggedness: measured over 30
    boards, shipping scores 575, a seam-blind disc 372 — and grading turned
    OFF ENTIRELY scores 1064, the highest of the three, because a deposit
    that does not exist is all gap. An absolute threshold would therefore
    have passed most loudly on the feature being absent.

    So compare like with like: our deposits must leave more of the shoulder
    unclaimed than a lobe-less, seam-blind one would. The tests above
    already prove a deposit is there at all, which is the other half.
    """
    ours = _shoulder_gaps()
    disc = _shoulder_gaps(pure_halo_lobe=0.0, pure_halo_bare_bias=1.0)
    assert ours > disc * 1.15, (
        f"deposits are filling in too solidly (gaps {ours} vs a disc's "
        f"{disc}) — the edges will read as a clean boundary"
    )


# ── the escape hatch ─────────────────────────────────────────────────


def _seats_grid(monkeypatch, seed, value=None):
    if value is None:
        monkeypatch.delenv("SOC_MAP_HALO", raising=False)
    else:
        monkeypatch.setenv("SOC_MAP_HALO", value)
    return GameSession.new(_W, _H, seed=seed, players=["p1", "p2"]).grid


def test_soc_map_halo_off_restores_the_v128_board_exactly(monkeypatch):
    """The revert must be a REVERT, not an approximation.

    Grading roughly doubles the RED on a board, so it needs an escape hatch
    that an operator can pull without a code edit. The claim that makes it
    safe is that the pass runs last on its own RNG stream, so switching it
    off yields byte-identical pre-v1.29 terrain — this is the test of that
    claim, over whole ``GameSession`` boards rather than raw generator
    calls, because the seat-count band is applied at that call site.
    """
    for seed in (0, 5, 23):
        off = _seats_grid(monkeypatch, seed, "off")
        zero = _seats_grid(monkeypatch, seed, "0")
        assert off == zero, "'off' and '0' must mean the same thing"

        monkeypatch.delenv("SOC_MAP_HALO", raising=False)
        params = GenerationParams(
            width=_W,
            height=_H,
            seed=seed,
            min_pure_count=2,
            max_pure_count=4,
            grade_pure_red=False,
        )
        assert off == generate_grid(params), (
            f"seed {seed}: SOC_MAP_HALO=off did not reproduce the v1.28 board"
        )


def test_soc_map_halo_thins_the_mass_without_moving_the_pures(monkeypatch):
    """A fraction is a density dial, not a different map."""
    full = [_mass(_seats_grid(monkeypatch, s, None)) for s in range(12)]
    half = [_mass(_seats_grid(monkeypatch, s, "0.4")) for s in range(12)]
    assert sum(half) < sum(full), f"0.4 did not thin the mass ({half} vs {full})"

    for seed in range(6):
        a = _pures(_seats_grid(monkeypatch, seed, None))
        b = _pures(_seats_grid(monkeypatch, seed, "0.4"))
        assert a == b, f"seed {seed}: the halo dial moved the jackpots"


def test_a_nonsense_halo_value_falls_back_to_shipped(monkeypatch):
    """Map generation is a bad place to raise on a typo'd env var."""
    assert _seats_grid(monkeypatch, 4, "banana") == _seats_grid(monkeypatch, 4, None)


# ── the guard against the premise going stale ────────────────────────


def test_the_baseline_really_was_spiky():
    """Without grading, a jackpot's neighbourhood is mostly poor ground.

    If this ever passes trivially, the terrain generator has changed and the
    grading pass may no longer be earning its place.
    """
    spiky = 0
    total = 0
    for seed in range(30):
        grid = _ungraded(seed)
        for (px, py) in _pures(grid):
            total += 1
            near = [
                c
                for x, y, c in _cells(grid)
                if math.hypot(x - px, y - py) <= 2.0 and (x, y) != (px, py)
            ]
            if sum(1 for c in near if c.tile == Tile.RED and c.purity >= 151) < 3:
                spiky += 1
    assert total and spiky / total > 0.6, (
        f"only {spiky}/{total} ungraded jackpots were spikes — the premise of "
        "the grading pass has moved"
    )
