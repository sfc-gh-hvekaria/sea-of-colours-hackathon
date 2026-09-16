"""Stop the Sea of Colours server already on this port, and only that.

v1.26 — backs ``run_web.py --replace``. The failure it removes is a dead
end rather than an error: uvicorn prints ``address already in use`` and
exits, and the one fact you need next — *whose* server that is — is the
one fact the message does not carry. In this repo the answer is almost
always "my own, from earlier", because the canonical command runs with
``--no-reload`` and so has to be restarted by hand to pick up a Python
change.

**Identity is proved, not guessed.** Port 8000 is the most contested
port on a dev machine, and the cost of being wrong is asymmetric: we
would be killing someone's unrelated work with no undo. So the check is
an HTTP call to ``/api/meta/whoami`` — an answer on the socket proves
the listener is ours, where a command line matched out of ``ps`` only
suggests it. Anything that does not answer with our marker is left
alone and the launcher exits, which is the same outcome as today minus
the guesswork.

The one place matching *is* used is the fallback for a server that holds
the port but no longer answers (a wedged worker, a Snowpark call that
never returned). That is exactly when a human needs this flag most, so
refusing there would defeat the point — but it is the weaker evidence,
and :attr:`Occupant.how` records which of the two we relied on so the
launcher can say so out loud before it kills anything.

Nothing here runs unless ``--replace`` is passed. Killing a server is a
decision, and per ``AGENTS.md`` the person who started one owns it.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

APP_IDENT = "sea_of_colours"

# Command lines we accept as "a Sea of Colours server" when the port is
# held but nothing answers on it. Both halves of the uvicorn pair match:
# the reloader parent runs `run_web.py`, the worker runs `server.app`.
_PROC_MARKERS = ("run_web.py", "server.app", "server:app")

# How long to wait for a polite SIGTERM before escalating. A server with
# an open Snowpark session takes a beat to unwind, and killing it harder
# than necessary can leave that session dangling.
TERM_GRACE_S = 6.0
KILL_GRACE_S = 3.0


class ReplaceRefused(RuntimeError):
    """The port is busy and we could not prove it is ours to take."""


@dataclass(frozen=True)
class Occupant:
    """What is on the port, and how confident we are about it."""

    pid: int | None
    root: str | None
    how: str  # "http" — it told us | "process" — it looks like us
    detail: str

    @property
    def proved(self) -> bool:
        return self.how == "http"


# ── looking ──────────────────────────────────────────────────────────
def probe_host(host: str) -> str:
    """The address to *dial* for a server bound to ``host``.

    ``--lan`` binds ``0.0.0.0``, which is a wildcard to bind and not an
    address to connect to; dialling it works on Linux and macOS but not
    everywhere, and loopback reaches the same listener on all three.
    """
    return "127.0.0.1" if host in ("", "0.0.0.0", "::", "*") else host


def port_is_free(host: str, port: int) -> bool:
    """Can uvicorn bind ``host:port`` right now?

    Asked by binding rather than by connecting, because those are
    different questions: a socket in TIME_WAIT refuses connections while
    still denying a plain bind. ``SO_REUSEADDR`` is set for the same
    reason uvicorn sets it — otherwise we would report "busy" for a
    minute after a clean shutdown and escalate to SIGKILL for nothing.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host or "0.0.0.0", port))
        return True
    except OSError:
        return False


def _whoami(host: str, port: int, timeout: float) -> dict | None:
    url = f"http://{probe_host(host)}:{port}/api/meta/whoami"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            if resp.status != 200:
                return None
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) else None


def listening_pids(port: int) -> list[int]:
    """PIDs holding a listening socket on ``port``, via ``lsof``.

    Only consulted when the HTTP probe fails. Absent or unhappy ``lsof``
    yields an empty list, which reads as "cannot tell" and refuses.
    """
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5.0, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.split():
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return [p for p in dict.fromkeys(pids) if p != os.getpid()]


def _cmdline(pid: int) -> str:
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _ppid(pid: int) -> int | None:
    try:
        out = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5.0, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return int(out) if re.fullmatch(r"\d+", out) else None


def looks_like_soc(cmdline: str) -> bool:
    return any(m in cmdline for m in _PROC_MARKERS)


def supervisor_of(pid: int, port: int) -> int | None:
    """The uvicorn reloader above ``pid``, if there is one.

    Under ``--reload`` the process answering HTTP is a *child*; killing
    it alone just makes the reloader spawn a replacement and the port
    never frees. So the parent sometimes has to go first — and picking
    the wrong parent means killing whatever launched the server, which
    is usually the user's terminal.

    The test is therefore **ownership of the listening socket**, not the
    parent's name. uvicorn's reloader opens the socket and hands it to
    the worker, so both hold it; anything else that happens to be above
    the process — a shell, an IDE task runner, ``make`` — does not, and
    cannot be mistaken for a reloader however it is spelled.

    Command-line matching alone is not enough here, and the failure is
    not hypothetical: a server started as ``zsh -c "... run_web.py ..."``
    gives the *shell* a command line containing ``run_web.py``. It reads
    as one of ours on every string test and holds no socket at all.
    """
    parent = _ppid(pid)
    if not parent or parent <= 1 or parent == os.getpid():
        return None
    if parent not in listening_pids(port):
        return None
    return parent if looks_like_soc(_cmdline(parent)) else None


def identify(host: str, port: int, *, timeout: float = 1.5) -> Occupant | None:
    """What holds ``host:port``, or ``None`` if it is free.

    Returns an :class:`Occupant` with ``how="http"`` when the listener
    identified itself, ``how="process"`` when it did not answer but the
    process holding the socket is recognisably ours, and ``how="other"``
    for anything else — which the caller must refuse to kill.
    """
    if port_is_free(host, port):
        return None

    body = _whoami(host, port, timeout)
    if body is not None and body.get("app") == APP_IDENT:
        pid = body.get("pid")
        root = body.get("root")
        up = body.get("uptime_s")
        return Occupant(
            pid=int(pid) if isinstance(pid, int) else None,
            root=str(root) if root else None,
            how="http",
            detail=f"answered /api/meta/whoami, up {up}s"
            if up is not None else "answered /api/meta/whoami",
        )

    if body is not None:
        return Occupant(
            pid=None, root=None, how="other",
            detail="an HTTP server that is not Sea of Colours",
        )

    pids = listening_pids(port)
    for pid in pids:
        cmd = _cmdline(pid)
        if looks_like_soc(cmd):
            return Occupant(
                pid=pid, root=None, how="process",
                detail=f"not answering HTTP; pid {pid} is {cmd[:80]!r}",
            )
    if pids:
        return Occupant(
            pid=pids[0], root=None, how="other",
            detail=f"pid {pids[0]} — {_cmdline(pids[0])[:80]!r}",
        )
    return Occupant(
        pid=None, root=None, how="other",
        detail="something is bound to the port but it cannot be identified "
               "(lsof unavailable, or the owner belongs to another user)",
    )


# ── acting ───────────────────────────────────────────────────────────
def _signal(pid: int, sig: int) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return True  # already gone is the outcome we wanted
    except PermissionError:
        return False


def _wait_free(host: str, port: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if port_is_free(host, port):
            return True
        time.sleep(0.15)
    return port_is_free(host, port)


def stop(occupant: Occupant, host: str, port: int, *, log=print) -> bool:
    """SIGTERM the occupant (and its reloader), escalating if it clings.

    Order matters: the supervisor goes first, because a reloader that
    outlives its worker immediately replaces it.
    """
    if occupant.pid is None:
        return False
    targets = []
    sup = supervisor_of(occupant.pid, port)
    if sup is not None:
        targets.append(sup)
    targets.append(occupant.pid)

    for pid in targets:
        if not _signal(pid, signal.SIGTERM):
            log(f"[soc] --replace: not permitted to signal pid {pid}")
            return False
    if _wait_free(host, port, time.monotonic() + TERM_GRACE_S):
        return True

    log(f"[soc] --replace: pid {targets[-1]} ignored SIGTERM — escalating")
    # Re-read the holders: a reloader we terminated may have handed the
    # socket to a worker that is not in `targets`.
    for pid in dict.fromkeys(targets + listening_pids(port)):
        _signal(pid, signal.SIGKILL)
    return _wait_free(host, port, time.monotonic() + KILL_GRACE_S)


def replace(host: str, port: int, *, log=print) -> bool:
    """Free ``host:port`` for us, or raise :class:`ReplaceRefused`.

    ``True`` when a server was stopped, ``False`` when the port was
    already free (so the caller can stay quiet about it).
    """
    occupant = identify(host, port)
    if occupant is None:
        return False

    if occupant.how == "other":
        raise ReplaceRefused(
            f"port {port} is busy and it is not a Sea of Colours server: "
            f"{occupant.detail}. Refusing to kill it — use --port to run "
            f"somewhere else, or stop it yourself."
        )
    if occupant.pid is None:
        raise ReplaceRefused(
            f"port {port} is held by a Sea of Colours server that did not "
            f"report its pid ({occupant.detail}). Stop it by hand."
        )
    if occupant.pid == os.getpid():
        # Unreachable from the launcher, which has not bound anything —
        # but a test harness or an embedding caller can get here, and
        # "free the port" would be satisfied by suicide.
        raise ReplaceRefused(
            f"port {port} is served by this very process — nothing to replace."
        )

    where = f" from {occupant.root}" if occupant.root else ""
    log(
        f"[soc] --replace: stopping the Sea of Colours server on {port} "
        f"(pid {occupant.pid}{where}) — {occupant.detail}"
    )
    if occupant.how == "process":
        log(
            "[soc]   note: it never answered, so this was identified by its "
            "command line rather than by asking it."
        )
    if not stop(occupant, host, port, log=log):
        raise ReplaceRefused(
            f"could not free port {port} — pid {occupant.pid} survived "
            f"SIGKILL, or another process took the port immediately."
        )
    log(f"[soc] --replace: port {port} is free")
    return True
