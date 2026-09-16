"""CLI entrypoint for the sea_of_colours topography grid generator.

Run ``python main.py --help`` to see all knobs. Defaults produce a roughly
80x50 map with green polar bands, red mountain ranges, and dotted blue
concentrations.
"""

from __future__ import annotations

import argparse
import random
import sys

from sea_of_colours.data import grid_to_rgb, grid_to_semantic, save_bin, save_npy
from sea_of_colours.generator import GenerationParams, generate_grid
from sea_of_colours.png import save_png
from sea_of_colours.render import legend, to_ansi


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sea_of_colours",
        description="Generate a topographical ANSI grid (green N/S bands, red mountains, blue dots).",
    )
    p.add_argument("--width", type=int, default=80, help="Grid width in tiles (default: 80).")
    p.add_argument("--height", type=int, default=50, help="Grid height in tiles (default: 50).")
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible maps (default: random).",
    )

    p.add_argument(
        "--green-strength",
        type=float,
        default=1.0,
        help="How aggressively to paint inside green bands (default: 1.0).",
    )
    p.add_argument(
        "--green-band-count",
        type=int,
        default=None,
        choices=[0, 1, 2, 3],
        help=(
            "Force the number of diagonal green bands (default: random "
            "1-3). Pass 0 to suppress green entirely."
        ),
    )
    p.add_argument(
        "--green-band-half-width",
        type=float,
        default=1.5,
        help="Half-width of each green band in cells (default: 1.5).",
    )
    # v0.8.0 — ``--band-depth`` is retained for backwards CLI compat
    # but the new band generator ignores it. The flag is kept so
    # stored shell histories keep working without an immediate
    # rewrite.
    p.add_argument(
        "--band-depth",
        type=float,
        default=0.18,
        help=(
            "DEPRECATED (v0.8.0). Polar-band depth — ignored by the new "
            "diagonal-band generator. Use --green-band-count instead."
        ),
    )

    p.add_argument(
        "--red-coverage",
        type=float,
        default=0.30,
        help="Fraction of the map covered by mountain ranges (default: 0.30).",
    )
    ridges = p.add_mutually_exclusive_group()
    ridges.add_argument(
        "--ridges",
        dest="ridges",
        action="store_true",
        help="Use ridge transform for sharper mountain ridges (default).",
    )
    ridges.add_argument(
        "--no-ridges",
        dest="ridges",
        action="store_false",
        help="Use raw fBm (rounder, blobbier mountains).",
    )
    p.set_defaults(ridges=True)
    p.add_argument(
        "--red-gamma",
        type=float,
        default=5.0,
        help=(
            "Exponent on the curved part of ridge (default: 5.0). "
            "Lower = more vein/mass overall; see also --red-ridge-linear."
        ),
    )
    p.add_argument(
        "--red-depth-ref",
        type=float,
        default=3.0,
        help=(
            "Manhattan steps from seam edge at which depth credit saturates "
            "(default: 3.0). Lower = thicker-looking cores sooner."
        ),
    )
    p.add_argument(
        "--red-pure-min-depth",
        type=int,
        default=3,
        help=(
            "Minimum depth before solid pure (255) is allowed (default: 3). "
            "One-tile-wide seams have depth 1 and never reach 255."
        ),
    )
    p.add_argument(
        "--red-ridge-linear",
        type=float,
        default=0.28,
        help=(
            "Blend weight for linear t in ridge shape: (1-f)*t**gamma + f*t "
            "(default: 0.28). Higher = less trace-heavy map."
        ),
    )
    p.add_argument(
        "--red-core-boost",
        type=float,
        default=0.35,
        help=(
            "In thick knots (depth ≥ pure-min), adds up to this much to the "
            "ridge term so cores can reach 255 without t==1 (default: 0.35)."
        ),
    )

    p.add_argument(
        "--blue-density",
        type=float,
        default=0.03,
        help=(
            "Fraction of the map covered by blue pockets (default: 0.03). "
            "Higher = denser/larger pockets; lower = sparser singletons."
        ),
    )
    p.add_argument(
        "--smooth-blue",
        type=int,
        default=0,
        metavar="N",
        help="Apply N cellular-automata smoothing passes to coalesce blue dots (default: 0).",
    )
    p.add_argument(
        "--blue-gamma",
        type=float,
        default=1.0,
        help=(
            "Falloff exponent for blue purity along the distance-from-edge "
            "axis (default: 1.0). Each pocket already gets one deep cell at "
            "its center; this controls the ring around it. 1.0 = linear "
            "(mid ring, thin shallow edge); >1 grows the shallow edge; <1 "
            "shrinks it so most cells stay deep."
        ),
    )

    p.add_argument(
        "--no-truecolor",
        dest="truecolor",
        action="store_false",
        help="Use 256-color ANSI instead of 24-bit (for older terminals).",
    )
    p.set_defaults(truecolor=True)

    p.add_argument(
        "--print-legend",
        action="store_true",
        help="Print a color legend underneath the grid.",
    )

    p.add_argument(
        "--png",
        metavar="PATH",
        default=None,
        help="Also save the grid as a PNG at PATH (e.g. map.png).",
    )
    p.add_argument(
        "--pixel-size",
        type=int,
        default=12,
        help="Pixels per tile in the saved PNG (default: 12).",
    )
    p.add_argument(
        "--png-grid-lines",
        action="store_true",
        help="Draw thin separator lines between tiles in the PNG.",
    )
    p.add_argument(
        "--no-print",
        dest="print_terminal",
        action="store_false",
        help="Skip terminal output (useful when only saving a PNG).",
    )
    p.set_defaults(print_terminal=True)

    p.add_argument(
        "--save-data",
        metavar="PATH",
        default=None,
        help=(
            "Also save the grid as a tensor at PATH. Extension picks the format: "
            "'.npy' uses NumPy's standard format (requires numpy); anything else "
            "uses a small stdlib-only .bin container readable via "
            "sea_of_colours.data.load_bin."
        ),
    )
    p.add_argument(
        "--data-view",
        choices=("semantic", "rgb"),
        default="semantic",
        help=(
            "Which tensor view to save with --save-data: 'semantic' is (H, W, 2) "
            "uint8 of [tile_id, purity] (default); 'rgb' is (H, W, 3) uint8 with "
            "red cells encoded as (purity, 0, 0)."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    seed = args.seed if args.seed is not None else random.randrange(2**31)

    # v0.8.0 — translate the CLI ``--green-band-count`` flag into the
    # tuple shape ``GenerationParams.green_band_count_choices`` expects.
    # ``None`` keeps the default (1, 2, 3) random roll; an explicit
    # integer pins the band count for the session.
    if args.green_band_count is None:
        band_choices: tuple[int, ...] = (1, 2, 3)
    elif args.green_band_count == 0:
        band_choices = ()
    else:
        band_choices = (int(args.green_band_count),)

    params = GenerationParams(
        width=args.width,
        height=args.height,
        seed=seed,
        green_strength=args.green_strength,
        band_depth=args.band_depth,
        green_band_count_choices=band_choices,
        green_band_half_width=args.green_band_half_width,
        red_coverage=args.red_coverage,
        ridges=args.ridges,
        red_gamma=args.red_gamma,
        red_depth_ref=args.red_depth_ref,
        red_pure_min_depth=args.red_pure_min_depth,
        red_ridge_linear=args.red_ridge_linear,
        red_core_boost=args.red_core_boost,
        blue_density=args.blue_density,
        blue_smooth_iters=args.smooth_blue,
        blue_gamma=args.blue_gamma,
    )

    grid = generate_grid(params)

    if args.print_terminal:
        print(to_ansi(grid, truecolor=args.truecolor))
        if args.print_legend:
            print()
            print(legend(truecolor=args.truecolor))
            print(f"seed: {seed}")

    if args.png:
        out = save_png(
            args.png,
            grid,
            pixel_size=args.pixel_size,
            grid_lines=args.png_grid_lines,
        )
        print(
            f"saved PNG: {out}  "
            f"({len(grid[0]) * args.pixel_size}x{len(grid) * args.pixel_size}, "
            f"seed {seed})"
        )

    if args.save_data:
        if args.data_view == "rgb":
            tensor = grid_to_rgb(grid)
        else:
            tensor = grid_to_semantic(grid)

        path_lower = args.save_data.lower()
        if path_lower.endswith(".npy"):
            out_path = save_npy(args.save_data, tensor)
        else:
            out_path = save_bin(args.save_data, tensor)
        print(
            f"saved data: {out_path}  "
            f"shape={tensor.shape} dtype=uint8 view={args.data_view} seed {seed}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
