"""Generator invariant: every board carries at least one pure (255) RED cell.

Natural pures only form in thick seam cores, so many seeds produce none. The
``ensure_pure_red`` guard (default ON) deterministically promotes the strongest
RED core to pure. These tests lock that guarantee and prove the guard is
non-destructive (seeds that already have a pure are untouched) and seed-stable.

**Scope (v1.24).** Every board is built with ``spread_pure_red=False`` via
:func:`_params`, so these measure the ``ensure_pure_red`` guard ALONE. The
v1.24 spread rule (§2.2) guarantees two pures in its own right, which would
make a "pureless" baseline impossible to construct and would silently turn
the tests below into tests of the wrong layer. Spread has its own module,
``test_generator_pure_spread.py``.
"""

from __future__ import annotations

from sea_of_colours.generator import (
    GenerationParams,
    Tile,
    generate_grid,
)

# Match the live game board (see GameSession.new / card.txt) rather than the
# 80x50 GenerationParams default.
_W, _H = 40, 28


def _params(seed, **kw):
    """Params with the v1.24 spread rule off — see the module docstring."""
    kw.setdefault("spread_pure_red", False)
    # v1.29 — grading likewise, and for the same reason: it enriches the
    # ground around a pure and promotes bare cells to RED, so a board built
    # with it on is not the raw output these tests measure against.
    kw.setdefault("grade_pure_red", False)
    return GenerationParams(width=_W, height=_H, seed=seed, **kw)


def _pure_cells(grid):
    return [
        (x, y)
        for y, row in enumerate(grid)
        for x, c in enumerate(row)
        if c.tile == Tile.RED and c.purity == 255
    ]


def test_every_seed_has_at_least_one_pure_red():
    for seed in range(60):
        grid = generate_grid(_params(seed))
        assert _pure_cells(grid), f"seed {seed} produced no pure RED"


def test_guard_actually_promotes_on_a_pureless_seed():
    # There must exist at least one seed whose RAW noise output has no pure, so
    # the guard is doing real work (not just riding naturally-pure seeds).
    pureless = None
    for seed in range(60):
        raw = generate_grid(
            _params(seed, ensure_pure_red=False)
        )
        if not _pure_cells(raw):
            pureless = seed
            break
    assert pureless is not None, "no pure-less seed found in range — widen the scan"

    guarded = generate_grid(
        _params(pureless, ensure_pure_red=True)
    )
    assert _pure_cells(guarded), f"guard failed to add a pure on seed {pureless}"


def test_guard_is_noop_when_a_natural_pure_exists():
    # Find a seed that is naturally pure, then confirm the guarded grid is
    # identical cell-for-cell (the guard must not touch an already-pure board).
    natural = None
    for seed in range(60):
        raw = generate_grid(
            _params(seed, ensure_pure_red=False)
        )
        if _pure_cells(raw):
            natural = (seed, raw)
            break
    assert natural is not None, "no naturally-pure seed found in range"
    seed, raw = natural
    guarded = generate_grid(
        _params(seed, ensure_pure_red=True)
    )
    assert [[(c.tile, c.purity) for c in row] for row in guarded] == [
        [(c.tile, c.purity) for c in row] for row in raw
    ]


def test_promotion_is_deterministic():
    # Same seed → same promoted pure set (no RNG in the guard).
    a = generate_grid(_params(123))
    b = generate_grid(_params(123))
    assert sorted(_pure_cells(a)) == sorted(_pure_cells(b))


def test_no_red_board_is_a_noop():
    # A board with no RED at all can't be promoted — must not crash or fabricate.
    grid = generate_grid(
        _params(5, red_coverage=0.0)
    )
    assert not _pure_cells(grid)
