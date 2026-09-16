"""Efficient tensor-style data export for sea_of_colours grids.

Two views of the same grid are exposed:

- semantic ``(H, W, 2)`` uint8 of ``[tile_id, purity]`` — the game state.
- RGB ``(H, W, 3)`` uint8 — visualization. Red cells encode their purity
  directly in the red channel (``(purity, 0, 0)``), blue cells in the blue
  channel (``(0, 0, purity)``); empty and green use the renderer's palette.

The buffer is built once into a ``bytearray`` (one byte per channel per cell,
row-major) which keeps the pure-Python build cost minimal and avoids any
per-cell object allocation. ``GridTensor.numpy()`` returns a writable
``ndarray`` backed by the same data layout via ``np.frombuffer`` + ``reshape``,
which is the cheapest hand-off you can do without numpy in the generation
pipeline itself.

This module also offers ``save_npy`` / ``save_bin`` for dumping grids to disk
in either the standard ``.npy`` format (when numpy is available) or a small
raw binary container that can be reloaded without numpy.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Tuple, Union

from sea_of_colours.generator import Cell, Grid, Tile
from sea_of_colours.render import TILE_COLORS

try:
    import numpy as _np  # type: ignore
except ImportError:
    _np = None  # type: ignore


@dataclass(frozen=True)
class GridTensor:
    """A ``(height, width, channels)`` uint8 buffer stored row-major.

    Layout: ``data[(y * width + x) * channels + c]``.

    Use :meth:`numpy` to view it as a true ``ndarray`` of shape
    ``(height, width, channels)``. Use :meth:`as_list` when you need a pure
    nested-list view without dragging in numpy.
    """

    height: int
    width: int
    channels: int
    data: bytes

    def __post_init__(self) -> None:
        expected = self.height * self.width * self.channels
        if len(self.data) != expected:
            raise ValueError(
                f"GridTensor data length {len(self.data)} != "
                f"height*width*channels={expected}"
            )

    @property
    def shape(self) -> Tuple[int, ...]:
        if self.channels == 1:
            return (self.height, self.width)
        return (self.height, self.width, self.channels)

    def numpy(self, *, copy: bool = True) -> Any:
        """Return an ``ndarray`` view of ``data``.

        ``copy=True`` (default) returns a writable ndarray. ``copy=False``
        returns a zero-copy read-only ndarray that shares memory with this
        buffer — useful if you only need to read the data once and want to
        avoid the allocation. Requires numpy.
        """
        if _np is None:
            raise ImportError(
                "numpy is required for GridTensor.numpy(); install with "
                "`pip install numpy`."
            )
        arr = _np.frombuffer(self.data, dtype=_np.uint8)
        arr = arr.reshape(*self.shape)
        if copy:
            return arr.copy()
        return arr

    def as_list(self) -> list:
        """Return a nested list of the same shape (slow; for debugging / JSON)."""
        rows = []
        for y in range(self.height):
            row = []
            for x in range(self.width):
                base = (y * self.width + x) * self.channels
                if self.channels == 1:
                    row.append(self.data[base])
                else:
                    row.append(tuple(self.data[base + c] for c in range(self.channels)))
            rows.append(row)
        return rows


def grid_to_semantic(grid: Grid) -> GridTensor:
    """Pack ``grid`` as an ``(H, W, 2)`` uint8 tensor of ``[tile_id, purity]``."""
    if not grid or not grid[0]:
        return GridTensor(0, 0, 2, b"")

    height = len(grid)
    width = len(grid[0])
    buf = bytearray(height * width * 2)

    pos = 0
    for row in grid:
        for cell in row:
            buf[pos] = int(cell.tile)
            p = cell.purity
            if p < 0:
                p = 0
            elif p > 255:
                p = 255
            buf[pos + 1] = p
            pos += 2
    return GridTensor(height, width, 2, bytes(buf))


def grid_to_rgb(grid: Grid) -> GridTensor:
    """Pack ``grid`` as an ``(H, W, 3)`` uint8 tensor of per-cell RGB values.

    Red cells encode ``(purity, 0, 0)`` and blue cells encode ``(0, 0, purity)``
    so each substance's purity is directly readable from its channel. Empty
    and green cells use the renderer's palette
    (:data:`sea_of_colours.render.TILE_COLORS`).
    """
    if not grid or not grid[0]:
        return GridTensor(0, 0, 3, b"")

    height = len(grid)
    width = len(grid[0])
    buf = bytearray(height * width * 3)

    empty = TILE_COLORS[Tile.EMPTY]
    green = TILE_COLORS[Tile.GREEN]

    pos = 0
    for row in grid:
        for cell in row:
            t = cell.tile
            if t == Tile.RED:
                p = cell.purity
                if p < 0:
                    p = 0
                elif p > 255:
                    p = 255
                buf[pos] = p
                buf[pos + 1] = 0
                buf[pos + 2] = 0
            elif t == Tile.BLUE:
                p = cell.purity
                if p < 0:
                    p = 0
                elif p > 255:
                    p = 255
                buf[pos] = 0
                buf[pos + 1] = 0
                buf[pos + 2] = p
            elif t == Tile.EMPTY:
                buf[pos] = empty[0]
                buf[pos + 1] = empty[1]
                buf[pos + 2] = empty[2]
            else:
                buf[pos] = green[0]
                buf[pos + 1] = green[1]
                buf[pos + 2] = green[2]
            pos += 3
    return GridTensor(height, width, 3, bytes(buf))


_BIN_MAGIC = b"SOCV1"


def save_bin(path: Union[str, Path], tensor: GridTensor) -> Path:
    """Write ``tensor`` to a small stdlib-only ``.bin`` container.

    Layout: 5-byte magic ``SOCV1``, then 3 big-endian uint32s (height, width,
    channels), then the raw ``data`` bytes. Reload with :func:`load_bin`.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack(">III", tensor.height, tensor.width, tensor.channels)
    out.write_bytes(_BIN_MAGIC + header + tensor.data)
    return out


def load_bin(path: Union[str, Path]) -> GridTensor:
    """Load a tensor previously written by :func:`save_bin`."""
    blob = Path(path).read_bytes()
    if not blob.startswith(_BIN_MAGIC):
        raise ValueError("not a sea_of_colours .bin tensor (missing magic)")
    h, w, c = struct.unpack(">III", blob[5:17])
    data = blob[17:]
    return GridTensor(h, w, c, data)


def save_npy(path: Union[str, Path], tensor: GridTensor) -> Path:
    """Write ``tensor`` as a standard NumPy ``.npy`` file. Requires numpy."""
    if _np is None:
        raise ImportError(
            "numpy is required for save_npy(); use save_bin() for a stdlib-only "
            "binary container, or install numpy."
        )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    arr = tensor.numpy(copy=False)
    _np.save(out, arr, allow_pickle=False)
    return out
