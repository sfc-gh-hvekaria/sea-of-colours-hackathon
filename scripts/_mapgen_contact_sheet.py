#!/usr/bin/env python3
"""Render real generated boards to a PNG contact sheet, to eyeball the mapgen.

Builds whole ``GameSession``s rather than calling ``generate_grid``, so the
boards are exactly what a season of that seat count would deal -- including
the v1.28 pure-count band, which lives at the call site and is invisible to
the generator on its own.

Colours come from ``render.cell_visual``, the same function the ANSI dump
uses, so the sheet cannot drift from the game's own palette.

Scratch harness, like the other ``scripts/_*.py`` -- not a test.

Usage::

    python scripts/_mapgen_contact_sheet.py [out_dir] [n_boards] [seats...]

e.g. ``python scripts/_mapgen_contact_sheet.py ~/Desktop 5 2 3``
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from sea_of_colours.game.session import GameSession  # noqa: E402
from sea_of_colours.snowpark import engine as soc_engine  # noqa: E402
from sea_of_colours.generator import Tile, pure_count_range  # noqa: E402
from sea_of_colours.render import (  # noqa: E402
    BLUE_LEVEL_NAMES,
    RED_LEVEL_NAMES,
    blue_level,
    cell_visual,
    red_level,
)

W, H = 40, 28
CELL = 15
PAD = 14
HEAD = 34
COLS = 2

INK = (214, 226, 220)
DIM = (128, 148, 140)
PAPER = (9, 12, 11)
JACKPOT = (255, 214, 64)


def _font(size: int):
    for p in (
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/Library/Fonts/Courier New.ttf",
    ):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_HEAD = _font(15)
F_SUB = _font(12)


# The dither ramp draws the ORE as a shade-block glyph in the foreground over
# the bare-ground background, so a cell's real colour is fg composited onto bg
# by the glyph's ink coverage. Painting bg alone loses every trace/vein/mass
# cell and renders a board that looks all but empty — which is exactly what
# the first cut of this script produced.
_COVERAGE = {"\u2591": 0.25, "\u2592": 0.50, "\u2593": 0.75, "\u2588": 1.0}


def _flat_rgb(cell) -> tuple:
    fg, bg, glyph = cell_visual(cell)
    if not fg or not glyph:
        return tuple(bg)
    cov = _COVERAGE.get(glyph[0], 0.0)
    if cov <= 0:
        return tuple(bg)
    return tuple(
        round(b + (f - b) * cov) for f, b in zip(fg, bg)
    )


def _pures(grid):
    return [
        (x, y)
        for y, row in enumerate(grid)
        for x, c in enumerate(row)
        if c.tile == Tile.RED and c.purity == 255
    ]


def _census(grid) -> dict:
    """Count squares per tier, using the engine's own tier functions.

    Tier boundaries are single-sourced from ``render`` rather than restated
    here -- they are the same ladder scoring uses (RULEBOOK 4.4), so a copy
    would be one more surface to drift.
    """
    out = {f"red_{n}": 0 for n in RED_LEVEL_NAMES}
    out.update({f"blue_{n}": 0 for n in BLUE_LEVEL_NAMES})
    out["green"] = 0
    out["empty"] = 0
    for row in grid:
        for c in row:
            if c.tile == Tile.RED:
                out[f"red_{RED_LEVEL_NAMES[red_level(c.purity) - 1]}"] += 1
            elif c.tile == Tile.BLUE:
                out[f"blue_{BLUE_LEVEL_NAMES[blue_level(c.purity) - 1]}"] += 1
            elif c.tile == Tile.GREEN:
                out["green"] += 1
            else:
                out["empty"] += 1
    return out


def _print_census(seats: int, rows: list) -> None:
    keys = (
        [f"red_{n}" for n in RED_LEVEL_NAMES]
        + ["green"]
        + [f"blue_{n}" for n in BLUE_LEVEL_NAMES]
        + ["empty"]
    )
    head = ["trace", "vein", "MASS", "pure", "green"]
    head += list(BLUE_LEVEL_NAMES) + ["empty", "red_val"]
    print(f"\n  {seats}-HOUSE — squares per tier (board is {W * H})")
    print("  seed | " + " ".join(f"{h:>7}" for h in head))
    print("  " + "-" * (7 + len(head) * 8))
    for seed, cen, red_val in rows:
        cells = [f"{cen[k]:>7}" for k in keys]
        print(f"  {seed:>4} | " + " ".join(cells) + f" {red_val:>7}")
    n = len(rows)
    avg = [sum(c[k] for _s, c, _v in rows) / n for k in keys]
    print("  " + "-" * (7 + len(head) * 8))
    print(
        "   avg | "
        + " ".join(f"{a:>7.1f}" for a in avg)
        + f" {sum(v for _s, _c, v in rows) / n:>7.0f}"
    )


def _closest(cells):
    if len(cells) < 2:
        return None
    return min(
        max(abs(a[0] - b[0]), abs(a[1] - b[1]))
        for i, a in enumerate(cells)
        for b in cells[i + 1:]
    )


def _draw_board(sheet: Image.Image, grid, ox: int, oy: int) -> None:
    """Paint one board, then ring every jackpot so it reads at a glance."""
    px = Image.new("RGB", (W, H), PAPER)
    load = px.load()
    for y, row in enumerate(grid):
        for x, cell in enumerate(row):
            load[x, y] = _flat_rgb(cell)
    sheet.paste(px.resize((W * CELL, H * CELL), Image.NEAREST), (ox, oy))

    d = ImageDraw.Draw(sheet)
    for (x, y) in _pures(grid):
        # Ring OUTSIDE the cell, so the jackpot's own colour is unobscured.
        x0, y0 = ox + x * CELL - 2, oy + y * CELL - 2
        d.rectangle(
            [x0, y0, x0 + CELL + 3, y0 + CELL + 3], outline=JACKPOT, width=2,
        )
    d.rectangle(
        [ox - 1, oy - 1, ox + W * CELL, oy + H * CELL],
        outline=(52, 64, 58), width=1,
    )


def sheet_for(seats: int, n: int, out_dir: Path) -> Path:
    lo, hi = pure_count_range(seats)
    bw, bh = W * CELL, H * CELL
    rows = (n + COLS - 1) // COLS
    sw = PAD + COLS * (bw + PAD)
    sh = HEAD + PAD + rows * (bh + HEAD + PAD)

    sheet = Image.new("RGB", (sw, sh), PAPER)
    d = ImageDraw.Draw(sheet)
    d.text(
        (PAD, 10),
        f"SEA OF COLOURS  ·  {seats}-HOUSE BOARDS  ·  {W}x{H}  ·  "
        f"jackpot band {lo}-{hi}  ·  rings mark pure RED (255)",
        font=F_HEAD, fill=INK,
    )

    counts = []
    census_rows = []
    for i in range(n):
        sess = GameSession.new(
            W, H, seed=i, players=[f"p{k}" for k in range(1, seats + 1)],
        )
        pures = _pures(sess.grid)
        gap = _closest(pures)
        counts.append(len(pures))
        census_rows.append((
            i,
            _census(sess.grid),
            # The board's headline RED figure, from the same function that
            # feeds the replay header's "RED ON MAP".
            soc_engine.compute_extraction(sess, [1])["map_red_value"],
        ))

        c, r = i % COLS, i // COLS
        ox = PAD + c * (bw + PAD)
        oy = HEAD + PAD + r * (bh + HEAD + PAD)
        d.text(
            (ox, oy - 19),
            f"seed {i}   ·   {len(pures)} jackpots   ·   closest pair "
            f"{gap} cells",
            font=F_SUB, fill=DIM,
        )
        _draw_board(sheet, sess.grid, ox, oy)

    out = out_dir / f"soc_boards_{seats}p.png"
    sheet.save(out)
    print(f"{seats}-house: counts {counts} -> {out}")
    _print_census(seats, census_rows)
    return out


def main() -> int:
    out_dir = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.cwd()
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    seat_counts = [int(a) for a in sys.argv[3:]] or [2, 3]
    out_dir.mkdir(parents=True, exist_ok=True)
    for seats in seat_counts:
        sheet_for(seats, n, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
