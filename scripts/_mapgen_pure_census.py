#!/usr/bin/env python3
"""How many pure(255) RED cells does a board get, and how clumped are they?

A pure is the jackpot the whole redsign mechanic is built around, so a
seed that hands out a slab of them next to each other is not "lucky", it
is a different game. This measures the distribution before/after any
de-clustering change.

Scratch harness, like the other ``scripts/_*.py`` — not a test.

Usage::

    python scripts/_mapgen_pure_census.py [n_seeds] [width height] [seats]

``seats`` (v1.28) sizes the count the way ``GameSession.new`` does, via
``pure_count_range`` — pass 4 to measure the board a full table gets.

Pass ``--floor N`` instead to hold the count at a flat N with no draw.
That is the mode the RULEBOOK's separation figures come from: the draw
would otherwise mix several counts into one sample, and the question the
separation table answers is "how tight does it get at exactly N pures".

NOTE the size. ``GenerationParams`` defaults to 80x50, but ``/api/game/new``
serves 40x28 — that is the board people actually play, and it clusters far
less than the default does. Measuring the wrong one overstates the problem
by a wide margin.
"""
from __future__ import annotations

import sys
from collections import Counter, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sea_of_colours.generator import (  # noqa: E402
    GenerationParams,
    Tile,
    generate_grid,
    pure_count_range,
)


def pures(grid) -> list[tuple[int, int]]:
    return [
        (x, y)
        for y, row in enumerate(grid)
        for x, c in enumerate(row)
        if c.tile == Tile.RED and c.purity >= 255
    ]


def clumps(cells: list[tuple[int, int]]) -> list[int]:
    """Sizes of 8-connected groups of pure cells."""
    todo = set(cells)
    out: list[int] = []
    while todo:
        seed = todo.pop()
        q, n = deque([seed]), 1
        while q:
            x, y = q.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    p = (x + dx, y + dy)
                    if p in todo:
                        todo.discard(p)
                        q.append(p)
                        n += 1
        out.append(n)
    return sorted(out, reverse=True)


def closest_pair(cells: list[tuple[int, int]]) -> int | None:
    """Smallest CHEBYSHEV gap between any two pures, or None if fewer than 2.

    Chebyshev (king moves) is the metric v1.24's separation rule uses, because
    the question it answers is "can one probe disk cover both jackpots".
    """
    if len(cells) < 2:
        return None
    return min(
        max(abs(a[0] - b[0]), abs(a[1] - b[1]))
        for i, a in enumerate(cells)
        for b in cells[i + 1:]
    )


def main() -> int:
    # v1.28 — the count is a per-seat-count BAND now, so the census has to
    # be told which table it is measuring or it only ever reports the
    # 2-player board. ``--floor N`` pins it flat instead, which is the mode
    # the RULEBOOK's separation figures come from: a drawn count mixes
    # several N into one sample, and the separation question is per-N.
    flat = 0
    argv = list(sys.argv)
    if "--floor" in argv:
        i = argv.index("--floor")
        flat = int(argv[i + 1])
        del argv[i:i + 2]

    n = int(argv[1]) if len(argv) > 1 else 200
    w = int(argv[2]) if len(argv) > 3 else 40
    h = int(argv[3]) if len(argv) > 3 else 28
    seats = int(argv[4]) if len(argv) > 4 else 2
    if flat:
        want, top = flat, None
    else:
        want, top = pure_count_range(seats)
    tot = Counter()
    clump_hist = Counter()
    worst = (0, -1)
    boards_with_a_clump = 0
    pure_counts = []
    gaps: list[int] = []
    lonely = 0          # boards below the count floor
    crowded = (999, -1)  # tightest pair seen, and where

    for seed in range(n):
        grid = generate_grid(GenerationParams(
            width=w, height=h, seed=seed,
            min_pure_count=want, max_pure_count=top,
        ))
        ps = pures(grid)
        pure_counts.append(len(ps))
        cl = clumps(ps)
        for size in cl:
            clump_hist[size] += 1
        tot["pures"] += len(ps)
        if cl and cl[0] > worst[0]:
            worst = (cl[0], seed)
        if any(s > 1 for s in cl):
            boards_with_a_clump += 1
        if len(ps) < want:
            lonely += 1
        gap = closest_pair(ps)
        if gap is not None:
            gaps.append(gap)
            if gap < crowded[0]:
                crowded = (gap, seed)

    pure_counts.sort()
    band = f"flat floor {want}" if top is None else f"seats {seats} -> band {want}-{top}"
    print(f"seeds: {n}   board {w}x{h}   {band}")
    print(f"pures per board: min {pure_counts[0]}  "
          f"median {pure_counts[n // 2]}  "
          f"mean {tot['pures'] / n:.1f}  max {pure_counts[-1]}")
    print(f"boards with 2+ pures touching: {boards_with_a_clump} "
          f"({100.0 * boards_with_a_clump / n:.0f}%)")
    print(f"worst clump: {worst[0]} cells (seed {worst[1]})")
    # v1.24 separation rule (§2.2): >= min_pure_count pures, no pair closer
    # than min_pure_separation Chebyshev cells.
    print(f"\nboards below the floor of {want}: {lonely} "
          f"({100.0 * lonely / n:.0f}%)")
    if gaps:
        gaps.sort()
        print(f"closest pure pair per board (chebyshev): "
              f"min {gaps[0]}  median {gaps[len(gaps) // 2]}  max {gaps[-1]}")
        print(f"tightest pair overall: {crowded[0]} cells (seed {crowded[1]})")
    print("\nclump-size histogram (8-connected groups, all boards):")
    for size in sorted(clump_hist):
        bar = "#" * min(60, clump_hist[size])
        print(f"  {size:3d} cells : {clump_hist[size]:5d} {bar}")

    _deposit_census(n, w, h, want, top)
    return 0


def _tiers(grid) -> tuple[dict[str, int], int]:
    out = {"trace": 0, "vein": 0, "mass": 0, "pure": 0}
    value = 0
    for row in grid:
        for c in row:
            if c.tile != Tile.RED:
                continue
            value += c.purity
            p = c.purity
            key = (
                "pure" if p >= 255
                else "mass" if p >= 151
                else "vein" if p >= 51
                else "trace"
            )
            out[key] += 1
    return out, value


def _deposit_census(n: int, w: int, h: int, want: int, top) -> None:
    """v1.29 — the graded-deposit figures the RULEBOOK §2.2 tables quote.

    Kept here rather than in a throwaway script so the claims stay
    reproducible: grading roughly doubles the RED on a board, which is the
    kind of number someone will want to re-check before trusting a score.
    Measures with grading ON and OFF over the same seeds, so the delta is
    attributable to this pass alone.
    """
    def run(graded: bool):
        tiers = {"trace": [], "vein": [], "mass": [], "pure": []}
        values = []
        attached, deposit, barren = [], [], 0
        for seed in range(n):
            grid = generate_grid(GenerationParams(
                width=w, height=h, seed=seed,
                min_pure_count=want, max_pure_count=top,
                grade_pure_red=graded,
            ))
            t, val = _tiers(grid)
            for k in tiers:
                tiers[k].append(t[k])
            values.append(val)
            cells = {
                (x, y): c
                for y, row in enumerate(grid)
                for x, c in enumerate(row)
            }
            for (px, py) in pures(grid):
                near = far = 0
                for dx in range(-5, 6):
                    for dy in range(-5, 6):
                        c = cells.get((px + dx, py + dy))
                        if c is None or c.tile != Tile.RED:
                            continue
                        if not (151 <= c.purity <= 254):
                            continue
                        d = (dx * dx + dy * dy) ** 0.5
                        if d <= 1.5:
                            near += 1
                        if d <= 4.5:
                            far += 1
                attached.append(near)
                deposit.append(far)
                if far == 0:
                    barren += 1
        med = lambda a: sorted(a)[len(a) // 2]  # noqa: E731
        return tiers, values, attached, deposit, barren, med

    print("\n" + "=" * 62)
    print("v1.29 graded deposits (RULEBOOK 2.2) — same seeds, grading on vs off")
    print("=" * 62)
    for label, graded in (("grading OFF (v1.28)", False), ("grading ON  (v1.29)", True)):
        tiers, values, attached, deposit, barren, med = run(graded)
        print(f"\n{label}")
        print("  squares per board (median): " + "  ".join(
            f"{k} {med(v)}" for k, v in tiers.items()
        ))
        print(f"  map RED value (median): {med(values):,}")
        if deposit:
            print(f"  per jackpot — mass within 1.5: median {med(attached)}"
                  f"   within 4.5: median {med(deposit)}")
            print(f"  jackpots with no mass in the deposit: {barren}/{len(deposit)}"
                  f" ({100.0 * barren / len(deposit):.1f}%)")


if __name__ == "__main__":
    raise SystemExit(main())
