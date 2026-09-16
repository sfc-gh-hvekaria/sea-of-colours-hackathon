"""Make one agent's turn independent of the turn before it.

The V12 harness keeps module-level state between turns on purpose — it
remembers where it saw hazards, which cells an enemy landed on, what
band a rival showed before it armed. In a season that memory is the
point. In the lab it is contamination: casting two agents onto the same
frozen night, or the same agent twice, must give the same answer both
times, and it will not if the second run can see the first one's
memories.

This module is deliberately the only place that knows the list, so that
when a fork adds a cache there is one file to update rather than a
scattering of ``.clear()`` calls in whatever ran most recently.
"""

from __future__ import annotations

import sys
from typing import Sequence

_V12 = "sea_of_colours.orchestrator_2.harnesses.tabula_v12"

#: Every cache below, as ``(module, attribute)``. One list, so a fork
#: that adds a cache has one file to update rather than a scattering of
#: ``.clear()`` calls in whatever ran most recently.
_CACHES: tuple[tuple[str, str], ...] = (
    (f"{_V12}.hazard_memory", "_CACHE"),
    (f"{_V12}.hazard_memory", "_BLUE_CACHE"),
    (f"{_V12}.frontier", "_ENEMY_LANDINGS"),
    (f"{_V12}._v7.opponent_weapons", "_MEMORY_STORE"),
    (f"{_V12}._v7.memory", "_IN_MEMORY_STORE"),
    (f"{_V12}._v7.harness", "_SNAPSHOTS"),
)


def evict_session(session_id: str) -> int:
    """Forget one session's harness memory, and nobody else's.

    This is what the lab uses, and it is the safe half of
    :func:`reset_harness_caches`. Every cache above is a dict keyed by
    the session id (usually ``session::player``, sometimes a tuple, in
    one case with the day appended), so a session's entries can be
    picked out and dropped without touching a live game's.

    That distinction is the whole reason this function exists. Clearing
    the caches wholesale from inside the web server would take a running
    agent's hazard memory with it, and the player would see an agent
    that suddenly forgot the board with nothing in any log to explain
    it. Eviction cannot do that.

    Note the lab does not need this for *correctness* — it plans in a
    scratch clone with a fresh id, so the caches are empty for that key
    before it starts. This is housekeeping: without it, a long-lived
    server accumulates one dead entry per plan.

    Returns the number of entries dropped, so a test can prove it did
    something rather than silently matching nothing.
    """
    sid = str(session_id or "")
    if not sid:
        return 0

    def _mentions(key: object) -> bool:
        if isinstance(key, str):
            return sid in key
        if isinstance(key, tuple):
            return any(isinstance(p, str) and sid in p for p in key)
        return False

    dropped = 0
    for mod_path, attr in _CACHES:
        try:
            mod = __import__(mod_path, fromlist=["_"])
        except Exception:
            continue
        cache = getattr(mod, attr, None)
        if not isinstance(cache, dict):
            continue
        for key in [k for k in cache if _mentions(k)]:
            # Popped in place rather than rebound: rebinding the module
            # attribute would leave any ``from x import _CACHE`` alias
            # pointing at the old object, which fails silently and looks
            # exactly like a nondeterministic agent.
            cache.pop(key, None)
            dropped += 1
    return dropped


class LabIsolationError(RuntimeError):
    """Raised when clearing shared state would disturb a live game."""


def reset_harness_caches(*, force: bool = False) -> None:
    """Clear every in-process cache the V12 harness accumulates.

    **This mutates state that is shared with any game running in the
    same process.** It is not a store write, which is exactly why it is
    easy to miss: the caches it clears are module-level globals in the
    harness, so wiping them mid-season would take a live agent's hazard
    memory and enemy-landing history with it. The player would see an
    agent that suddenly forgot the board, with nothing in any log to
    explain it.

    So it refuses to run inside the web server. ``server.app`` being
    imported is the honest signal that this process may be serving real
    games, and a lab that cannot be sure is a lab that should not
    touch shared memory. Casting agents belongs in a process of its
    own — or against a private copy — and that constraint is enforced
    here rather than left as a comment.

    ``force`` exists for tests, which import both halves of the repo
    and are not serving anyone.

    Tolerant of missing modules by design: a fork is free to delete any
    of these, and the lab should not refuse to run someone's agent
    because they tidied up. Caches are cleared in place rather than
    rebound, because rebinding the module attribute would leave any
    ``from x import _CACHE`` alias pointing at the old object — which
    fails silently and looks exactly like a nondeterministic agent.
    """
    if not force and "server.app" in sys.modules:
        raise LabIsolationError(
            "refusing to clear harness caches inside the web server: "
            "these are module-level globals shared with any live game, "
            "so wiping them would erase a running agent's memory. Cast "
            "agents from a separate process."
        )

    def _clear(mod_path: str, *attrs: str, call: Sequence[str] = ()) -> None:
        try:
            mod = __import__(mod_path, fromlist=["_"])
        except Exception:
            return
        for attr in attrs:
            cache = getattr(mod, attr, None)
            if hasattr(cache, "clear"):
                cache.clear()
        for fn_name in call:
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass

    _clear(f"{_V12}.hazard_memory", "_CACHE", "_BLUE_CACHE")
    _clear(f"{_V12}.frontier", "_ENEMY_LANDINGS", call=["reset"])
    _clear(f"{_V12}.option_economics", call=["reset_halo_trace"])
    _clear(f"{_V12}._v7.opponent_weapons", "_MEMORY_STORE")
    _clear(f"{_V12}._v7.memory", "_IN_MEMORY_STORE")
    _clear(f"{_V12}._v7.harness", "_SNAPSHOTS")
