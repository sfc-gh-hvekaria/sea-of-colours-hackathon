#!/usr/bin/env python3
"""Scan seeds for boards with >=2 pure-RED (purity 255) seams in SEPARATE regions.

Terrain is deterministic from ``seed`` (see :mod:`sea_of_colours.generator`), so
we can generate boards locally and inspect the grid without touching Snowflake.

A "pure region" is an 8-connected component of RED cells at purity 255. We report
seeds that have >=2 such components whose centroids are at least ``--min-sep``
cells apart (Chebyshev), so the season actually presents multiple contested
theatres rather than one blob or seed-42's degenerate stacked pair.

Usage:
    python scripts/find_multi_pure_seed.py --start 1 --count 400 --min-sep 12
    python scripts/find_multi_pure_seed.py --seeds 42 7 100   # inspect specific seeds
"""
from __future__ import annotations

import argparse
from typing import List, Tuple

from sea_of_colours.generator import GenerationParams, Tile, generate_grid


def _pure_cells(grid) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for y, row in enumerate(grid):
        for x, cell in enumerate(row):
            if cell.tile == Tile.RED and cell.purity >= 255:
                out.append((x, y))
    return out


def _components(cells: List[Tuple[int, int]]) -> List[List[Tuple[int, int]]]:
    """8-connected components over the given cell set."""
    cellset = set(cells)
    seen = set()
    comps: List[List[Tuple[int, int]]] = []
    for c in cells:
        if c in seen:
            continue
        stack = [c]
        comp: List[Tuple[int, int]] = []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.append(cur)
            cx, cy = cur
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nb = (cx + dx, cy + dy)
                    if nb in cellset and nb not in seen:
                        stack.append(nb)
        comps.append(comp)
    return comps


def _centroid(comp: List[Tuple[int, int]]) -> Tuple[float, float]:
    n = len(comp)
    return (sum(x for x, _ in comp) / n, sum(y for _, y in comp) / n)


def _cheb(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def analyze(seed: int, width: int, height: int, min_size: int):
    grid = generate_grid(GenerationParams(width=width, height=height, seed=seed))
    cells = _pure_cells(grid)
    comps = [c for c in _components(cells) if len(c) >= min_size]
    comps.sort(key=len, reverse=True)
    cents = [_centroid(c) for c in comps]
    min_sep = None
    for i in range(len(cents)):
        for j in range(i + 1, len(cents)):
            d = _cheb(cents[i], cents[j])
            if min_sep is None or d < min_sep:
                min_sep = d
    return comps, cents, min_sep


def _fmt(seed, comps, cents, min_sep) -> str:
    parts = []
    for comp, cent in zip(comps, cents):
        parts.append(f"[n={len(comp):2d} @ ({cent[0]:.0f},{cent[1]:.0f})]")
    sep = f"{min_sep:.0f}" if min_sep is not None else "-"
    return (
        f"seed {seed:4d}: {len(comps)} pure region(s), min_sep={sep}  "
        + " ".join(parts)
    )


def render_map(seed: int, width: int, height: int) -> str:
    """ASCII board: RED purity tiers, with pure (255) cells as ``@``.

    Legend: ``@`` pure(255)  ``#`` mass(>=192)  ``+`` vein(>=96)
            ``.`` trace red   ``b`` blue  ``g`` green  ``·`` empty
    """
    grid = generate_grid(GenerationParams(width=width, height=height, seed=seed))
    lines = [f"seed {seed}  ({width}x{height})  @=pure #=mass +=vein .=trace"]
    header = "    " + "".join(str((x // 10) % 10) for x in range(width))
    lines.append(header)
    lines.append("    " + "".join(str(x % 10) for x in range(width)))
    for y, row in enumerate(grid):
        chars = []
        for cell in row:
            t, p = cell.tile, cell.purity
            if t == Tile.RED:
                chars.append("@" if p >= 255 else "#" if p >= 192 else "+" if p >= 96 else ".")
            elif t == Tile.BLUE:
                chars.append("b")
            elif t == Tile.GREEN:
                chars.append("g")
            else:
                chars.append("\u00b7")
        lines.append(f"{y:3d} " + "".join(chars))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--count", type=int, default=400)
    ap.add_argument("--seeds", type=int, nargs="*", help="inspect specific seeds")
    ap.add_argument("--width", type=int, default=40)
    ap.add_argument("--height", type=int, default=28)
    ap.add_argument(
        "--min-size", type=int, default=1,
        help="min cells in a pure component to count it (1 = any pure cell)",
    )
    ap.add_argument(
        "--min-sep", type=float, default=12.0,
        help="min Chebyshev distance between region centroids to qualify",
    )
    ap.add_argument(
        "--min-regions", type=int, default=2,
        help="min number of qualifying separate pure regions",
    )
    ap.add_argument("--top", type=int, default=25, help="max qualifying seeds to print")
    ap.add_argument("--map", action="store_true", help="also render an ASCII board per --seeds seed")
    args = ap.parse_args()

    if args.seeds:
        for s in args.seeds:
            comps, cents, min_sep = analyze(s, args.width, args.height, args.min_size)
            print(_fmt(s, comps, cents, min_sep))
            if args.map:
                print(render_map(s, args.width, args.height))
                print()
        return

    hits = []
    for seed in range(args.start, args.start + args.count):
        comps, cents, min_sep = analyze(seed, args.width, args.height, args.min_size)
        if len(comps) >= args.min_regions and min_sep is not None and min_sep >= args.min_sep:
            hits.append((seed, comps, cents, min_sep))

    # Rank: more regions first, then more separation, then bigger seams.
    hits.sort(key=lambda h: (len(h[1]), h[3], sum(len(c) for c in h[1])), reverse=True)
    print(
        f"Scanned seeds {args.start}..{args.start + args.count - 1} "
        f"({args.width}x{args.height}); "
        f">= {args.min_regions} pure regions, min_sep >= {args.min_sep:.0f}: "
        f"{len(hits)} hits\n"
    )
    for seed, comps, cents, min_sep in hits[: args.top]:
        print(_fmt(seed, comps, cents, min_sep))


if __name__ == "__main__":
    main()
