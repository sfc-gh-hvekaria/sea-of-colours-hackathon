"""Mint lab boards from V12-vs-V12 seasons, for a SNAP-capable library.

Issue 42 left an open task: every shipped board was frozen before SNAP
existed, so its blob does not price `snap` and `arm()` correctly refuses
a snap rack on it — which means nothing in the library can exercise the
weapon. A board stamps the economy of the season it came from, so the
fix is not code, it is a season played today.

Two seats of V12 rather than V12-vs-heuristic on purpose: a frozen turn
is most useful when both sides were thinking, and the diff a fork wants
to read is against a board where the opposition was not a bot walking a
policy.

Sequential by construction — ``mint`` pins ``uuid4`` process-wide under a
lock to stay reproducible, so two at once would interleave one seeded
stream and neither would regenerate.

    python scripts/_mint_snap_boards.py --seasons 3
"""

from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, default=3)
    ap.add_argument("--p1", default="tabula_v12")
    ap.add_argument("--p2", default="tabula_v12")
    ap.add_argument("--days", type=int, default=7)
    # Smaller than mint's 40x28 default: SNAP is single-square tempo
    # denial, so a board is only interesting if the two seats are close
    # enough to contest the same ground.
    ap.add_argument("--width", type=int, default=28)
    ap.add_argument("--height", type=int, default=18)
    ap.add_argument("--seeds", default="")
    args = ap.parse_args()

    from turnlab import mint as lab_mint
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND

    print(f"today's economy: {dict(BLUE_COST_BY_KIND)}", flush=True)
    if "snap" not in BLUE_COST_BY_KIND:
        print("SNAP is not priced — these boards would be no better than "
              "the ones already shipped", file=sys.stderr)
        return 1

    seeds = (
        [int(s) for s in args.seeds.split(",") if s.strip()]
        if args.seeds else [7301, 7302, 7303][: args.seasons]
    )
    seats = {"p1": args.p1, "p2": args.p2}
    print(f"seats  {seats}")
    print(f"seeds  {seeds}\n", flush=True)

    started = time.time()
    for i, seed in enumerate(seeds, 1):
        print(f"── season {i}/{len(seeds)} · seed {seed} "
              f"{'─' * 30}", flush=True)
        t0 = time.time()

        def _say(fr, _seed=seed):
            print(f"   froze d{fr.day} {fr.seat} → {fr.snapshot_id} "
                  f"({round(time.time() - t0)}s)", flush=True)

        res = lab_mint.mint(
            seats,
            seed=seed,
            width=args.width,
            height=args.height,
            days=args.days,
            season_name=f"SNAPLAB_{seed}",
            on_freeze=_say,
        )
        print(f"   session   {res.session_id}")
        print(f"   days      {res.days_played}")
        print(f"   scores    {dict(res.scores)}")
        print(f"   frozen    {len(res.frozen)} boards in {res.seconds}s")
        if res.aborted:
            print(f"   ABORTED   {res.aborted}")
        print(flush=True)

    print(f"all done in {round(time.time() - started)}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
