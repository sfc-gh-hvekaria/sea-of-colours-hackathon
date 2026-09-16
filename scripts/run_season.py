#!/usr/bin/env python3
"""Headless season runner — CLI orchestrator for Sea of Colours.

Drives a full season end-to-end without any frontend in the loop. Each
seat is assigned a deterministic agent runtime (``heuristic`` →
``RED_HARVEST``, or ``red_harvest_lite`` → the no-weapons variant), and
the loop tickets every planning day through
:func:`sea_of_colours.agent.runtime.run_agent_turn` until
:data:`Phase.SEASON_COMPLETE` is reached.

For **LLM seats use** :file:`scripts/run_matchup_v12.py` — V12 is an
orchestrator harness, not a runtime label this script understands.

The persistence model is the same one the live FastAPI server uses:
``soc_backend.get_store()`` returns either the in-memory store (offline
dev) or the Snowpark-backed store (default). Snowflake-backed runs land
in the deployment named by ``sea_of_colours/snowpark/naming.py``
(``SOC_HACKATHON_DB.SEA_OF_COLOURS`` unless overridden) under the
auto-generated season name so the Phase C watcher frontend can list and
replay them.

Examples::

    # Heuristic vs heuristic, persisted to Snowflake under a
    # deterministic ``LatinWord_EnglishNoun`` season name.
    python scripts/run_season.py --seed 42

    # RED_HARVEST (p1) vs the no-weapons bot (p2), 24×16 grid.
    python scripts/run_season.py --p1 heuristic --p2 red_harvest_lite \\
        --seed 7 --width 24 --height 16 --season-name "Demo_Match"

    # Offline / unit-test mode — no Snowflake required, no replay
    # persisted, just exercises the engine.
    SOC_BACKEND=memory python scripts/run_season.py --seed 1

Exit codes:
  0 — season completed naturally (phase reached SEASON_COMPLETE)
  2 — preflight / configuration error
  3 — aborted (deadlock detected / safety loop budget exhausted)
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# Self-bootstrap the repo root onto ``sys.path`` so ``python
# scripts/run_season.py`` Just Works without ``PYTHONPATH=.`` or a
# ``pip install -e .``. Matches the convention used by other scripts in
# this repo (several scripts rely on being run from the root,
# but this runner is meant to be invoked headlessly from anywhere —
# cron, CI, `nohup` from a different cwd).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Hard ceiling on the resolution loop. With ``SEASON_DAY_CAP = 5`` and
# two seats per day, the natural budget is 10 ``run_agent_turn`` calls.
# Anything past 4× that almost certainly indicates a deadlock — the
# heuristic always submits, so the only stall is a buggy custom agent
# or a turn that fails AND its fallback fails too.
MAX_TURN_ITERATIONS = 200


# ── argparse ─────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_season",
        description=(
            "Run a full Sea of Colours season headlessly and persist it "
            "to Snowflake (or in-memory) for later replay."
        ),
    )
    p.add_argument(
        "--p1",
        choices=("heuristic", "red_harvest_lite"),
        default="heuristic",
        help=(
            "Agent runtime for seat p1 (default: heuristic). "
            "'heuristic' = RED_HARVEST in-process; 'red_harvest_lite' = "
            "same playbook with chaff/EMP disabled (hackathon easy "
            "opponent). For an LLM seat see scripts/run_matchup_v12.py."
        ),
    )
    p.add_argument(
        "--p2",
        choices=("heuristic", "red_harvest_lite"),
        default="heuristic",
        help="Agent runtime for seat p2 (default: heuristic).",
    )
    p.add_argument(
        "--p3",
        choices=("heuristic", "red_harvest_lite"),
        default=None,
        help="Agent runtime for seat p3 (default: seat absent).",
    )
    p.add_argument(
        "--p4",
        choices=("heuristic", "red_harvest_lite"),
        default=None,
        help="Agent runtime for seat p4 (default: seat absent).",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "Game seed (default: random). The same seed produces the same "
            "map AND the same auto-generated season name."
        ),
    )
    p.add_argument(
        "--width",
        type=int,
        default=40,
        help="Grid width in tiles (default: 40).",
    )
    p.add_argument(
        "--height",
        type=int,
        default=28,
        help="Grid height in tiles (default: 28).",
    )
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help=(
            "Planning days per season (default: engine default = 7). "
            "v0.7.4 — the cap is stored per-session, so passing e.g. "
            "--days 10 here doesn't touch the module-level default or "
            "affect parallel runs hitting the same Snowflake schema."
        ),
    )
    p.add_argument(
        "--season-name",
        default=None,
        help=(
            "Override the auto-generated season label. Useful for tournament "
            "bookkeeping; omit to let the engine pick a deterministic name "
            "from the seed."
        ),
    )
    p.add_argument(
        "--backend",
        choices=("snowflake", "memory", "file"),
        default=None,
        help=(
            "Storage backend. Defaults to SOC_BACKEND, or auto-detect when "
            "that is unset. 'memory' keeps the season in this process, so "
            "a separate watcher server cannot see it. 'file' persists one "
            "JSON file per season under --store-dir, making the season "
            "watchable offline by a SOC_BACKEND=file server pointed at the "
            "same directory. 'snowflake' persists to the deployed schema."
        ),
    )
    p.add_argument(
        "--store-dir",
        default=None,
        help=(
            "Directory for the 'file' backend (default: SOC_STORE_DIR env "
            "or ./seasons/). Point both this runner and the watcher server "
            "at the same dir to replay offline bot seasons through /watch."
        ),
    )
    p.add_argument(
        "--watch-base-url",
        default="/watch.html",
        help=(
            "Base URL printed in the season-complete summary so you can "
            "deep-link straight into the (Phase C) watcher frontend. The "
            "season slug is appended as `?season=<slug>`."
        ),
    )
    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help=(
            "Suppress per-turn lines; only print startup banner, "
            "night-resolve summaries, and the final season-complete block. "
            "Useful for piping into a CI log."
        ),
    )
    p.add_argument(
        "--wipe-first",
        action="store_true",
        help=(
            "DESTRUCTIVE. Call store.wipe_all_sessions() before starting "
            "this season — every SOC_* row for every past session (live "
            "UI + prior CLI runs) is deleted first. Default is append-only "
            "persistence (sessions accumulate). Use this for dev resets "
            "or when you want a guaranteed-empty Snowflake schema before "
            "the season starts."
        ),
    )
    return p


# ── helpers ──────────────────────────────────────────────────────────


def _resolve_seed(arg_seed: Optional[int]) -> int:
    """Pick a seed once, deterministically when caller provided one."""
    if arg_seed is not None:
        return int(arg_seed)
    # 31-bit range matches main.py's grid generator and keeps the seed
    # fitting in a SMALLINT-friendly column if we ever narrow it.
    return random.randrange(2**31)


def _configure_backend(arg_backend: Optional[str]) -> str:
    """Lock in ``SOC_BACKEND`` before any soc_backend module use.

    Returns the resolved backend label so the banner can show it.
    """
    if arg_backend is not None:
        os.environ["SOC_BACKEND"] = arg_backend

    # v1.12 — was a hard `setdefault("snowflake")`, on the reasoning
    # that the point of the CLI runner is to persist a season for the
    # watcher. True, but it made the runner unusable on a machine with
    # no Snowflake at all. Defer to auto-detect and warn instead: a
    # season nobody else can see is a surprise worth one line, not a
    # crash.
    from sea_of_colours.snowpark import backend as soc_backend

    resolved = soc_backend.resolution()
    if not resolved.persists:
        print(
            f"[season] {resolved.summary()} — this season will not be "
            "visible to a separate watcher process. Use --backend file "
            "for offline sharing, or --backend snowflake to persist.",
            file=sys.stderr,
        )
    return resolved.name


def _slug(name: Optional[str]) -> str:
    """Mirror season_names.season_name_to_slug for URL-safe linking."""
    from sea_of_colours.game.season_names import season_name_to_slug
    return season_name_to_slug(name or "")


def _agent_label(runtime: str) -> str:
    """Human-friendly label for the startup banner."""
    if runtime == "red_harvest_lite":
        return "heuristic (RED_HARVEST_LITE — no weapons)"
    return "heuristic (RED_HARVEST)"


def _print_banner(
    *,
    season_name: str,
    session_id: str,
    seed: int,
    width: int,
    height: int,
    backend: str,
    seat_labels: Dict[str, str],
    season_day_cap: int,
) -> None:
    line = "═" * 72
    print(line, flush=True)
    print(f"  SEA OF COLOURS — headless season runner", flush=True)
    print(line, flush=True)
    print(f"  season       : {season_name}", flush=True)
    print(f"  session_id   : {session_id}", flush=True)
    print(f"  seed         : {seed}", flush=True)
    print(f"  grid         : {width}×{height}", flush=True)
    print(f"  day cap      : {season_day_cap} nights", flush=True)
    print(f"  backend      : {backend}", flush=True)
    for seat, label in seat_labels.items():
        print(f"  {seat}           : {label}", flush=True)
    print(line, flush=True)


def _format_turn_line(
    *,
    day: int,
    player: str,
    result: Dict[str, Any],
    pending_after: Dict[str, bool],
) -> str:
    """Single grep-friendly line per agent turn."""
    agent_id = result.get("agent_id", "?")
    runtime = result.get("runtime", "?")
    moves = len(result.get("moves") or [])
    ms = int(result.get("ms_elapsed", 0))
    submitted = "✓" if result.get("submitted") else "✗"
    suffix: str
    if result.get("night_resolved"):
        # Engine has already ticked the calendar; reflect the NEW day.
        suffix = "  night_resolved"
    else:
        waiting = [seat for seat, lock in pending_after.items() if not lock]
        suffix = f"  waiting_on={','.join(waiting) or '(none)'}"
    return (
        f"[day {day}] {player} {agent_id:<16} {runtime:<9} "
        f"{moves:>2} moves  {ms:>5}ms  submit={submitted}{suffix}"
    )


def _print_log_for_day(rows: List[Dict[str, Any]]) -> None:
    """Echo engine log rows for a single resolved night.

    Rows arrive from ``store.list_log(session_id, day_from=N, day_to=N)``
    in the canonical sequence the engine wrote them, so we just pretty-
    print one indented line per row with level + text. We deliberately
    skip the ``ts`` field — CLI output is for live tailing, not audit.
    """
    for row in rows:
        level = str(row.get("level", "info")).lower()
        text = str(row.get("text", ""))
        # Promote errors so they're scannable when tailing a long run.
        marker = "✗" if level == "error" else "·"
        print(f"    {marker} [{level}] {text}", flush=True)


def _print_final_block(
    *,
    view: Dict[str, Any],
    season_name: str,
    session_id: str,
    seed: int,
    watch_base_url: str,
    days_played: int,
    season_day_cap: int,
    seat_labels: Dict[str, str],
) -> None:
    scores = view.get("scores") or {}
    seat_scores = [(s, int(scores.get(s, 0))) for s in seat_labels]
    top_score = max((sc for _, sc in seat_scores), default=0)
    winners = [s for s, sc in seat_scores if sc == top_score]
    if len(winners) == 1:
        winner_seat = winners[0]
        winner = f"{winner_seat} ({seat_labels[winner_seat]})"
    else:
        winner = "tie: " + ", ".join(winners)
    slug = _slug(season_name)
    deep_link = f"{watch_base_url}?season={slug}" if slug else watch_base_url
    line = "═" * 72
    print("", flush=True)
    print(line, flush=True)
    print(f"  SEASON_COMPLETE — {season_name}", flush=True)
    print(line, flush=True)
    print(f"  session_id   : {session_id}", flush=True)
    print(f"  seed         : {seed}", flush=True)
    print(f"  days played  : {days_played}/{season_day_cap}", flush=True)
    print(f"  final score", flush=True)
    for seat, sc in seat_scores:
        print(f"    {seat} {seat_labels[seat]:<24} : {sc}", flush=True)
    print(f"  winner       : {winner}", flush=True)
    print(f"  replay", flush=True)
    print(f"    season slug : {slug or '(none)'}", flush=True)
    print(f"    watch URL   : {deep_link}", flush=True)
    print(f"    proxy URL   : /api/game/{session_id}/replay", flush=True)
    print(line, flush=True)


# ── main ─────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    seed = _resolve_seed(args.seed)
    backend = _configure_backend(args.backend)
    # The 'file' backend reads SOC_STORE_DIR at store construction; set it
    # before get_store() builds the FileSocStore below.
    if args.store_dir:
        os.environ["SOC_STORE_DIR"] = args.store_dir

    # Import here so the env-var configuration above is honoured by the
    # ``SOC_BACKEND`` capture at the top of ``soc_backend``.
    from sea_of_colours.agent.runtime import (
        HEURISTIC_AGENT_NAME,
        run_agent_turn,
    )
    from sea_of_colours.game.session import SEASON_DAY_CAP, Phase
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import engine as soc_engine

    # Reset singletons in case the process re-runs (tests / repls) — the
    # first call after this will pick up the env-var changes above.
    soc_backend.reset_for_tests()
    store = soc_backend.get_store()

    if args.wipe_first:
        # Destructive opt-in. Without this flag we run append-only:
        # the new session lands alongside every previously persisted
        # season (CLI or live UI) in SOC_GAME_SESSION.
        print(
            f"--wipe-first: deleting all prior SOC_* rows before starting",
            flush=True,
        )
        store.wipe_all_sessions()

    # v0.9.18 — mark both seats as bots at init so GameSession.new()
    # auto-assigns Latin display names + 3-letter tags + RANDOM palette
    # colors (seeded by the game seed). The per-turn runtime is still
    # forced via run_agent_turn(runtime_override=...) below; this map only
    # drives identity/colour generation, not turn execution. "heuristic"
    # → RED_HARVEST strategy slug.
    _STRATEGY_SLUG = {
        "heuristic": "red_harvest",
        "red_harvest_lite": "red_harvest_lite",
    }
    seats = ["p1", "p2"]
    if getattr(args, "p3", None):
        seats.append("p3")
    if getattr(args, "p4", None):
        seats.append("p4")
    runtime_for = {s: getattr(args, s) for s in seats}
    agents_for_init = {
        s: _STRATEGY_SLUG.get(runtime_for[s], "red_harvest") for s in seats
    }
    info = soc_engine.init_session(
        store,
        seed=seed,
        width=args.width,
        height=args.height,
        season_name=args.season_name,
        season_day_cap=args.days,
        players=seats,
        agents=agents_for_init,
    )
    session_id = info["session_id"]
    season_name = info.get("season_name") or "(unnamed)"
    # v0.7.4 — the engine echoes the resolved cap (default 5 when
    # --days is omitted); fall back to SEASON_DAY_CAP for legacy
    # ``init_session`` builds that haven't been redeployed yet.
    season_day_cap = int(info.get("season_day_cap") or SEASON_DAY_CAP)

    seat_labels = {s: _agent_label(runtime_for[s]) for s in seats}
    _print_banner(
        season_name=season_name,
        session_id=session_id,
        seed=seed,
        width=args.width,
        height=args.height,
        backend=backend,
        seat_labels=seat_labels,
        season_day_cap=season_day_cap,
    )

    iteration = 0
    started = time.time()

    while True:
        if iteration >= MAX_TURN_ITERATIONS:
            print(
                f"\n[abort] safety budget exhausted "
                f"({MAX_TURN_ITERATIONS} agent turns) — bailing out. "
                "Likely a deadlocked seat (heuristic "
                "fallback also empty?). Check Snowflake logs.",
                flush=True,
            )
            return 3

        status = soc_engine.get_session_status(store, session_id)
        phase = status.get("phase")
        if phase == Phase.SEASON_COMPLETE.value:
            break

        pending = status.get("pending") or {}
        # Pick the next seat that hasn't submitted yet, in seat order.
        seat = next((s for s in seats if not pending.get(s, False)), None)
        if seat is None:
            # All seats submitted but phase didn't advance — force resolve.
            soc_engine.run_night(store, session_id)
            iteration += 1
            continue

        result = run_agent_turn(
            store,
            session_id,
            seat,
            runtime_override=runtime_for[seat],
        )
        # Refresh pending so the suffix on the turn line reflects the
        # state AFTER this seat's submission.
        post = soc_engine.get_session_status(store, session_id)
        pending_after = post.get("pending") or {}

        # The day this turn played. When ``night_resolved`` fires the
        # engine has already advanced the calendar (``post.day`` is the
        # NEXT planning day); the actual turn we just ran belonged to
        # the prior day. Otherwise we're still in that day's planning
        # phase and ``post.day`` is correct.
        post_day = int(post.get("day", 1))
        played_day = post_day - 1 if result.get("night_resolved") else post_day

        if not args.quiet:
            print(
                _format_turn_line(
                    day=played_day,
                    player=seat,
                    result=result,
                    pending_after=pending_after,
                ),
                flush=True,
            )

        if result.get("night_resolved"):
            # Read the full log slice for the night we just resolved.
            # ``store.list_log(day_from=N, day_to=N)`` is bounded by the
            # rows the engine appended for that specific night — no
            # sliding-window truncation like ``get_session_status``.
            night_rows = store.list_log(
                session_id, day_from=played_day, day_to=played_day,
            )
            _print_log_for_day(night_rows)
            if not args.quiet:
                print("", flush=True)

        iteration += 1

    final_view = soc_engine.get_view(store, session_id, "p1")
    days_played = max(0, int(final_view.get("day", 1)) - 1)
    elapsed = time.time() - started
    _print_final_block(
        view=final_view,
        season_name=season_name,
        session_id=session_id,
        seed=seed,
        watch_base_url=args.watch_base_url,
        days_played=days_played,
        season_day_cap=season_day_cap,
        seat_labels=seat_labels,
    )
    print(f"  wall time    : {elapsed:.1f}s", flush=True)
    # Touch the unused default so static analysers don't drop it when the
    # module is imported for help-text generation in tooling.
    _ = HEURISTIC_AGENT_NAME
    return 0


if __name__ == "__main__":
    sys.exit(main())
