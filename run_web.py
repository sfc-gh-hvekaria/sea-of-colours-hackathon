#!/usr/bin/env python3
"""Run the FastAPI UI without editable installs.

Adds the repo root to ``sys.path`` so ``import sea_of_colours`` works, then
starts Uvicorn. Use from repo root::

    python run_web.py              # solo play on http://127.0.0.1:8000
    python run_web.py --lan        # invite phones on the same Wi-Fi
    python run_web.py --port 8001  # when 8000 is taken
    python run_web.py --replace    # ...or take 8000 back from an older run

v1.26 — ``--replace`` stops the *Sea of Colours* server already on the
port and then starts. The canonical command runs ``--no-reload``, so
picking up a Python change means restarting by hand, and the way that
goes wrong is uvicorn's ``address already in use`` — an error that
names the problem and withholds the only useful fact, which is whose
server that is. It stays opt-in and it refuses anything it cannot prove
is ours; see :mod:`server.portguard`.

v1.15 — ``--lan`` exists because the defaults here are deliberately
loopback-only, and that silently broke the multiplayer invite flow: the
invite modal would detect the host's LAN IP, print a QR pointing at it,
and the phone would get connection-refused because nothing was listening
on that interface. Binding the LAN is a real exposure decision (no auth,
anyone on the network can take a seat), so it stays opt-in — but it now
has an obvious name instead of requiring a hand-written uvicorn command.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _note(msg: str) -> None:
    """Launcher chatter goes to stderr, beside uvicorn's own banner."""
    print(msg, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_web.py",
        description="Start the Sea of Colours web UI.",
    )
    p.add_argument(
        "--host", default="127.0.0.1",
        help="Interface to bind. Default 127.0.0.1 (this machine only).",
    )
    p.add_argument("--port", type=int, default=8000, help="Port. Default 8000.")
    p.add_argument(
        "--lan", action="store_true",
        help="Host a shared game: bind 0.0.0.0 so phones and laptops on "
             "the same Wi-Fi can join, and turn auto-reload off. Anyone "
             "who can reach this machine can take a seat.",
    )
    p.add_argument(
        "--replace", action="store_true",
        help="If a Sea of Colours server is already on this port, stop it "
             "and take over. Refuses if the port belongs to anything else.",
    )
    reload_group = p.add_mutually_exclusive_group()
    reload_group.add_argument(
        "--reload", dest="reload", action="store_true", default=None,
        help="Restart on file changes (default when not using --lan).",
    )
    reload_group.add_argument(
        "--no-reload", dest="reload", action="store_false",
        help="Never restart on file changes.",
    )
    return p


def resolve(argv: list[str] | None = None) -> tuple[str, int, bool]:
    """``(host, port, reload)`` for these arguments.

    Split out from :func:`main` so the flag semantics — particularly
    "--lan turns reload off unless you insist" — are testable without
    starting a server.
    """
    args = build_parser().parse_args(argv)
    host = "0.0.0.0" if args.lan else args.host
    # Reload is a dev convenience that costs you a shared game: the
    # cross-player submit lock is a per-process threading.Lock, and a
    # memory-backed season lives in that process, so an incidental file
    # save mid-party resets everyone. --lan therefore defaults it off,
    # while an explicit --reload still wins.
    reload = args.reload if args.reload is not None else not args.lan
    return host, int(args.port), bool(reload)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    host, port, reload = resolve(argv)

    if args.replace:
        # Before the boot banner and before importing the app, so a
        # refusal costs nothing and — with SOC_BACKEND=snowflake — we
        # don't open a second Snowpark session only to abandon it.
        from server import portguard  # noqa: PLC0415 — after sys.path setup

        try:
            portguard.replace(host, port, log=_note)
        except portguard.ReplaceRefused as exc:
            _note(f"[soc] {exc}")
            return 1

    if args.lan:
        from server.app import _lan_ip  # noqa: PLC0415 — after sys.path setup

        ip = _lan_ip() or host
        print(
            f"[soc] LAN hosting: other devices join at http://{ip}:{port}/\n"
            f"[soc]   anyone who can reach this machine can take a seat — "
            f"there is no auth.",
            file=sys.stderr,
            flush=True,
        )

    import uvicorn

    uvicorn.run(
        "server.app:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=[str(ROOT / "server"), str(ROOT / "sea_of_colours")],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
