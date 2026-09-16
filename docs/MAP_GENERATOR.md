# The map generator CLI

`main.py` is a standalone, dependency-free terminal map generator. It
predates the game and still runs on its own: it prints topographical
colour grids as ANSI squares, writes PNGs, and dumps grids as tensors.

**You do not need any of this to play.** The game generates its own
terrain through the same library, and the web UI never shells out to
this CLI. It lives here rather than in the README so the README can be
about the game; reach for it when you want to eyeball terrain, tune the
noise parameters, or export a grid for analysis.

> The `purity` tiers below (`trace`/`vein`/`mass`/`pure` and
> `shallow`/`mid`/`sink`/`deep`) are described here as the *renderer*
> sees them. [`RULEBOOK.md`](../RULEBOOK.md) §2.2 and §2.4 are canonical
> for what they mean in play — if the two disagree, the RULEBOOK wins.

## The map generator CLI

The game's terrain comes from a standalone generator you can also run on
its own — a dependency-free Python CLI that generates topographical
colour grids and prints them as ANSI squares in your terminal.

Each tile is one of four states:

- empty (dark gray)
- green &mdash; 1&ndash;3 same-orientation diagonal bands stretching across the map (v0.8.0; previously polar)
- red &mdash; vast mountain ranges that sweep through the middle latitudes
- blue &mdash; dotted concentrations scattered across the map (lakes, oases)

Tiles are placed by layered value-noise + fractal Brownian motion so the
output reads as terrain rather than independent random scatter.

### Red concentration tiers

Every cell carries a `purity` value in `[0, 255]`. For **red** and **blue**
the same four **numeric** bands apply; only the rendering palette differs.
Unicode Block Elements — **homogeneous** pairs per band (`░░`, `▒▒`, `▓▓`) plus solid fills:

| Level | Name  | Purity    | Red pattern | Approx. fill (PNG dither) |
| --- | --- | --- | --- | --- |
| 1   | trace | 0–50      | `░░` | sparse |
| 2   | vein  | 51–150    | `▒▒` | mid |
| 3   | mass  | 151–254   | `▓▓` | heavy |
| 4   | pure  | **255 only** | solid red | 100% |

A power curve on ``t`` (plus a linear ``t`` mix via `--red-ridge-linear`,
default `0.28`) shapes ridge brightness; **thickness** is Manhattan distance to
the seam edge (`--red-depth-ref` default `3`). That spreads values into **vein**
and **mass** more than a pure ``t**gamma`` curve; thin traces stay light, thick
cores still climb toward **pure** when deep enough.

**That is only the natural terrain.** Four later passes then rework the pure
cells specifically, and they are what a board actually ships with — see
RULEBOOK §2.2 for the reasoning:

| Pass | Does what |
|---|---|
| `ensure_pure_red` | promotes the strongest RED core if the noise made no pure at all |
| `decluster_pure_red` | thins touching pures to one per cluster |
| `spread_pure_red` | guarantees the COUNT (a band sized to the seat count) and a minimum **Chebyshev 12** between any two |
| `grade_pure_red` (v1.29) | lays a graded deposit around each pure — `mass` chunks near it, a `vein` shoulder out to `pure_vein_radius` |

So purity near a jackpot is **not** a function of Manhattan depth: grading
enriches that ground directly, and roughly doubles the total RED on a board.
Set `SOC_MAP_HALO=off` to generate pre-v1.29 terrain instead.

### Blue depth tiers

**Blue is a pocket**, not a seam: a Chebyshev distance transform finds the
center of each blob; purity falls off from that peak. The same four bands
as red:

| Level | Name     | Purity    | Blue pattern |
| --- | --- | --- | --- |
| 1   | shallow  | 0–50      | `░░` |
| 2   | mid      | 51–150    | `▒▒` |
| 3   | sink     | 151–254   | `▓▓` |
| 4   | deep     | **255 only** | solid blue |

The pocket **peak** reaches purity `255` (`deep`). `--blue-gamma` (default
`1.0`) controls the falloff toward the pocket edge: `>1` grows the outer
bands; `<1` keeps more cells near the peak.

The PNG renderer mirrors both red and blue tiers using **Bayer-dithered
pixel fills** at the matching densities so a tile in the PNG visually
matches the loading-bar style block character in the terminal.

Green currently defaults to full purity and will get its own tier rules
later.

## Requirements

- Python 3.10 or newer
- A terminal that supports ANSI background colors (24-bit preferred; 256-color
  fallback available via `--no-truecolor`)
- No third-party packages for the CLI / library / PNG / tensor paths.
  The optional Web UI uses [FastAPI](https://fastapi.tiangolo.com/) +
  [Uvicorn](https://www.uvicorn.org/) — see the [README](../README.md#web-ui).

## Usage

From the project root:

```bash
python main.py
```

That prints an 80x50 map with default settings. Pass `--help` to see every
flag. Use `--print-legend` to print a color key and the seed underneath the
grid.

### Reproducible maps

```bash
python main.py --seed 42 --print-legend
```

### Bigger map with thicker green bands

```bash
python main.py --width 120 --height 70 --green-band-half-width 2.5
```

### Heavier mountain coverage with blobby (non-ridged) mountains

```bash
python main.py --red-coverage 0.45 --no-ridges
```

### Dense lake clusters instead of single-pixel dots

```bash
python main.py --blue-density 0.10 --smooth-blue 1
```

### Older terminal (no true-color support)

```bash
python main.py --no-truecolor
```

### Save a PNG (also prints to terminal)

```bash
python main.py --seed 42 --png maps/map.png --pixel-size 16
```

### Only save a PNG, no terminal output

```bash
python main.py --seed 42 --png maps/map.png --no-print
```

### Save the grid as a tensor

```bash
python main.py --seed 42 --save-data maps/map.npy           # NumPy .npy, semantic view
python main.py --seed 42 --save-data maps/map.npy --data-view rgb
python main.py --seed 42 --save-data maps/map.bin           # stdlib-only .bin (no numpy needed)
```

Two views are available via `--data-view`:

- `semantic` *(default)* — `(H, W, 2)` `uint8`, two channels per cell:
  `[tile_id, purity]` where `tile_id` is `0=empty, 1=green, 2=red, 3=blue`.
  This is the game state, losslessly round-trippable.
- `rgb` — `(H, W, 3)` `uint8`. Red cells encode `(purity, 0, 0)` and blue
  cells encode `(0, 0, purity)`, so each substance's purity is directly
  readable from its channel; empty and green use the renderer's palette.

The output format follows the file extension: `.npy` writes a standard
NumPy file (requires NumPy installed); anything else writes a small
stdlib-only `.bin` container readable via `sea_of_colours.data.load_bin`.

## Parameter cheatsheet

| Flag | Default | What it does |
| --- | --- | --- |
| `--width` | `80` | Grid width in tiles. |
| `--height` | `50` | Grid height in tiles. |
| `--seed N` | random | Seed for reproducible output. |
| `--green-strength` | `1.0` | Higher = denser green inside the bands. |
| `--green-band-count` | random 1&ndash;3 | Force a specific green band count (0 disables green). |
| `--green-band-half-width` | `1.5` | Half-width of each band in cells (~3 cells thick). |
| `--band-depth` | `0.18` | DEPRECATED (v0.8.0). Polar-band knob; ignored by the new generator. |
| `--red-coverage` | `0.30` | Fraction of the map covered by mountains. |
| `--ridges` / `--no-ridges` | ridges on | Sharper ridge-line mountains vs. round blobs. |
| `--red-gamma` | `5.0` | Curved part ``t**gamma`` in ridge; lower ⇒ brighter mid-seam. |
| `--red-depth-ref` | `3.0` | Depth at which thickness factor saturates (lower ⇒ “fatter” cores sooner). |
| `--red-ridge-linear` | `0.28` | Blend toward linear `t` so maps are less trace-dominated. |
| `--red-pure-min-depth` | `3` | Minimum depth (tiles) before purity may be **255** (`pure`). |
| `--red-core-boost` | `0.35` | Extra ridge headroom in thick knots so **pure** appears without `t === 1`. |
| `--blue-density` | `0.03` | Fraction of the map covered by blue pockets. |
| `--smooth-blue N` | `1` | Cellular-automata passes to coalesce dots into chunkier pockets. |
| `--blue-gamma` | `1.0` | Falloff exponent for blue depth from pocket edge to center. `1.0` = linear; `>1` grows the shallow edge; `<1` keeps pockets mostly deep. |
| `--no-truecolor` | off | Use 256-color ANSI instead of 24-bit. |
| `--print-legend` | off | Print color legend and seed below the map. |
| `--png PATH` | off | Also save the grid as a PNG to `PATH`. |
| `--pixel-size N` | `12` | Pixels per tile in the saved PNG. |
| `--png-grid-lines` | off | Draw thin separator lines between tiles in the PNG. |
| `--no-print` | off | Skip terminal output (handy when only saving a PNG). |
| `--save-data PATH` | off | Also save the grid as a tensor. `.npy` ⇒ NumPy format; anything else ⇒ stdlib `.bin`. |
| `--data-view` | `semantic` | Tensor view: `semantic` `(H,W,2)` of `[tile, purity]`, or `rgb` `(H,W,3)`. |

## How the topography is generated

Three independent noise fields are sampled and then composited in priority
order:

1. **Red mountains** are sampled from a low-frequency fBm field. With ridges
   enabled, the field is passed through `1 - |2n - 1|` so high values form
   connected ridge lines. The top `--red-coverage` fraction becomes RED.
2. **Green diagonal bands** (v0.8.0) combine a band-distance mask (a
   smoothstep falloff over the perpendicular distance to each of 1&ndash;3
   parallel band centers) with a separate noise layer. The mask is 1 at the
   center of each band and 0 outside `green_band_half_width` cells, but the noise gives the
   band a ragged inner border instead of a flat stripe.
3. **Blue pockets** are sampled from a medium-frequency fBm field with a
   high threshold (top `--blue-density` fraction). One pass of cellular-
   automata smoothing coalesces specks into chunkier pockets by default.
   Once the pocket *shapes* are settled, a Chebyshev distance transform
   picks the geometric center of each connected component as the pocket's
   peak, and every other cell's purity is graded by its Chebyshev distance
   *from that one peak* — so each pocket renders as a single bright
   ``deep`` core surrounded by a ``mid`` ring and a ``shallow`` halo.

Each layer is allowed to overwrite the previous one, so green takes priority
over red where the bands meet the mountains, and blue takes priority over
everything for the small dotted features.

## Programmatic use

```python
from sea_of_colours import generate_grid, to_ansi
from sea_of_colours.generator import GenerationParams

grid = generate_grid(GenerationParams(width=100, height=60, seed=7))
print(to_ansi(grid))
```

### Grid as a tensor

`sea_of_colours.data` exposes the same grid as a packed `uint8` buffer. The
underlying storage is a `bytearray` (1 byte per channel per cell, row-major)
so it allocates no per-cell Python objects, and `GridTensor.numpy()` converts
it to a real `ndarray` of shape `(H, W, channels)` via `np.frombuffer` + a
reshape — zero copy if you ask for `copy=False`:

```python
from sea_of_colours import generate_grid, grid_to_rgb, grid_to_semantic
from sea_of_colours.generator import GenerationParams

grid = generate_grid(GenerationParams(width=100, height=60, seed=7))

semantic = grid_to_semantic(grid)        # (60, 100, 2): [tile_id, purity]
rgb      = grid_to_rgb(grid)             # (60, 100, 3): RGB; red cells = (purity, 0, 0)

# Stdlib-only: GridTensor exposes shape, channels, and raw bytes
print(semantic.shape, semantic.channels, len(semantic.data))

# Optional numpy view — true ndarray, ready for ML / image libs
arr = rgb.numpy()                         # writable copy
arr_view = rgb.numpy(copy=False)          # zero-copy, read-only

# Save / load without numpy:
from sea_of_colours import save_bin, load_bin
save_bin("map.bin", semantic)
roundtrip = load_bin("map.bin")           # same shape, dtype, data
```
