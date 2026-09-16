"""Turn noise fields and band masks into a ``Grid`` of biome tiles.

The compositing order matches the brief: red mountains first, then green
diagonal bands (which overwrite red so the bands stay clean), then blue
dotted concentrations on top of everything.

Every cell carries a ``purity`` value in ``[0, 255]`` alongside its ``tile``
type, but red and blue compute it from very different shapes:

* **Red is a seam.** Cells are chosen with the same thresholded ridge noise as
  before; **purity** is fixed only after ``GREEN`` and ``BLUE`` are painted, from
  (a) **Manhattan depth** — how many steps a red cell sits from the *visible*
  red/unred boundary — and (b) the stored ridge coordinate ``t``, shaped as a
  blend of ``t ** red_gamma`` and a linear ``t`` term (``red_ridge_linear``) so
  typical ``t`` values are not all crushed into trace; thicker cores can reach
  **mass** and, when depth and ridge both cooperate, occasional **pure** (``255``).
  One-pixel-wide seams never reach ``255``.
* **Blue is a pocket.** A Chebyshev distance transform picks the geometric
  center of each connected blue blob (the dist-transform peak), and
  every other cell is graded by its Chebyshev distance *from that peak*.
  The peak cell reaches purity ``255`` (solid ``deep``); outer cells fall
  into lower bands. ``blue_gamma`` is the falloff exponent (``1.0`` = linear).
* **Green is a band** (v0.8.0). The generator rolls 1–3 same-orientation
  bands (snap-to-8 angle set: 0, 22.5°, 45°, 67.5°, 90°, 112.5°, 135°,
  157.5°), spaces their centers along the perpendicular axis, and
  paints cells whose ``smoothstep(half_width, 0, perp_distance) *
  noise`` exceeds the threshold. Pre-v0.8.0 ``band_depth`` is honoured
  as a fallback for the legacy polar-bands look when ``green_band_count_choices``
  resolves to an empty tuple.

Green still defaults to full purity (``255``) until it gets its own tier
rules.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Tuple

from sea_of_colours.noise import Field, fbm_2d, ridge_transform


class Tile(IntEnum):
    EMPTY = 0
    GREEN = 1
    RED = 2
    BLUE = 3


@dataclass(slots=True)
class Cell:
    """A single grid cell: which biome it belongs to and how pure that biome is.

    ``purity`` is a saturation/intensity scalar in ``[0, 255]``. For RED cells
    this is interpreted as the red channel value (so 0 ≈ black, 255 = pure
    red). EMPTY cells use purity 0; other tiles default to 255 until their
    own purity rules are added.
    """

    tile: Tile
    purity: int = 0


Grid = List[List[Cell]]


# Snap-to-8 orientation set for the v0.8.0 green bands. Picking one of
# eight discrete angles keeps the bands looking crisp on a low-res
# grid; freely sampled angles produce aliasing under integer cell math.
GREEN_BAND_ORIENTATIONS: Tuple[float, ...] = tuple(
    i * math.pi / 8.0 for i in range(8)
)
"""Snap-to-8: 0°, 22.5°, 45°, 67.5°, 90°, 112.5°, 135°, 157.5°."""

GREEN_BAND_COUNT_CHOICES: Tuple[int, ...] = (1, 2, 3)
GREEN_BAND_HALF_WIDTH: float = 1.5
GREEN_BAND_JITTER: float = 0.1


@dataclass
class GenerationParams:
    """Knobs exposed to the CLI for tuning the topography."""

    width: int = 80
    height: int = 50
    seed: int = 0

    green_strength: float = 1.0
    #: DEPRECATED (v0.8.0). Pre-band-gen knob — controlled the half-depth
    #: of the polar mask as a fraction of height. Retained so existing
    #: CLI / config callers don't crash; the new generator ignores it.
    band_depth: float = 0.18
    #: Discrete count choices for the diagonal green bands. The
    #: generator rolls one of these uniformly per session.
    green_band_count_choices: Tuple[int, ...] = field(
        default_factory=lambda: GREEN_BAND_COUNT_CHOICES,
    )
    #: Half-width of each band in cell units. ``1.5`` → ~3 cells thick.
    green_band_half_width: float = GREEN_BAND_HALF_WIDTH
    #: Jitter applied to each band center as a fraction of the per-band
    #: spacing. ``0.1`` = bands can shift ±10% off their ideal slot.
    green_band_jitter: float = GREEN_BAND_JITTER
    #: Discrete orientation set the generator picks from.
    green_band_orientations: Tuple[float, ...] = field(
        default_factory=lambda: GREEN_BAND_ORIENTATIONS,
    )

    red_coverage: float = 0.30
    ridges: bool = True
    red_gamma: float = 5.0
    #: Manhattan steps from visible red boundary at which depth credit saturates.
    red_depth_ref: float = 3.0
    #: Blend ``ridge = (1-f)*t**gamma + f*t`` (``f`` in ``[0, 1]``). A nonzero
    #: ``f`` lifts mid-range ``t`` so seam interiors reach vein/mass more often.
    red_ridge_linear: float = 0.28
    #: Added to ``t ** red_gamma`` in thick cells so cores can hit 255 without
    #: requiring ``t == 1`` (rare in floating noise). Only applied for
    #: ``d >= red_pure_min_depth``.
    red_core_boost: float = 0.35
    #: Minimum depth (inclusive) before purity may be solid ``pure`` (255).
    red_pure_min_depth: int = 3

    blue_density: float = 0.03
    blue_smooth_iters: int = 1
    blue_gamma: float = 1.0
    #: Minimum purity for ANY generated BLUE cell. The depth gradient is
    #: remapped into ``[blue_min_purity, 255]`` instead of ``[0, 255]``
    #: so pocket-edge cells are still meaningfully fissile rather than
    #: worthless purity-0 tiles that look blue but fund nothing. Set to
    #: 0 to restore the legacy "edges fade to nothing" behaviour.
    blue_min_purity: int = 40

    #: Guarantee at least one pure (255) RED cell per generation. Natural pures
    #: only form in thick seam cores (depth >= ``red_pure_min_depth`` with the
    #: ridge saturated), so many seeds produce none — leaving a board with no
    #: redsign to contest. When True, :func:`generate_grid` deterministically
    #: promotes the strongest RED core to pure. Set False to restore the raw
    #: noise output.
    ensure_pure_red: bool = True

    #: v1.21 — thin touching pures down to one per cluster. A pure is the
    #: jackpot the whole redsign mechanic is built around, so a seed that
    #: hands out a contiguous slab of them (measured: up to 14 cells, and
    #: 98% of boards had at least a touching pair) is not a lucky board,
    #: it is a different game — one seat lands once and banks the lot.
    #: See :func:`_decluster_pure_red`. Set False for the raw noise output.
    decluster_pure_red: bool = True

    #: Purity band a demoted pure lands in. Top of the ``mass`` tier
    #: ([151..254]), so a thinned cluster still reads as a rich core worth
    #: combing — it just stops being a second jackpot.
    pure_demote_min: int = 220
    pure_demote_max: int = 254

    #: v1.24 — every board carries at least :attr:`min_pure_count` pures, and
    #: no two of them stand closer than :attr:`min_pure_separation`. The
    #: v1.21 rule only forbade *touching*, which left two legal shapes that
    #: both spoil the contest: a board with a single jackpot (nothing to
    #: choose between) and two jackpots four cells apart (one probe disk sees
    #: both, so the "which seam do I commit to" decision never happens).
    #: See :func:`_spread_pure_red`. Set False for the v1.21 behaviour.
    spread_pure_red: bool = True

    #: Minimum pures guaranteed on a board. Two, so there is always a choice.
    #:
    #: v1.28 — the *default* is two, but a real season sizes the count to the
    #: table and draws at random between this and :attr:`max_pure_count`:
    #: ``GameSession.new`` passes ``pure_count_range(len(seat_ids))``. The
    #: seat count is applied there rather than here because the generator has
    #: no notion of seats, and giving these fields a seat-shaped default
    #: would change the meaning of every standalone call. Do not read this
    #: default as "a board has two pures" — ask the caller.
    min_pure_count: int = 2

    #: Upper end of the pure count, inclusive. ``None`` (the default) means
    #: *no draw*: :attr:`min_pure_count` is a plain floor and the board gets
    #: whatever the terrain yields above it, which is the v1.24 behaviour
    #: every standalone caller and generator test still expects.
    #:
    #: v1.28 — set it and the count becomes a uniform random draw from
    #: ``[min_pure_count, max_pure_count]``, so the number of jackpots is not
    #: a tell for the number of seats. The draw is a hard floor, not a cap:
    #: a board whose terrain naturally carries more separated pures than the
    #: draw keeps them, because demoting a legitimate jackpot to hit a
    #: target is destroying real terrain to satisfy a statistic.
    max_pure_count: Optional[int] = None

    #: Minimum CHEBYSHEV distance between any two pures. 12 is three probe
    #: radii at the default r4, so no single probe — and no plausible pair —
    #: covers both. Best-effort: a board whose RED cannot host two cells this
    #: far apart takes the furthest pair available rather than dropping to one.
    min_pure_separation: int = 12

    #: v1.29 — grade the ground around each pure: a ``mass`` core, then a
    #: ``vein`` shoulder, then back to whatever was there. Measured on the
    #: v1.28 output, a jackpot was usually an isolated 255 in ordinary
    #: terrain — the top-up promotes the best *separated* RED, and separated
    #: mass barely exists (~3 cells a board), so it routinely lifted a cell
    #: of purity 80-150 straight to pure. That reads as a spike, not a
    #: deposit, and it breaks the read a player should be able to make:
    #: richer ground means you are getting warmer.
    #: See :func:`_grade_pure_red`. Set False for the v1.28 output.
    grade_pure_red: bool = True

    #: EUCLIDEAN radii of the graded ground, in cells. Euclidean and not
    #: Chebyshev deliberately — a Chebyshev ring is a literal square, which
    #: on a square grid renders as a bullseye and announces itself as
    #: generated. Both sit well inside :attr:`min_pure_separation`, so two
    #: jackpots' haloes cannot merge into one rich region and undo the
    #: separation rule's whole purpose.
    #:
    #: v1.29.1 — the mass radius is now where mass is *likeliest*, not a
    #: solid core; the reach is wider so the deposit trails off further.
    pure_mass_radius: float = 1.5
    pure_vein_radius: float = 4.2

    #: Chance a graded cell comes out ``mass`` rather than ``vein``, at the
    #: jackpot's shoulder and out at the fringe respectively. The first cut
    #: made every cell inside :attr:`pure_mass_radius` mass, which read as a
    #: solid bright core — too much, and too neat. Making it a probability
    #: that decays with distance gives a few chunks clinging to the pure and
    #: a scatter of others further out, which is what an ore body looks
    #: like. The fringe value never reaches zero, so mass keeps appearing
    #: right to the edge of the deposit instead of stopping on a line.
    pure_mass_core_chance: float = 0.32
    pure_mass_fringe_chance: float = 0.06

    #: Purity bands for the graded ground. The mass ceiling is deliberately
    #: below 255: grading must never mint a second pure, least of all one
    #: adjacent to the first, which is the exact shape
    #: :func:`_decluster_pure_red` exists to remove.
    pure_mass_min: int = 165
    pure_mass_max: int = 240
    pure_vein_min: int = 60
    pure_vein_max: int = 150

    #: How far the graded radius is allowed to wander with angle, as a
    #: fraction. A deposit drawn at a constant radius is a circle with a
    #: radial gradient, and on a square grid that reads instantly as
    #: generated — the eye finds the centre before the player does. Each
    #: jackpot gets its own random phase, so no two haloes are the same
    #: shape.
    pure_halo_lobe: float = 0.34

    #: How much less willing grading is to claim BARE ground than to enrich
    #: RED that is already there. Below 1.0 the deposit grows along the seam
    #: it belongs to and only spills into the void where it has to, which is
    #: what stops a jackpot in sparse terrain from painting a perfect disc
    #: onto empty black.
    pure_halo_bare_bias: float = 0.5


#: v1.28 — how many jackpots a board carries, by seat count (RULEBOOK §2.2).
#: Inclusive ``(low, high)``; the generator draws uniformly between them.
#:
#: A flat 2 was right for a duel and wrong for a full table — four Houses
#: sharing two jackpots means two of them have nothing to contest. But a
#: floor pinned exactly to the seat count is worse in the other direction:
#: the count becomes a constant, and a constant is a tell. Every seat can
#: then infer "one jackpot per House, so N-1 are out there" the moment they
#: find their first, which turns the season's opening question — how much of
#: this board is worth fighting for — into arithmetic.
#:
#: So it is a band. Note the four-seat low of 3 is deliberate and NOT a
#: rounding of "one per House": at a full table it is allowed to come up
#: short, so a House can find itself with no jackpot to reach. That is the
#: scarcity the range exists to create.
PURE_COUNT_BY_SEATS: dict[int, Tuple[int, int]] = {
    1: (2, 3),
    2: (2, 4),
    3: (3, 5),
    4: (3, 6),
}


def pure_count_range(seats: int) -> Tuple[int, int]:
    """Inclusive ``(low, high)`` pure count for a season of ``seats`` Houses.

    RULEBOOK §2.2. Outside the table the band is extrapolated and clamped:
    never below 2 (one jackpot gives the opening nothing to choose between)
    and never above 6, which is where the 12-cell separation stops being
    reliably satisfiable on a 40x28 board. Measured over 200 seeds: counts
    up to 6 never put two pures closer than 9, and an r4 probe spans 8, so
    "no single probe lights two jackpots" survives the whole range. At 7 the
    closest pair reaches 8 and that stops being true — hence the ceiling.
    ``MAX_SEATS`` is 4, so the extrapolation is a guard, not a live path.
    """
    n = max(1, int(seats))
    if n in PURE_COUNT_BY_SEATS:
        return PURE_COUNT_BY_SEATS[n]
    return (max(2, min(3, n)), max(2, min(6, n + 2)))


def _percentile_threshold(field: Field, top_fraction: float) -> float:
    """Return the value above which ``top_fraction`` of cells lie."""
    if top_fraction <= 0.0:
        return float("inf")
    if top_fraction >= 1.0:
        return float("-inf")
    flat = [v for row in field for v in row]
    flat.sort()
    idx = int(len(flat) * (1.0 - top_fraction))
    idx = max(0, min(len(flat) - 1, idx))
    return flat[idx]


def _smoothstep(edge: float, distance: float) -> float:
    """Smoothstep falloff from 1 at ``distance == 0`` to 0 at
    ``distance >= edge``. Standard cubic Hermite ``3t^2 - 2t^3`` on
    ``t = 1 - d/edge``.
    """
    if edge <= 0.0:
        return 1.0 if distance <= 0.0 else 0.0
    t = 1.0 - max(0.0, distance) / edge
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def _green_band_mask(params: GenerationParams) -> Field:
    """Build the per-cell mask for v0.8.0 diagonal green bands.

    Rolls 1–3 same-orientation bands at one of the snap-to-8 angles,
    spaces their centers along the perpendicular axis with small
    jitter, and returns a ``[0..1]`` mask whose value is the largest
    smoothstep falloff to any band's center line.

    Reproducibility: the RNG seed is derived from ``params.seed`` so
    a fixed seed always rolls the same bands.
    """
    width = params.width
    height = params.height
    half_w = max(0.5, float(params.green_band_half_width))
    jitter_max = max(0.0, float(params.green_band_jitter))
    band_count_choices = tuple(params.green_band_count_choices) or (1, 2, 3)
    orientations = tuple(params.green_band_orientations) or (0.0,)

    # Dedicated child RNG so green band selection doesn't perturb the
    # RED / BLUE noise seeds (which use ``params.seed + N_000`` already).
    rng = random.Random(params.seed * 7919 + 1009)
    band_count = rng.choice(band_count_choices)
    theta = rng.choice(orientations)
    sin_t = math.sin(theta)
    cos_t = math.cos(theta)

    # Perpendicular-axis projection range: corners give the extrema.
    corners = [
        0.0 * sin_t - 0.0 * cos_t,
        (width - 1) * sin_t - 0.0 * cos_t,
        0.0 * sin_t - (height - 1) * cos_t,
        (width - 1) * sin_t - (height - 1) * cos_t,
    ]
    perp_min, perp_max = min(corners), max(corners)
    span = max(1e-6, perp_max - perp_min)
    # Evenly-spaced band centers along the perpendicular axis,
    # inset slightly from the edges so a single band doesn't grow
    # against the map border.
    centers: List[float] = []
    for i in range(band_count):
        base = perp_min + span * (i + 0.5) / band_count
        jitter = rng.uniform(-jitter_max, jitter_max) * (span / band_count)
        centers.append(base + jitter)

    mask: Field = [[0.0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            perp = x * sin_t - y * cos_t
            best = 0.0
            for c in centers:
                d = abs(perp - c)
                m = _smoothstep(half_w, d)
                if m > best:
                    best = m
            mask[y][x] = best
    return mask


def _red_edge_manhattan_depth(grid: Grid, width: int, height: int) -> List[List[int]]:
    """Minimum 4-neighbour steps from each RED cell to a non-RED cell.

    Used as a thickness proxy on the **final** map so polar bands and blue
    pockets reshape seam topology before purity is assigned.
    """
    inf = width + height + 100
    dist: List[List[int]] = [[inf] * width for _ in range(height)]
    q: List[tuple[int, int]] = []

    for y in range(height):
        for x in range(width):
            if grid[y][x].tile != Tile.RED:
                continue
            is_edge = False
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny, nx = y + dy, x + dx
                if not (0 <= ny < height and 0 <= nx < width):
                    is_edge = True
                    break
                if grid[ny][nx].tile != Tile.RED:
                    is_edge = True
                    break
            if is_edge:
                dist[y][x] = 1
                q.append((y, x))

    head = 0
    while head < len(q):
        y, x = q[head]
        head += 1
        d = dist[y][x]
        nd = d + 1
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = y + dy, x + dx
            if not (0 <= ny < height and 0 <= nx < width):
                continue
            if grid[ny][nx].tile != Tile.RED:
                continue
            if nd < dist[ny][nx]:
                dist[ny][nx] = nd
                q.append((ny, nx))
    return dist


def _finalize_red_purity(
    grid: Grid,
    ridge_t: List[List[float]],
    params: GenerationParams,
) -> None:
    """Set RED purity from ridge noise × visible seam thickness (depth)."""
    width = params.width
    height = params.height
    gamma = max(0.1, params.red_gamma)
    depth_ref = max(0.5, params.red_depth_ref)
    pure_floor = max(1, int(params.red_pure_min_depth))
    boost_max = max(0.0, float(params.red_core_boost))
    lin = max(0.0, min(1.0, float(params.red_ridge_linear)))

    dist = _red_edge_manhattan_depth(grid, width, height)
    inf = width + height + 100

    for y in range(height):
        for x in range(width):
            if grid[y][x].tile != Tile.RED:
                continue
            d = dist[y][x]
            if d >= inf:
                d = 1 + min(x, y, width - 1 - x, height - 1 - y)
            depth_factor = min(1.0, float(d) / depth_ref)
            t = ridge_t[y][x]
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            curved = t**gamma
            ridge = (1.0 - lin) * curved + lin * t
            boost = 0.0
            if d >= pure_floor and boost_max > 0.0:
                denom = max(1e-6, depth_ref - float(pure_floor) + 1.0)
                boost = boost_max * min(
                    1.0, float(d - pure_floor + 1) / denom
                )
            combined = min(1.0, ridge + boost)
            raw = 255.0 * depth_factor * combined
            p = int(round(raw))
            if p < 0:
                p = 0
            elif p > 255:
                p = 255
            if d < pure_floor:
                p = min(p, 254)
            grid[y][x] = Cell(Tile.RED, p)


def _apply_red(
    grid: Grid,
    params: GenerationParams,
    ridge_t: List[List[float]],
) -> None:
    """Paint RED cells and record normalized ridge coordinate ``t`` per cell.

    Purity is assigned later by :func:`_finalize_red_purity` so it respects
    topology after ``GREEN`` / ``BLUE`` overpaint.
    """
    if params.red_coverage <= 0.0:
        return

    base_scale = max(8.0, min(params.width, params.height) / 4.0)
    field = fbm_2d(
        params.width,
        params.height,
        base_scale=base_scale,
        octaves=4,
        persistence=0.5,
        lacunarity=2.0,
        seed=params.seed + 1_000,
    )
    if params.ridges:
        field = ridge_transform(field)

    threshold = _percentile_threshold(field, params.red_coverage)
    span = max(1e-6, 1.0 - threshold)

    for y in range(params.height):
        row = grid[y]
        frow = field[y]
        trow = ridge_t[y]
        for x in range(params.width):
            val = frow[x]
            if val >= threshold:
                t = (val - threshold) / span
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                trow[x] = t
                row[x] = Cell(Tile.RED, 0)


def _apply_green(grid: Grid, params: GenerationParams) -> None:
    """Paint diagonal bands by combining a band mask with fBm noise.

    v0.8.0 — replaces the polar ``_edge_mask`` with
    :func:`_green_band_mask`. The fBm noise layer and the
    ``threshold`` rule are unchanged so a tuned ``green_strength``
    still controls overall coverage.
    """
    if params.green_strength <= 0.0:
        return
    if not params.green_band_count_choices:
        return

    base_scale = max(4.0, params.width / 12.0)
    noise = fbm_2d(
        params.width,
        params.height,
        base_scale=base_scale,
        octaves=3,
        persistence=0.5,
        lacunarity=2.0,
        seed=params.seed + 2_000,
    )
    mask = _green_band_mask(params)

    threshold = 0.35 / max(0.01, params.green_strength)
    for y in range(params.height):
        row = grid[y]
        nrow = noise[y]
        mrow = mask[y]
        for x in range(params.width):
            if mrow[x] * nrow[x] >= threshold:
                row[x] = Cell(Tile.GREEN, 255)


def _smooth_blue(grid: Grid, width: int, height: int, iters: int) -> None:
    """Cellular-automata smoothing: keep BLUE cells with >= 2 BLUE neighbors."""
    for _ in range(iters):
        snapshot = [[c.tile for c in row] for row in grid]
        for y in range(height):
            for x in range(width):
                if snapshot[y][x] != Tile.BLUE:
                    continue
                neighbors = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < width and 0 <= ny < height:
                            if snapshot[ny][nx] == Tile.BLUE:
                                neighbors += 1
                if neighbors < 2:
                    grid[y][x] = Cell(Tile.EMPTY, 0)


def _blue_distance_field(grid: Grid, width: int, height: int) -> List[List[int]]:
    """Chebyshev distance from each BLUE cell to the nearest non-BLUE or boundary.

    Computed with the standard two-pass distance-transform sweep: forward
    pass touches only previously visited neighbors, backward pass closes the
    other half. Non-blue cells keep distance ``0``; blue cells get ``>= 1``.
    The grid boundary counts as non-blue, so cells on the edge of the map
    can never sit deeper than ``1``.
    """
    inf = width + height + 1
    dist: List[List[int]] = [
        [inf if grid[y][x].tile == Tile.BLUE else 0 for x in range(width)]
        for y in range(height)
    ]

    for y in range(height):
        for x in range(width):
            if dist[y][x] == 0:
                continue
            best = dist[y][x]
            for dy, dx in ((-1, -1), (-1, 0), (-1, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < height and 0 <= nx < width:
                    candidate = dist[ny][nx] + 1
                    if candidate < best:
                        best = candidate
                else:
                    if 1 < best:
                        best = 1
            dist[y][x] = best

    for y in range(height - 1, -1, -1):
        for x in range(width - 1, -1, -1):
            if dist[y][x] == 0:
                continue
            best = dist[y][x]
            for dy, dx in ((1, 1), (1, 0), (1, -1), (0, 1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < height and 0 <= nx < width:
                    candidate = dist[ny][nx] + 1
                    if candidate < best:
                        best = candidate
                else:
                    if 1 < best:
                        best = 1
            dist[y][x] = best

    return dist


def _apply_blue(grid: Grid, params: GenerationParams) -> None:
    """Sprinkle dotted concentrations and compute per-pocket water depth.

    Two stages, deliberately split so the blob *shapes* and the *depths*
    don't fight each other:

    1. Noise-threshold + optional CA smoothing decides which cells are
       BLUE.
    2. Walk each connected component once: the cell with the highest
       distance-transform value is the pocket's peak (its geometric
       center), and every other cell's purity is graded by its Chebyshev
       distance *from that single peak*. The peak reaches purity ``255``
       (solid ``deep``); other cells spread across the lower purity bands
       defined in :mod:`sea_of_colours.render`.

    The falloff exponent ``blue_gamma`` controls how quickly purity drops
    with distance from the peak (1.0 = linear; >1 grows the shallow edge;
    <1 keeps more cells near deep).
    """
    if params.blue_density <= 0.0:
        return

    width = params.width
    height = params.height

    base_scale = max(4.0, width / 16.0)
    field = fbm_2d(
        width,
        height,
        base_scale=base_scale,
        octaves=3,
        persistence=0.5,
        lacunarity=2.0,
        seed=params.seed + 3_000,
    )
    threshold = _percentile_threshold(field, params.blue_density)
    for y in range(height):
        row = grid[y]
        frow = field[y]
        for x in range(width):
            if frow[x] >= threshold:
                row[x] = Cell(Tile.BLUE, 0)

    if params.blue_smooth_iters > 0:
        _smooth_blue(grid, width, height, params.blue_smooth_iters)

    dist = _blue_distance_field(grid, width, height)
    gamma = max(0.1, params.blue_gamma)
    visited: List[List[bool]] = [[False] * width for _ in range(height)]

    for y0 in range(height):
        for x0 in range(width):
            if visited[y0][x0] or grid[y0][x0].tile != Tile.BLUE:
                continue

            stack = [(y0, x0)]
            comp: List[tuple] = []
            peak_y, peak_x = y0, x0
            peak_d = dist[y0][x0]
            while stack:
                y, x = stack.pop()
                if visited[y][x]:
                    continue
                visited[y][x] = True
                comp.append((y, x))
                if dist[y][x] > peak_d:
                    peak_d = dist[y][x]
                    peak_y, peak_x = y, x
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < height and 0 <= nx < width:
                            if (
                                not visited[ny][nx]
                                and grid[ny][nx].tile == Tile.BLUE
                            ):
                                stack.append((ny, nx))

            ref = max(2, peak_d + 1)
            # Remap the depth gradient into ``[floor, 255]`` so even the
            # shallowest pocket-edge cell carries usable fissile value
            # (a purity-0 BLUE tile renders blue but funds nothing — a
            # trap, RULEBOOK §2.4).
            floor = max(0, min(255, int(params.blue_min_purity)))
            span = 255.0 - float(floor)
            for cy, cx in comp:
                cheb = max(abs(cy - peak_y), abs(cx - peak_x))
                t = cheb / ref
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                purity = int(floor + ((1.0 - t) ** gamma) * span)
                if purity < floor:
                    purity = floor
                elif purity > 255:
                    purity = 255
                grid[cy][cx] = Cell(Tile.BLUE, purity)


def _pure_clusters(
    grid: Grid, width: int, height: int
) -> List[List[Tuple[int, int]]]:
    """8-connected groups of pure(255) RED cells, in row-major discovery order."""
    seen = [[False] * width for _ in range(height)]
    out: List[List[Tuple[int, int]]] = []
    for y in range(height):
        for x in range(width):
            c = grid[y][x]
            if seen[y][x] or c.tile != Tile.RED or c.purity < 255:
                continue
            stack = [(y, x)]
            seen[y][x] = True
            group: List[Tuple[int, int]] = []
            while stack:
                cy, cx = stack.pop()
                group.append((cy, cx))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if not (0 <= ny < height and 0 <= nx < width):
                            continue
                        if seen[ny][nx]:
                            continue
                        n = grid[ny][nx]
                        if n.tile == Tile.RED and n.purity >= 255:
                            seen[ny][nx] = True
                            stack.append((ny, nx))
            out.append(group)
    return out


def _decluster_pure_red(grid: Grid, params: GenerationParams) -> None:
    """Leave one pure per 8-connected cluster; demote the rest to high mass.

    RULEBOOK §2.2. A pure(255) is the jackpot a redsign broadcasts, and the
    contest for it is the game's centrepiece. Raw ridge noise does not
    respect that: a thick seam core saturates over a whole patch, so pures
    arrive in slabs. One landing auto-harvests the cell it lands on, so a
    slab is a single seat banking several jackpots off one drop with no
    contest at all.

    The survivor is the cell nearest the cluster's centroid (ties row-major),
    which reads as the seam's core staying pure while its shoulders drop a
    tier. Demoted cells land in ``[pure_demote_min, pure_demote_max]`` — top
    of ``mass``, so the ground is still worth combing and the seam keeps its
    shape; it just stops paying out twice.

    Deliberately narrow: only *touching* pures are thinned. Two pures a few
    cells apart are two separate finds and both survive.

    RNG comes off a FRESH ``random.Random(seed + 4_000)`` (RED uses 1_000,
    GREEN 2_000, BLUE 3_000) so existing layers' streams are untouched and
    old seeds still reproduce their terrain.
    """
    width, height = params.width, params.height
    lo = max(0, min(255, int(params.pure_demote_min)))
    hi = max(lo, min(255, int(params.pure_demote_max)))
    rng = random.Random(params.seed + 4_000)

    for group in _pure_clusters(grid, width, height):
        if len(group) < 2:
            continue
        cy = sum(p[0] for p in group) / len(group)
        cx = sum(p[1] for p in group) / len(group)
        keep = min(
            group,
            key=lambda p: ((p[0] - cy) ** 2 + (p[1] - cx) ** 2, p[0], p[1]),
        )
        # Sorted so the RNG is consumed in a stable order regardless of the
        # flood fill's traversal.
        for y, x in sorted(group):
            if (y, x) == keep:
                continue
            grid[y][x] = Cell(Tile.RED, rng.randint(lo, hi))


def _chebyshev(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    """King-move distance. The metric that answers "can one probe see both?"."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _spread_pure_red(grid: Grid, params: GenerationParams) -> None:
    """Guarantee ``min_pure_count`` pures, none closer than ``min_pure_separation``.

    RULEBOOK §2.2. :func:`_decluster_pure_red` (v1.21) only stopped pures
    *touching*, which still permitted the two shapes that flatten the opening:
    a board with exactly one jackpot, where there is nothing to choose between;
    and two jackpots a few cells apart, where one probe disk lights both and
    the "which seam do I commit to" decision never happens. This widens the
    rule to a real separation, and puts a floor under the count.

    Two passes, both deterministic:

    1. **Thin.** Walk pures row-major, keeping one whenever it clears
       ``min_pure_separation`` from every pure already kept; demote the rest
       into ``[pure_demote_min, pure_demote_max]``, exactly as declustering
       does. Subsumes the touching rule (distance 1 is far below 12), so the
       v1.21 invariant still holds with this enabled.
    2. **Top up.** While short of the target count, promote the best
       remaining RED cell to 255 — preferring any cell that clears the
       separation, richest first, ties row-major.

    The target is ``min_pure_count``, or — when ``max_pure_count`` is set
    (v1.28) — a uniform draw from that inclusive range, so a board does not
    advertise the seat count by how many jackpots it carries. The draw comes
    off its OWN stream, ``random.Random(seed + 6_000)``, deliberately: pass 1
    demotes in row-major order and pass 2 promotes with no RNG at all, so
    drawing separately means a board that lands on count N is byte-identical
    to a board whose floor was a flat N, and raising the count only ever
    *adds* jackpots to the same terrain.

    **Best-effort separation, hard floor on count.** If no cell clears the
    distance (a board whose RED is one small blob), the top-up takes the
    candidate that is *furthest* from the existing pures rather than giving
    up: two contested jackpots close together still beats one uncontested.
    The count is the guarantee; the distance is the strong preference. A
    board with no RED at all gets nothing, and does not spin.

    Greedy, not optimal — pass 1 keeps the first legal pure in row-major
    order, which can retain fewer than a perfect packing would. That is
    deliberate: it is stable, cheap, and the floor in pass 2 covers the
    shortfall.

    RNG comes off a FRESH ``random.Random(seed + 5_000)`` (RED 1_000, GREEN
    2_000, BLUE 3_000, decluster 4_000) so no existing layer's stream moves
    and old seeds keep their terrain.
    """
    width, height = params.width, params.height
    lo = max(0, min(255, int(params.pure_demote_min)))
    hi = max(lo, min(255, int(params.pure_demote_max)))
    sep = max(1, int(params.min_pure_separation))
    want = max(1, int(params.min_pure_count))
    if params.max_pure_count is not None:
        # Own stream — see the docstring. Clamped rather than validated so a
        # reversed range degrades to the floor instead of raising inside map
        # generation, where the traceback would be a long way from the caller.
        top = max(want, int(params.max_pure_count))
        want = random.Random(params.seed + 6_000).randint(want, top)
    rng = random.Random(params.seed + 5_000)

    # ── pass 1: thin to a separated set ──────────────────────────────
    kept: List[Tuple[int, int]] = []
    for y in range(height):
        for x in range(width):
            c = grid[y][x]
            if c.tile != Tile.RED or c.purity < 255:
                continue
            if all(_chebyshev((y, x), k) >= sep for k in kept):
                kept.append((y, x))
            else:
                grid[y][x] = Cell(Tile.RED, rng.randint(lo, hi))

    # ── pass 2: top up to the floor ──────────────────────────────────
    while len(kept) < want:
        best_key = None
        best_at: Optional[Tuple[int, int]] = None
        for y in range(height):
            for x in range(width):
                c = grid[y][x]
                if c.tile != Tile.RED or c.purity >= 255:
                    continue
                gap = min((_chebyshev((y, x), k) for k in kept), default=sep)
                # Clamping the gap at ``sep`` makes every legally-separated
                # candidate tie, so the richest wins among them; only when
                # nothing clears the bar does distance decide, which is the
                # degenerate-board fallback.
                key = (-min(gap, sep), -c.purity, y, x)
                if best_key is None or key < best_key:
                    best_key, best_at = key, (y, x)
        if best_at is None:
            break  # no RED left to promote — nothing more we can do
        grid[best_at[0]][best_at[1]] = Cell(Tile.RED, 255)
        kept.append(best_at)


def _grade_pure_red(grid: Grid, params: GenerationParams) -> None:
    """Surround every pure with a mass core and a vein shoulder.

    RULEBOOK §2.2. :func:`_spread_pure_red` guarantees the *count* and the
    *spacing* of jackpots but says nothing about the ground they sit in, and
    measured on the v1.28 output that ground was ordinary: promotion picks the
    richest cell that clears the separation, separated ``mass`` is vanishingly
    rare (~3 cells on a 40x28 board, and some boards have none), so the top-up
    routinely lifted a cell of purity 80-150 straight to 255. The jackpot then
    reads as a spike with nothing around it.

    That costs the player a read they should be able to trust — that thickening
    ground means you are getting warmer. A pure with no shoulder is unfindable
    except by landing on it, which turns the search into a lottery instead of
    prospecting.

    Two graded bands per pure, by EUCLIDEAN distance:

    * within :attr:`~GenerationParams.pure_mass_radius` — the ``mass`` core;
    * out to :attr:`~GenerationParams.pure_vein_radius` — a ``vein`` shoulder
      whose target purity falls off with distance, so the deposit thins
      outward instead of ending on a step.

    Three rules keep the pass from doing damage:

    1. **It only ever raises.** A cell already richer than its target is left
       alone, so grading cannot flatten terrain that was interesting already.
    2. **It never touches GREEN or BLUE.** Bare ground is promoted to RED
       (that is just growing the seam), but another colour is a deliberate
       feature of the board and is not ours to overwrite. Their cell counts
       come out of this pass exactly as they went in.
    3. **It never reaches 255.** The mass ceiling is below pure, so grading
       cannot mint a jackpot — which would break both the separation
       guarantee above and the no-touching rule of
       :func:`_decluster_pure_red`.

    Three things keep it from looking generated, which is the whole
    difficulty — a correct halo that reads as a bullseye is worse than no
    halo, because it tells the player where the jackpot is from across the
    board:

    * the radius **wanders with angle** (two harmonics, per-jackpot random
      phase), so each deposit is a different lopsided blob rather than a
      circle;
    * edges are **ragged** — a cell inside a band is skipped with a
      probability that grows with distance, so the outline is broken;
    * grading **prefers ground that is already RED**
      (:attr:`~GenerationParams.pure_halo_bare_bias`), so the deposit grows
      along the seam it belongs to instead of stamping a disc across
      whatever happens to be underneath.

    The pure list is taken ONCE, before any write, so a graded cell can never
    seed a halo of its own.

    RNG comes off a FRESH ``random.Random(seed + 7_000)`` (RED 1_000, GREEN
    2_000, BLUE 3_000, decluster 4_000, spread 5_000, count draw 6_000) so no
    existing layer's stream moves and old seeds keep their terrain.
    """
    width, height = params.width, params.height
    r_mass = max(0.0, float(params.pure_mass_radius))
    r_vein = max(r_mass, float(params.pure_vein_radius))
    if r_vein <= 0:
        return

    m_lo, m_hi = int(params.pure_mass_min), int(params.pure_mass_max)
    v_lo, v_hi = int(params.pure_vein_min), int(params.pure_vein_max)
    m_lo, m_hi = max(1, min(254, m_lo)), max(1, min(254, m_hi))
    v_lo, v_hi = max(1, min(254, v_lo)), max(1, min(254, v_hi))
    if m_hi < m_lo:
        m_lo, m_hi = m_hi, m_lo
    if v_hi < v_lo:
        v_lo, v_hi = v_hi, v_lo

    rng = random.Random(params.seed + 7_000)

    pures = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if grid[y][x].tile == Tile.RED and grid[y][x].purity >= 255
    ]
    if not pures:
        return

    lobe = max(0.0, min(0.9, float(params.pure_halo_lobe)))
    bare_bias = max(0.0, min(1.0, float(params.pure_halo_bare_bias)))
    core = max(0.0, min(1.0, float(params.pure_mass_core_chance)))
    fringe = max(0.0, min(core, float(params.pure_mass_fringe_chance)))

    # Widen the scan by the most the lobe can push the radius out, or a
    # deposit's fattest side would be clipped to the circular reach.
    reach = int(math.ceil(r_vein * (1.0 + lobe)))
    for (px, py) in pures:
        # Per-jackpot phases, so no two deposits share a silhouette.
        ph2 = rng.uniform(0.0, math.tau)
        ph3 = rng.uniform(0.0, math.tau)
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                x, y = px + dx, py + dy
                if not (0 <= x < width and 0 <= y < height):
                    continue
                if dx == 0 and dy == 0:
                    continue
                dist = math.hypot(dx, dy)

                # Two harmonics: the 2-lobe term elongates the deposit along
                # some axis, the weaker 3-lobe term stops that reading as a
                # tidy ellipse.
                theta = math.atan2(dy, dx)
                warp = 1.0 + lobe * (
                    0.62 * math.sin(2.0 * theta + ph2)
                    + 0.38 * math.sin(3.0 * theta + ph3)
                )
                rm, rv = r_mass * warp, r_vein * warp
                if dist > rv:
                    continue

                cell = grid[y][x]
                if cell.tile in (Tile.GREEN, Tile.BLUE):
                    continue  # rule 2 — not ours to overwrite

                # How much ground gets touched at all, thinning outward.
                if dist <= rm:
                    keep = 0.92
                else:
                    t = (dist - rm) / max(1e-6, rv - rm)
                    keep = 0.86 - 0.52 * t
                if cell.tile != Tile.RED:
                    keep *= bare_bias  # follow the seam, don't flood the void

                # Whether a touched cell is mass or vein decays with distance
                # to a floor, so mass clusters at the jackpot and then
                # scatters outward rather than stopping at a boundary. The
                # gaussian is on (dist - 1) so the ring actually touching the
                # pure sits at the full core chance.
                spread = math.exp(-(((max(0.0, dist - 1.0)) / 1.7) ** 2))
                p_mass = fringe + (core - fringe) * spread

                # Draw all three unconditionally, THEN decide — so the stream
                # advances the same way whatever terrain a halo falls on, and
                # a skipped cell cannot shift the cells after it.
                roll = rng.random()
                tier_roll = rng.random()
                shade = rng.random()

                if roll > keep:
                    continue

                if tier_roll < p_mass:
                    lo, hi = m_lo, m_hi
                else:
                    # Vein, thinning outward, so the deposit trails off
                    # instead of ending on a step.
                    t = min(1.0, dist / max(1e-6, rv))
                    mid = v_hi + (v_lo - v_hi) * t
                    lo = max(v_lo, int(mid) - 18)
                    hi = min(v_hi, int(mid) + 18)
                    if hi < lo:
                        lo = hi = max(v_lo, min(v_hi, int(mid)))

                target = lo + int(shade * (hi - lo + 1))
                target = max(lo, min(hi, target))
                if cell.tile == Tile.RED and cell.purity >= target:
                    continue  # rule 1 — only ever raise
                grid[y][x] = Cell(Tile.RED, target)


def _ensure_pure_red(grid: Grid, params: GenerationParams) -> None:
    """Guarantee at least one pure (255) RED cell on the board.

    Natural pures only occur in thick seam cores (see :func:`_finalize_red_purity`),
    so many seeds produce a board with no redsign to contest. If none exists, this
    deterministically promotes the richest RED cell (peak, tie-broken by
    coordinates) to pure. No-op when a pure already exists or the board carries no
    RED at all. Purely a function of the already-assigned purities — no RNG, so it
    is seed-stable.

    v1.21 — this used to promote the peak PLUS its two richest neighbours, "so the
    guaranteed pure reads as a small natural seam". That is exactly the slab
    :func:`_decluster_pure_red` now removes, so the fallback would have been the
    one path still minting a cluster. The peak alone is promoted instead; the
    neighbours keep their own high purity, which already reads as a core.
    """
    width, height = params.width, params.height
    reds: List[Tuple[int, int, int]] = []  # (purity, y, x)
    for y in range(height):
        for x in range(width):
            c = grid[y][x]
            if c.tile != Tile.RED:
                continue
            if c.purity >= 255:
                return  # a natural pure already exists — leave the map untouched
            reds.append((c.purity, y, x))
    if not reds:
        return  # no RED on the board — nothing to promote (e.g. red_coverage 0)

    # Peak = richest cell; ties resolved by coordinates for determinism.
    reds.sort(key=lambda r: (-r[0], r[1], r[2]))
    _, py, px = reds[0]
    grid[py][px] = Cell(Tile.RED, 255)


def generate_grid(params: GenerationParams) -> Grid:
    """Generate a topography grid by compositing the three biome layers."""
    if params.width <= 0 or params.height <= 0:
        raise ValueError("width and height must be positive")

    grid: Grid = [
        [Cell(Tile.EMPTY, 0) for _ in range(params.width)]
        for _ in range(params.height)
    ]
    ridge_t: List[List[float]] = [
        [0.0 for _ in range(params.width)] for _ in range(params.height)
    ]
    _apply_red(grid, params, ridge_t)
    _apply_green(grid, params)
    _apply_blue(grid, params)
    _finalize_red_purity(grid, ridge_t, params)
    # Thin clusters BEFORE the guarantee, so a board whose only pures were a
    # single slab still ends up with one rather than none.
    if params.decluster_pure_red:
        _decluster_pure_red(grid, params)
    if params.ensure_pure_red:
        _ensure_pure_red(grid, params)
    # v1.24 — LAST, so it sees the final pure set: the guarantee above can
    # mint a pure right beside a survivor of declustering, and only a pass
    # that runs after both can enforce the separation over the whole board.
    if params.spread_pure_red:
        _spread_pure_red(grid, params)
    # v1.29 — after the pure set is FINAL, so every jackpot gets a shoulder
    # and none is graded around a cell that is about to be demoted.
    if params.grade_pure_red:
        _grade_pure_red(grid, params)
    return grid
