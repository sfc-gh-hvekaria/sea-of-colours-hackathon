"""Generator invariant (v1.21, RULEBOOK §2.2): pure cells never touch.

A ``pure`` (255) is the jackpot a redsign broadcasts. Ridge noise handed
them out in slabs — measured over 200 seeds, 98% of boards had at least two
pures 8-adjacent and the worst produced a contiguous 14-cell block — and
since a landing auto-harvests the cell it lands on, that let one seat bank
several jackpots off a single drop with nothing to contest.

These tests pin the rule, its narrowness (only *touching* pures are
thinned), the band a demoted cell lands in, and — the part that would be
expensive to get wrong — that the change did not perturb any other feature
of a given seed's terrain.

**Scope (v1.24).** Everything here runs with ``spread_pure_red=False`` via
:func:`_params`, so it measures the v1.21 layer *alone*. v1.24 widened the
rule to a minimum count and a minimum separation, which necessarily thins
harder than "only touching" — pinning both rules in one module would leave
each test ambiguous about which one it caught. The spread rule has its own
module, ``test_generator_pure_spread.py``.
"""

from __future__ import annotations

from collections import deque

from sea_of_colours.generator import (
    GenerationParams,
    Tile,
    generate_grid,
)

# The board /api/game/new actually serves. GenerationParams DEFAULTS to
# 80x50, which clusters far more heavily — measuring that one overstates the
# problem badly, so every test here pins the played size.
_W, _H = 40, 28

# Seeds whose raw (un-declustered) output puts 3+ pures in one 8-connected
# blob at 40x28, found by scanning 0..499. Only ~7% of seeds cluster at all,
# so a narrow range exercises none of the interesting path. Seed 126 is the
# three-in-a-row a player reported; 476 is the worst seen (8 cells).
_SLAB_SEEDS = (126, 184, 211, 411, 425, 441, 462, 476)
_WORST_SLAB_SEED = 476


def _params(seed, **kw):
    """Params for the tests below, with the v1.24 SPREAD rule off by default.

    Everything in this module predates v1.24 and pins the *narrow* v1.21
    rule — only touching pures are thinned. The spread rule (§2.2, min 2
    pures at least ``min_pure_separation`` apart) deliberately thins harder
    and promotes to a floor, which would mask what these tests measure: a
    "raw" board built with ``decluster_pure_red=False`` still came out
    de-slabbed, because spread had run over it. Tests for the spread rule
    itself live in ``test_generator_pure_spread.py`` and opt back in.
    """
    kw.setdefault("spread_pure_red", False)
    # v1.29 — grading off for the same reason, and it matters more here:
    # it promotes bare cells to RED, so the "no other feature of the terrain
    # moved" tests below would fail on a change that is working as intended.
    kw.setdefault("grade_pure_red", False)
    return GenerationParams(width=_W, height=_H, seed=seed, **kw)


def _pure_cells(grid):
    return [
        (x, y)
        for y, row in enumerate(grid)
        for x, c in enumerate(row)
        if c.tile == Tile.RED and c.purity == 255
    ]


def _touching_pairs(cells):
    """Pure cells that are 8-adjacent to another pure cell."""
    s = set(cells)
    out = []
    for (x, y) in cells:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                if (x + dx, y + dy) in s:
                    out.append(((x, y), (x + dx, y + dy)))
    return out


def _clusters(cells):
    """Sizes of 8-connected groups."""
    todo = set(cells)
    sizes = []
    while todo:
        q = deque([todo.pop()])
        n = 1
        while q:
            x, y = q.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    p = (x + dx, y + dy)
                    if p in todo:
                        todo.discard(p)
                        q.append(p)
                        n += 1
        sizes.append(n)
    return sizes


def test_no_two_pures_are_ever_8_adjacent():
    """The headline rule, across enough seeds to be meaningful."""
    for seed in range(120):
        grid = generate_grid(_params(seed))
        touching = _touching_pairs(_pure_cells(grid))
        assert not touching, (
            f"seed {seed} has {len(touching) // 2} touching pure pair(s), "
            f"e.g. {touching[0]}"
        )


def test_every_pure_cluster_is_a_single_cell():
    """Same rule stated the other way — no group larger than one survives."""
    for seed in range(60):
        grid = generate_grid(_params(seed))
        sizes = _clusters(_pure_cells(grid))
        assert sizes and set(sizes) == {1}, f"seed {seed} cluster sizes {sizes}"


def test_the_raw_generator_really_does_produce_slabs():
    """Guard against the rule silently becoming a no-op.

    If ridge noise stopped clustering pures on its own, every test above
    would pass while testing nothing. Pin that the unfixed path still has
    the defect the rule exists to remove.
    """
    # Clustering is rare on the played board (~7% of seeds), so this needs a
    # wide scan — a 60-seed window finds nothing worse than a pair.
    worst = 0
    for seed in _SLAB_SEEDS:
        raw = generate_grid(
            _params(seed, decluster_pure_red=False)
        )
        sizes = _clusters(_pure_cells(raw))
        worst = max([worst, *sizes])
    assert worst >= 3, (
        f"raw generator's biggest pure cluster was only {worst} cells — "
        "the de-cluster rule may no longer be testing anything"
    )


def test_thinning_only_touches_pures_and_only_demotes_them():
    """Nothing but the surplus pures may change.

    Every differing cell must have been 255 before and must land in the
    demotion band after; tile types and all other purities are untouched.
    """
    for seed in (*range(40), *_SLAB_SEEDS):
        raw = generate_grid(
            _params(seed, decluster_pure_red=False)
        )
        fixed = generate_grid(_params(seed))
        for y in range(_H):
            for x in range(_W):
                a, b = raw[y][x], fixed[y][x]
                assert a.tile == b.tile, f"seed {seed} tile changed at {(x, y)}"
                if a.purity == b.purity:
                    continue
                assert a.purity == 255, (
                    f"seed {seed} changed a non-pure cell at {(x, y)}: "
                    f"{a.purity} -> {b.purity}"
                )
                assert 220 <= b.purity <= 254, (
                    f"seed {seed} demoted {(x, y)} to {b.purity}, "
                    "outside the [220, 254] band"
                )


def test_a_pure_survives_every_cluster_that_had_one():
    """Thinning must not wipe a jackpot off the board entirely."""
    for seed in (*range(40), *_SLAB_SEEDS):
        raw = generate_grid(
            _params(seed, decluster_pure_red=False)
        )
        raw_clusters = len(_clusters(_pure_cells(raw)))
        fixed = generate_grid(_params(seed))
        assert len(_pure_cells(fixed)) == raw_clusters, (
            f"seed {seed}: {raw_clusters} raw cluster(s) should leave "
            f"{raw_clusters} pure(s), got {len(_pure_cells(fixed))}"
        )


def test_pures_that_merely_sit_near_each_other_both_survive():
    """The rule is narrow on purpose: only TOUCHING pures are thinned.

    Two finds a few cells apart are two separate contests and the board
    should keep both.
    """
    seen_multi = False
    for seed in range(60):
        pures = _pure_cells(
            generate_grid(_params(seed))
        )
        if len(pures) > 1:
            seen_multi = True
            break
    assert seen_multi, (
        "no seed kept more than one pure — the rule is thinning too hard"
    )


def test_the_toggle_restores_the_raw_noise_output():
    raw = generate_grid(
        _params(_WORST_SLAB_SEED, decluster_pure_red=False)
    )
    raw_clusters = _clusters(_pure_cells(raw))
    assert max(raw_clusters) == 8, (
        f"expected the unfixed path to still produce the 8-cell slab, "
        f"got clusters {raw_clusters}"
    )
    # Four separate blobs on this seed, so four surviving pures — one each.
    fixed = generate_grid(
        _params(_WORST_SLAB_SEED)
    )
    assert _clusters(_pure_cells(fixed)) == [1] * len(raw_clusters)


def test_the_reported_three_in_a_row_is_gone():
    """The shape a player actually hit: three pures side by side."""
    raw = generate_grid(
        _params(126, decluster_pure_red=False)
    )
    assert 3 in _clusters(_pure_cells(raw))
    fixed = generate_grid(_params(126))
    assert not _touching_pairs(_pure_cells(fixed))


def test_declustering_is_deterministic_for_a_seed():
    a = generate_grid(_params(77))
    b = generate_grid(_params(77))
    assert [[(c.tile, c.purity) for c in row] for row in a] == [
        [(c.tile, c.purity) for c in row] for row in b
    ]


def test_seed_stability_every_other_layer_is_untouched():
    """The demotion draws from a FRESH random stream (seed + 4_000).

    RED/GREEN/BLUE use the 1_000/2_000/3_000 offsets, so taking a new one
    means no existing layer's noise moves. If someone reuses a live stream
    instead, tile placement shifts for every seed ever generated — this is
    the test that catches it.
    """
    for seed in (*range(40), *_SLAB_SEEDS):
        raw = generate_grid(
            _params(seed, decluster_pure_red=False)
        )
        fixed = generate_grid(_params(seed))
        assert [[c.tile for c in row] for row in raw] == [
            [c.tile for c in row] for row in fixed
        ], f"seed {seed}: tile layout moved — a shared RNG stream was consumed"


def test_the_guarantee_promotes_exactly_one_cell():
    """``ensure_pure_red`` used to promote the peak PLUS two neighbours.

    That was the one remaining path deliberately minting a cluster, so it
    would have reintroduced the very thing §2.2 removes.
    """
    pureless = None
    for seed in range(120):
        raw = generate_grid(
            _params(seed, ensure_pure_red=False, decluster_pure_red=False)
        )
        if not _pure_cells(raw):
            pureless = seed
            break
    assert pureless is not None, "no pure-less seed found — widen the scan"

    guarded = generate_grid(
        _params(pureless)
    )
    assert len(_pure_cells(guarded)) == 1, (
        "the fallback guarantee must mint a single pure, not a seam"
    )
