"""Minimal stdlib-only PNG writer for ``Grid`` outputs.

Red and blue use the **same** four purity bands as :mod:`sea_of_colours.render`:

    0–50, 51–150, 151–254, and **255 only** for a solid tile.

Tiers 1–3 get Bayer-dithered overlays (thresholds tuned to mirror ``░░`` /
``▒▒`` / ``▓▓`` in the terminal); tier 4 is a flat colour fill.

PNG is a chunked format: an 8-byte signature, an IHDR chunk describing the
image, one or more IDAT chunks holding the zlib-compressed pixel data with a
1-byte filter prefix per scanline, and a terminating IEND chunk. We only
need 8-bit RGB (color type 2), no interlace, no filtering.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Dict, Tuple, Union

from sea_of_colours.generator import Cell, Grid, Tile
from sea_of_colours.render import (
    BLUE_FG,
    BLUE_SOLID_BG,
    RED_FG,
    RED_SOLID_BG,
    TILE_COLORS,
    cell_blue_level,
    cell_red_level,
)

RGB = Tuple[int, int, int]


_BAYER_4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)


_RED_FILL_THRESHOLDS: Dict[int, int] = {
    1: 2,
    2: 8,
    3: 14,
}

_BLUE_FILL_THRESHOLDS: Dict[int, int] = {
    1: 2,
    2: 8,
    3: 14,
}


_PURE_RED_LEVEL = 4
_DEEP_BLUE_LEVEL = 4


def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _write_png_bytes(width: int, height: int, pixels: bytes) -> bytes:
    """Serialize a raw RGB buffer (``width*height*3`` bytes) into a PNG file."""
    if len(pixels) != width * height * 3:
        raise ValueError("pixel buffer size does not match width*height*3")

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride : (y + 1) * stride]

    idat = zlib.compress(bytes(raw), level=6)

    return signature + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _fill_block(
    buf: bytearray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    width: int,
    height: int,
    color: RGB,
) -> None:
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(width, x1)
    y1 = min(height, y1)
    r, g, b = color
    for y in range(y0, y1):
        row_offset = y * width * 3
        for x in range(x0, x1):
            idx = row_offset + x * 3
            buf[idx] = r
            buf[idx + 1] = g
            buf[idx + 2] = b


def _base_rgb(cell: Cell) -> RGB:
    """Solid background color for a tile before any concentration pattern is drawn."""
    if cell.tile == Tile.RED:
        if cell_red_level(cell) == _PURE_RED_LEVEL:
            return RED_SOLID_BG
        return TILE_COLORS[Tile.EMPTY]
    if cell.tile == Tile.BLUE:
        if cell_blue_level(cell) == _DEEP_BLUE_LEVEL:
            return BLUE_SOLID_BG
        return TILE_COLORS[Tile.EMPTY]
    return TILE_COLORS[cell.tile]


def _dither_tile(
    buf: bytearray,
    x0: int,
    y0: int,
    pixel_size: int,
    img_width: int,
    threshold: int,
    color: RGB,
) -> None:
    """Overlay ``color`` onto the tile at (x0, y0) at the Bayer density given by ``threshold``.

    Bayer phase uses **global** pixel coordinates (``gx``, ``gy``) so the pattern
    continues across tile seams. Tile-local phase caused a visible vertical
    \"reset\" between neighbouring cells at the same tier.
    """
    r, g, b = color
    for py in range(pixel_size):
        gy = y0 + py
        row_bayer = _BAYER_4[gy % 4]
        row_offset = gy * img_width * 3
        for px in range(pixel_size):
            gx = x0 + px
            if row_bayer[gx % 4] < threshold:
                idx = row_offset + gx * 3
                buf[idx] = r
                buf[idx + 1] = g
                buf[idx + 2] = b


def grid_to_pixels(
    grid: Grid,
    *,
    pixel_size: int = 12,
    grid_lines: bool = False,
    grid_color: RGB = (15, 15, 18),
) -> Tuple[int, int, bytes]:
    """Rasterize ``grid`` to a flat RGB byte buffer.

    Red tiles at levels 1–3 get a Bayer-dithered overlay of pure-red pixels
    at the matching density; level 4 fills the tile with solid red. Blue tiles
    follow the same pattern in palette blue.
    """
    if pixel_size < 1:
        raise ValueError("pixel_size must be >= 1")
    if not grid or not grid[0]:
        raise ValueError("grid must be non-empty")

    rows = len(grid)
    cols = len(grid[0])
    width = cols * pixel_size
    height = rows * pixel_size

    buf = bytearray(width * height * 3)

    for ty in range(rows):
        y0 = ty * pixel_size
        y1 = y0 + pixel_size
        for tx in range(cols):
            cell = grid[ty][tx]
            x0 = tx * pixel_size
            x1 = x0 + pixel_size

            base = _base_rgb(cell)
            _fill_block(buf, x0, y0, x1, y1, width, height, base)

            if cell.tile == Tile.RED:
                lvl = cell_red_level(cell)
                if 1 <= lvl < _PURE_RED_LEVEL:
                    _dither_tile(
                        buf, x0, y0, pixel_size, width,
                        _RED_FILL_THRESHOLDS[lvl], RED_FG,
                    )
            elif cell.tile == Tile.BLUE:
                lvl = cell_blue_level(cell)
                if 1 <= lvl < _DEEP_BLUE_LEVEL:
                    _dither_tile(
                        buf, x0, y0, pixel_size, width,
                        _BLUE_FILL_THRESHOLDS[lvl], BLUE_FG,
                    )

            if grid_lines and pixel_size > 1:
                _fill_block(buf, x1 - 1, y0, x1, y1, width, height, grid_color)
                _fill_block(buf, x0, y1 - 1, x1, y1, width, height, grid_color)

    return width, height, bytes(buf)


def encode_png(
    grid: Grid,
    *,
    pixel_size: int = 12,
    grid_lines: bool = False,
    grid_color: RGB = (15, 15, 18),
) -> Tuple[int, int, bytes]:
    """Rasterize ``grid`` and return ``(width_px, height_px, png_bytes)``.

    Same pipeline as :func:`save_png`, without touching the filesystem — useful
    for APIs and tests.
    """
    width, height, pixels = grid_to_pixels(
        grid,
        pixel_size=pixel_size,
        grid_lines=grid_lines,
        grid_color=grid_color,
    )
    return width, height, _write_png_bytes(width, height, pixels)


def save_png(
    path: Union[str, Path],
    grid: Grid,
    *,
    pixel_size: int = 12,
    grid_lines: bool = False,
) -> Path:
    """Write ``grid`` as a PNG at ``path`` and return the resolved ``Path``."""
    _, _, blob = encode_png(
        grid,
        pixel_size=pixel_size,
        grid_lines=grid_lines,
    )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    return out
