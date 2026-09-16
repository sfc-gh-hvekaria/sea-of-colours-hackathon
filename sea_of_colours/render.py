"""ANSI rendering for topography grids.

Red and blue both use **four** purity bands — the same numeric boundaries:

    purity 0–50       → ``░░`` (LIGHT SHADE ×2)
    purity 51–150     → ``▒▒`` (MEDIUM SHADE ×2)
    purity 151–254    → ``▓▓`` (DARK SHADE ×2)
    purity 255 only   → solid tile (100% palette red / blue)

Each dither tier repeats **one** block-element glyph in both slots so there is
no mixed-character seam inside a cell. Glyphs are only ``░ ▒ ▓ █`` (plus
``██`` for void). **Empty** uses full blocks in the void colour; **green**
uses two spaces on solid green.

A 256-color fallback is provided for terminals without true-color support.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from sea_of_colours.generator import Cell, Grid, Tile

RGB = Tuple[int, int, int]

TILE_COLORS: Dict[Tile, RGB] = {
    # Bare ground: near-black, a touch more blue than red/green ("void purple").
    Tile.EMPTY: (14, 11, 22),
    Tile.GREEN: (63, 185, 80),
    Tile.RED: (209, 75, 75),
    Tile.BLUE: (59, 143, 224),
}

TILE_256: Dict[Tile, int] = {
    # Gray ramp, readable stand-in when truecolor is unavailable.
    Tile.EMPTY: 234,
    Tile.GREEN: 35,
    Tile.RED: 167,
    Tile.BLUE: 68,
}

RED_FG: RGB = (255, 0, 0)
RED_SOLID_BG: RGB = (255, 0, 0)

BLUE_FG: RGB = TILE_COLORS[Tile.BLUE]
BLUE_SOLID_BG: RGB = TILE_COLORS[Tile.BLUE]

RESET = "\033[0m"

# Two full blocks per cell — same width convention as red/blue ramps.
_EMPTY_SOLID: str = "\u2588\u2588"

# Ascending lower bounds; _level_for maps purity to 1..len(names).
# Tiers: [0–50], [51–150], [151–254], [255].
RED_LEVEL_CUTOFFS: Tuple[int, int, int, int] = (0, 51, 151, 255)
BLUE_LEVEL_CUTOFFS: Tuple[int, int, int, int] = (0, 51, 151, 255)

RED_LEVEL_NAMES: Tuple[str, str, str, str] = ("trace", "vein", "mass", "pure")

BLUE_LEVEL_NAMES: Tuple[str, str, str, str] = ("shallow", "mid", "sink", "deep")

# Homogeneous pairs only — never mix glyph types in one cell (avoids inner seam).
_SHARED_BLOCK_DITHERS: Tuple[str, str, str] = (
    "\u2591\u2591",
    "\u2592\u2592",
    "\u2593\u2593",
)

RED_BLOCK_RAMP: Tuple[str, str, str] = _SHARED_BLOCK_DITHERS
BLUE_BLOCK_RAMP: Tuple[str, str, str] = _SHARED_BLOCK_DITHERS


def _level_for(purity: int, cutoffs: Tuple[int, ...]) -> int:
    """Map ``purity`` (0-255) onto ``1..len(cutoffs)`` using ascending cutoffs."""
    p = max(0, min(255, int(purity)))
    for i in range(len(cutoffs) - 1, -1, -1):
        if p >= cutoffs[i]:
            return i + 1
    return 1


def red_level(purity: int) -> int:
    """Map ``purity`` (0-255) onto ``1..4`` (``pure`` == 255 only)."""
    return _level_for(purity, RED_LEVEL_CUTOFFS)


def cell_red_level(cell: Cell) -> int:
    """Return the red concentration level of ``cell`` (1-4), or 0 if it isn't RED."""
    if cell.tile != Tile.RED:
        return 0
    return red_level(cell.purity)


def blue_level(purity: int) -> int:
    """Map ``purity`` (0-255) onto ``1..4`` (``deep`` solid == 255 only)."""
    return _level_for(purity, BLUE_LEVEL_CUTOFFS)


def cell_blue_level(cell: Cell) -> int:
    """Return the blue depth level of ``cell`` (1-4), or 0 if it isn't BLUE."""
    if cell.tile != Tile.BLUE:
        return 0
    return blue_level(cell.purity)


def _rgb_to_256_cube(r: int, g: int, b: int) -> int:
    """Snap an RGB triple to the nearest cell of the xterm 6x6x6 color cube."""

    def channel(v: int) -> int:
        return min(5, max(0, (v + 25) // 51))

    return 16 + 36 * channel(r) + 6 * channel(g) + channel(b)


def cell_visual(cell: Cell) -> Tuple[Optional[RGB], RGB, str]:
    """Resolve a ``Cell`` to ``(fg, bg, content)`` for ANSI rendering.

    Top-tier red (``pure``, purity ``255``) and blue (``deep``, ``255``) use
    solid background and spaces. Lower tiers use block pairs from
    :data:`RED_BLOCK_RAMP` / :data:`BLUE_BLOCK_RAMP` over the void colour.

    **Every** returned ``content`` string is exactly **two** code points (two
    monospace columns), e.g. ``"██"``, ``"░░"``, ``"  "`` (green only).
    """
    tile = cell.tile
    if tile == Tile.EMPTY:
        e = TILE_COLORS[Tile.EMPTY]
        return e, e, _EMPTY_SOLID
    if tile == Tile.RED:
        lvl = red_level(cell.purity)
        if lvl == len(RED_LEVEL_NAMES):
            return None, RED_SOLID_BG, "  "
        return RED_FG, TILE_COLORS[Tile.EMPTY], RED_BLOCK_RAMP[lvl - 1]
    if tile == Tile.BLUE:
        lvl = blue_level(cell.purity)
        if lvl == len(BLUE_LEVEL_NAMES):
            return None, BLUE_SOLID_BG, "  "
        return BLUE_FG, TILE_COLORS[Tile.EMPTY], BLUE_BLOCK_RAMP[lvl - 1]
    return None, TILE_COLORS[Tile.GREEN], "  "


def _sgr_for(fg: Optional[RGB], bg: RGB, *, truecolor: bool) -> str:
    if truecolor:
        bg_part = f"48;2;{bg[0]};{bg[1]};{bg[2]}"
    else:
        bg_part = f"48;5;{_rgb_to_256_cube(*bg)}"
    if fg is None:
        return f"\033[{bg_part}m"
    if truecolor:
        fg_part = f"38;2;{fg[0]};{fg[1]};{fg[2]}"
    else:
        fg_part = f"38;5;{_rgb_to_256_cube(*fg)}"
    return f"\033[{bg_part};{fg_part}m"


def to_ansi(grid: Grid, *, truecolor: bool = True) -> str:
    """Render ``grid`` as an ANSI string.

    Adjacent cells sharing the same ``(fg, bg)`` pair share one SGR escape
    sequence for compact output.
    """
    lines = []
    for row in grid:
        parts = []
        last_sgr: Optional[str] = None
        for cell in row:
            fg, bg, content = cell_visual(cell)
            sgr = _sgr_for(fg, bg, truecolor=truecolor)
            if sgr != last_sgr:
                parts.append(sgr)
                last_sgr = sgr
            parts.append(content)
        parts.append(RESET)
        lines.append("".join(parts))
    return "\n".join(lines)


def _tier_ranges(cutoffs: Tuple[int, ...]) -> Tuple[str, ...]:
    """Human-readable purity interval per tier (must match :func:`_level_for`)."""
    n = len(cutoffs)
    out: list[str] = []
    for i in range(n):
        low = cutoffs[i]
        if i + 1 < n:
            high = cutoffs[i + 1] - 1
        else:
            high = 255
        out.append(f"{low:>3}-{high:>3}")
    return tuple(out)


def legend(*, truecolor: bool = True) -> str:
    """Return a multi-line legend with one line per red and blue tier."""

    def swatch(fg: Optional[RGB], bg: RGB, content: str) -> str:
        return _sgr_for(fg, bg, truecolor=truecolor) + content + RESET

    empty_rgb = TILE_COLORS[Tile.EMPTY]
    empty = swatch(empty_rgb, empty_rgb, _EMPTY_SOLID)
    green = swatch(None, TILE_COLORS[Tile.GREEN], "  ")

    red_ranges = _tier_ranges(RED_LEVEL_CUTOFFS)
    blue_ranges = _tier_ranges(BLUE_LEVEL_CUTOFFS)

    red_rows = []
    n_red = len(RED_LEVEL_NAMES)
    for i, name in enumerate(RED_LEVEL_NAMES):
        if i < n_red - 1:
            sw = swatch(RED_FG, TILE_COLORS[Tile.EMPTY], RED_BLOCK_RAMP[i])
        else:
            sw = swatch(None, RED_SOLID_BG, "  ")
        red_rows.append(f"  {sw}  {name:<9} purity {red_ranges[i]}")

    blue_rows = []
    n_blue = len(BLUE_LEVEL_NAMES)
    for i, name in enumerate(BLUE_LEVEL_NAMES):
        if i < n_blue - 1:
            sw = swatch(BLUE_FG, TILE_COLORS[Tile.EMPTY], BLUE_BLOCK_RAMP[i])
        else:
            sw = swatch(None, BLUE_SOLID_BG, "  ")
        blue_rows.append(f"  {sw}  {name:<9} purity {blue_ranges[i]}")

    head = f"{empty} empty    {green} green"
    return (
        head
        + "\n  red concentrations:\n"
        + "\n".join(red_rows)
        + "\n  blue depths:\n"
        + "\n".join(blue_rows)
    )
