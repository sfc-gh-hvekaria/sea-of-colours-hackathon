"""Pure-Python value noise and fractal Brownian motion (fBm).

These helpers return 2D fields as ``list[list[float]]`` with values normalized
to roughly ``[0, 1]``. They use only the standard library so the package has no
runtime dependencies.
"""

from __future__ import annotations

import random
from typing import List

Field = List[List[float]]


def _smoothstep(t: float) -> float:
    """Hermite smoothstep ``3t^2 - 2t^3`` for nicer interpolation than linear."""
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def value_noise_2d(width: int, height: int, scale: float, seed: int) -> Field:
    """Generate a 2D value-noise field of size ``width x height``.

    ``scale`` is the lattice spacing in pixels: larger values produce smoother,
    blobbier noise; smaller values produce finer detail. A lattice of random
    values is bilinearly interpolated with smoothstep easing to fill the grid.
    """
    if scale <= 0:
        raise ValueError("scale must be positive")

    rng = random.Random(seed)

    lat_w = int(width / scale) + 2
    lat_h = int(height / scale) + 2

    lattice = [[rng.random() for _ in range(lat_w)] for _ in range(lat_h)]

    field: Field = [[0.0] * width for _ in range(height)]
    for y in range(height):
        gy = y / scale
        y0 = int(gy)
        ty = _smoothstep(gy - y0)
        for x in range(width):
            gx = x / scale
            x0 = int(gx)
            tx = _smoothstep(gx - x0)

            v00 = lattice[y0][x0]
            v10 = lattice[y0][x0 + 1]
            v01 = lattice[y0 + 1][x0]
            v11 = lattice[y0 + 1][x0 + 1]

            top = _lerp(v00, v10, tx)
            bot = _lerp(v01, v11, tx)
            field[y][x] = _lerp(top, bot, ty)
    return field


def fbm_2d(
    width: int,
    height: int,
    base_scale: float,
    octaves: int = 4,
    persistence: float = 0.5,
    lacunarity: float = 2.0,
    seed: int = 0,
) -> Field:
    """Sum several octaves of value noise into a fractal Brownian motion field.

    Each octave halves its amplitude (``persistence``) and doubles its
    frequency (``lacunarity``) by default. The result is renormalized to
    ``[0, 1]`` so downstream thresholds are easy to reason about.
    """
    if octaves < 1:
        raise ValueError("octaves must be >= 1")

    field: Field = [[0.0] * width for _ in range(height)]
    amplitude = 1.0
    scale = base_scale
    total_amp = 0.0

    for i in range(octaves):
        octave = value_noise_2d(width, height, scale, seed + i * 9973)
        for y in range(height):
            row = field[y]
            orow = octave[y]
            for x in range(width):
                row[x] += orow[x] * amplitude
        total_amp += amplitude
        amplitude *= persistence
        scale = max(1.0, scale / lacunarity)

    lo = min(min(row) for row in field)
    hi = max(max(row) for row in field)
    span = hi - lo if hi > lo else 1.0
    for y in range(height):
        row = field[y]
        for x in range(width):
            row[x] = (row[x] - lo) / span
    return field


def ridge_transform(field: Field) -> Field:
    """Turn a noise field into ridge-like values via ``1 - |2n - 1|``.

    Useful for making mountain ranges read as ridges rather than round blobs.
    The output is re-normalized to ``[0, 1]``.
    """
    out: Field = [[1.0 - abs(2.0 * v - 1.0) for v in row] for row in field]
    lo = min(min(row) for row in out)
    hi = max(max(row) for row in out)
    span = hi - lo if hi > lo else 1.0
    for y in range(len(out)):
        row = out[y]
        for x in range(len(row)):
            row[x] = (row[x] - lo) / span
    return out
