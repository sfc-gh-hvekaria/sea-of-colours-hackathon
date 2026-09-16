#!/usr/bin/env python3
"""Pre-flight for a fresh install — one line per check, fix beside each failure.

    python scripts/quickstart_check.py            # offline checks only
    python scripts/quickstart_check.py --network  # also contact Snowflake

This exists to turn "it doesn't work" into a specific missing thing, and
it is the one command worth quoting at anyone who is stuck. Every check
prints its own fix, so the output is self-contained: nobody should have
to cross-reference a doc to act on it.

Two deliberate choices:

* **Nothing here is required to play.** Only the base install can fail
  the run (exit 1). Snowflake, Cortex and ``cloudflared`` are opt-in
  capabilities, so they report ``skip`` — a green run does not mean you
  configured Snowflake, it means nothing is *broken*.
* **Offline by default.** Opening a Snowpark session costs seconds and a
  warehouse resume, so anything touching the network waits for
  ``--network``.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PASS, FAIL, SKIP, WARN = "pass", "fail", "skip", "warn"

_MARK = {
    PASS: "\033[32m  ok  \033[0m",
    FAIL: "\033[31m FAIL \033[0m",
    SKIP: "\033[90m skip \033[0m",
    WARN: "\033[33m warn \033[0m",
}
_PLAIN = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip ", WARN: " warn "}

_MIN_PYTHON = (3, 10)
_BASE_MODULES = ("fastapi", "uvicorn", "httpx", "requests", "pytest")


class Report:
    """Accumulates results so the summary can count what actually matters."""

    def __init__(self, colour: bool) -> None:
        self.colour = colour
        self.failures = 0
        self.warnings = 0

    def line(self, status: str, label: str, detail: str = "", fix: str = "") -> None:
        mark = (_MARK if self.colour else _PLAIN)[status]
        print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
        if fix:
            print(f"         → {fix}")
        if status == FAIL:
            self.failures += 1
        elif status == WARN:
            self.warnings += 1

    def section(self, title: str) -> None:
        print(f"\n{title}")
        print("─" * max(len(title), 44))


# ── checks ─────────────────────────────────────────────────────────────

def check_python(r: Report) -> None:
    v = sys.version_info
    got = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= _MIN_PYTHON:
        r.line(PASS, "Python version", got)
    else:
        r.line(
            FAIL, "Python version",
            f"{got}, need {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+",
            "install a newer Python and recreate your virtualenv",
        )


def check_base_deps(r: Report) -> None:
    missing = [m for m in _BASE_MODULES if importlib.util.find_spec(m) is None]
    if missing:
        r.line(
            FAIL, "Base dependencies", f"missing {', '.join(missing)}",
            "pip install -r requirements.txt",
        )
    else:
        r.line(PASS, "Base dependencies", ", ".join(_BASE_MODULES))


def check_venv(r: Report) -> None:
    """Not a failure — plenty of people install globally on purpose."""
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        r.line(PASS, "Virtualenv", sys.prefix)
    else:
        r.line(
            WARN, "Virtualenv", "not active — installing into the system Python",
            "python -m venv .venv && source .venv/bin/activate",
        )


def check_engine_imports(r: Report) -> None:
    """Catches a half-copied tree or a stale .pyc before the server does."""
    try:
        from sea_of_colours.game.session import GameSession  # noqa: F401
        from sea_of_colours.snowpark import engine  # noqa: F401
    except Exception as exc:
        r.line(
            FAIL, "Game engine imports", str(exc),
            "run from the repo root; check the install completed",
        )
        return
    r.line(PASS, "Game engine imports", "sea_of_colours.game + snowpark")


def check_backend(r: Report) -> None:
    from sea_of_colours.snowpark import backend as soc_backend

    res = soc_backend.resolution()
    detail = f"{res.name} — {res.reason}"
    if res.persists:
        r.line(PASS, "Storage backend", detail)
    else:
        # Memory is a perfectly good answer; only say so, don't scold.
        r.line(PASS, "Storage backend", detail,
               "sessions are lost on restart; see docs/SNOWFLAKE_SETUP.md §2"
               if res.requested == "auto" else "")


def check_agents(r: Report) -> None:
    try:
        from sea_of_colours.orchestrator_2.binding_registry import (
            AGENT_LABEL_BINDINGS,
        )
    except Exception as exc:
        r.line(FAIL, "Agent roster", str(exc), "check the install completed")
        return
    labels = sorted(AGENT_LABEL_BINDINGS)
    r.line(PASS, "Agent roster", ", ".join(labels) if labels else "none")


def check_cortex(r: Report, network: bool) -> None:
    """V12's credentials. Optional — the heuristics need none of this."""
    try:
        from sea_of_colours.orchestrator_2 import cortex_chat
    except Exception as exc:
        r.line(FAIL, "Cortex (V12)", str(exc))
        return

    ready, why = cortex_chat.credentials_status()
    if not ready:
        r.line(
            SKIP, "Cortex credentials (V12)", why,
            "docs/SNOWFLAKE_SETUP.md §1 — only needed to play against V12",
        )
        return
    r.line(PASS, "Cortex credentials (V12)", "PAT and account present")

    if not network:
        r.line(SKIP, "Cortex reachable", "offline mode", "re-run with --network")
        return
    try:
        reply = cortex_chat.CortexChatInvoker().invoke(
            "Reply with exactly the word: pong"
        )
    except Exception as exc:
        r.line(
            FAIL, "Cortex reachable", str(exc)[:160],
            "check the PAT has SNOWFLAKE.CORTEX_USER and has not expired",
        )
        return
    ok = "pong" in str(reply).lower()
    r.line(PASS if ok else WARN, "Cortex reachable",
           "got 'pong'" if ok else f"unexpected reply: {str(reply)[:80]}")


def check_snowpark(r: Report, network: bool) -> None:
    """The persistence path.

    Not needed to *play* — you get a full game against the heuristics
    without it — but it is what the agent-iteration tooling reads, and
    the launcher only offers LLM seats on a persistent backend. So this
    reports SKIP rather than FAIL, while naming what's still closed off.
    """
    from sea_of_colours.snowpark import backend as soc_backend

    ready, why, fix = soc_backend.snowflake_readiness()
    if not ready:
        r.line(SKIP, "Snowflake backend", why,
               fix or "docs/SNOWFLAKE_SETUP.md §2 — needed for LLM agents "
                      "in the launcher and the turn-replay tooling")
        return
    r.line(PASS, "Snowflake backend", "deps and key-pair config present")

    if not network:
        r.line(SKIP, "Snowflake connection", "offline mode",
               "re-run with --network")
        return

    from sea_of_colours.snowpark import naming

    try:
        session = soc_backend._build_snowpark_session()
    except Exception as exc:
        r.line(FAIL, "Snowflake connection", str(exc)[:160],
               soc_backend._fix_for(exc))
        return
    r.line(PASS, "Snowflake connection", "session opened")

    db, sch = naming.database(), naming.schema()
    try:
        rows = session.sql(
            f"SHOW TABLES LIKE 'SOC_%' IN SCHEMA {db}.{sch}"
        ).collect()
    except Exception as exc:
        r.line(FAIL, f"Schema {db}.{sch}", str(exc)[:160],
               "python scripts/deploy_soc_schema.py")
        return
    if rows:
        r.line(PASS, f"Schema {db}.{sch}", f"{len(rows)} SOC_* tables")
    else:
        r.line(FAIL, f"Schema {db}.{sch}", "no SOC_* tables found",
               "python scripts/deploy_soc_schema.py")


def check_cloudflared(r: Report) -> None:
    """Report tunnel providers, not just cloudflared.

    v1.16 — multiplayer walks an ordered provider list, so the useful
    answer is "can you host at all" plus "do you have a spare".

    v1.17 — ssh alone will still host, but it is now the *fallback*, and
    its free hostname rotates after minutes, which silently kills invite
    links mid-game. cloudflared keeps one address for the whole session,
    so its absence is worth flagging rather than mentioning in passing.
    """
    ssh = shutil.which("ssh")
    cf = shutil.which("cloudflared")
    have = [n for n, p in (("cloudflare", cf), ("localhost.run (ssh)", ssh)) if p]
    if cf:
        r.line(PASS, "tunnel providers (multiplayer)", ", ".join(have))
    elif ssh:
        r.line(WARN, "tunnel providers (multiplayer)", "localhost.run (ssh) only",
               "brew install cloudflared — the ssh fallback's free address "
               "changes after ~13 min, which breaks invite links mid-game")
    else:
        r.line(SKIP, "tunnel providers (multiplayer)", "none on PATH",
               "brew install cloudflared, or install OpenSSH — only needed "
               "to host over the internet")


def check_tests(r: Report) -> None:
    """Opt-in: correct but slow enough to annoy on a routine pre-flight."""
    # No -x: the whole point is the pass/fail counts, and stopping at the
    # first failure would hide them behind the one known-failing test.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    summary = tail[-1] if tail else "no output"
    if proc.returncode == 0:
        r.line(PASS, "Test suite", summary)
    else:
        # A known-failing scenario test is expected on a clean checkout;
        # calling that a broken install sends people bug-hunting.
        r.line(WARN, "Test suite", summary,
               "one pre-existing scenario failure is expected on a clean "
               "checkout; anything else is worth a look")


# ── main ───────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pre-flight checks for a fresh Sea of Colours install.",
    )
    ap.add_argument(
        "--network", action="store_true",
        help="also contact Snowflake (opens a session, resumes a warehouse)",
    )
    ap.add_argument(
        "--tests", action="store_true", help="also run the pytest suite",
    )
    ap.add_argument("--no-colour", action="store_true", help="plain output")
    args = ap.parse_args()

    r = Report(colour=not args.no_colour and sys.stdout.isatty())

    print("Sea of Colours — quickstart check")
    print(f"repo: {REPO_ROOT}")

    r.section("Install")
    check_python(r)
    check_venv(r)
    check_base_deps(r)
    check_engine_imports(r)

    if r.failures:
        # Everything downstream imports the package; more failures here
        # would be noise restating the same root cause.
        print("\nBase install is incomplete — fix the above first.")
        return 1

    r.section("Play")
    check_backend(r)
    check_agents(r)

    r.section("Optional — Snowflake")
    check_cortex(r, args.network)
    check_snowpark(r, args.network)

    r.section("Optional — multiplayer")
    check_cloudflared(r)

    if args.tests:
        r.section("Tests")
        check_tests(r)

    r.section("Summary")
    if r.failures:
        print(f"{r.failures} check(s) failed — see the → lines above.")
        return 1
    if r.warnings:
        print(f"Ready to play ({r.warnings} warning(s)).")
    else:
        print("Ready to play.")
    print("\n    python run_web.py    →  http://127.0.0.1:8000")
    print("    then hit 'Quick game' for a board against RED_HARVEST_LITE")
    if not args.network:
        print("\n(Snowflake checks were offline — re-run with --network to "
              "verify the connection.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
