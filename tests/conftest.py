"""Pytest hooks shared across every ``tests/`` module.

The v0.8.0 ORBIT phase opens at session start AND between every
PRAXIS night (RULEBOOK §4). The legacy test suite was written before
the orbit phase existed and expects ``init_session`` to land in
PLANNING and every night to flip straight back to PLANNING. To keep
those tests green without rewriting every fixture we:

1. Default ``skip_initial_orbit=True`` on ``init_session`` and
   ``GameSession.new`` so new sessions still open in PLANNING.
2. Monkey-patch ``NightSimulator.run`` so after each night completes,
   if the simulator left the session in ORBIT we immediately settle
   an empty Orbit and flip to PLANNING.

Dedicated v0.8.0 orbit tests (``test_orbit_v1.py``) bypass this
patching by calling the resolver directly or opting back in to the
initial orbit phase. Live behaviour is unchanged for the FastAPI app
and the Snowflake procs — those are exercised in integration tests
that explicitly drive the orbit flow.

v1.13 — that auto-settled orbit now **empties the vault**: RED ships
and GREEN is disposed of automatically. Legacy tests asserting "the
night banked N parcels" against ``hoard_squares`` were reading a vault
the harness had already settled, so they should use
:func:`banked_parcels` below instead.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sea_of_colours.game import session as _session_module
from sea_of_colours.game import simulator as _simulator_module
from sea_of_colours.snowpark import engine as _engine_module


def banked_parcels(sess: Any, player: str) -> List[Dict[str, Any]]:
    """Every parcel the seat banked this session, settled or not.

    v1.13 — settlement moves parcels out of ``hoard_squares``: RED into
    ``shipped_squares``, and GREEN into it too (tagged with its origin
    tile, scored as a penalty). A test that resolves a night through the
    harness therefore sees an empty vault even though the harvest worked.

    Use this wherever the assertion is about *what the night produced*
    rather than about the vault's steady state. Provenance keys survive
    settlement untouched, so per-parcel checks read the same either way.
    """
    return list(sess.hoard_squares.get(player, []) or []) + list(
        sess.shipped_squares.get(player, []) or []
    )


_orig_new = _session_module.GameSession.new
_orig_init = _engine_module.init_session
_orig_night_run = _simulator_module.NightSimulator.run
# v0.9.2 — stash the truly-original ``try_drop_unit`` on the class so
# downstream tests can call it without going through the legacy
# memory-stamping shim below. The ``hasattr`` guard makes this
# idempotent under pytest's conftest re-imports (otherwise a second
# import would capture the already-patched version).
if not hasattr(_session_module.GameSession, "_v092_orig_try_drop"):
    _session_module.GameSession._v092_orig_try_drop = (  # type: ignore[attr-defined]
        _session_module.GameSession.try_drop_unit
    )
_orig_try_drop = _session_module.GameSession._v092_orig_try_drop  # type: ignore[attr-defined]


def _patched_new(cls, *args, **kwargs):
    # Force-skip the orbit phase for the legacy test suite. The
    # downstream :func:`init_session` always forwards the False
    # default explicitly, so :pyfunc:`dict.setdefault` doesn't help
    # — we override unconditionally here.
    kwargs["skip_initial_orbit"] = True
    return _orig_new.__func__(cls, *args, **kwargs)


def _patched_init(*args, **kwargs):
    kwargs["skip_initial_orbit"] = True
    return _orig_init(*args, **kwargs)


def _patched_night_run(self, sess, queues):
    """Run the night as usual, then auto-skip the dawn ORBIT phase.

    This preserves the pre-v0.8.0 semantics that tests assume:
    every PRAXIS night flips the session back to PLANNING ready for
    the next ``stash_policy`` call.

    v0.9.6 — iterate ``sess.players`` instead of the legacy global
    ``PLAYERS`` so 3- and 4-seat sessions still have their orbit
    auto-settled by the test harness (they were silently leaving
    the orbit half-resolved before).
    """
    _orig_night_run(self, sess, queues)
    if sess.phase == _session_module.Phase.ORBIT:
        from sea_of_colours.game.orbit_resolver import OrbitResolver
        seats = tuple(sess.players)
        sess.pending_orbit_actions = {p: [] for p in seats}
        OrbitResolver().run(sess, {p: [] for p in seats})


def _patched_try_drop(
    self, owner, harvester_id, x, y, harvest_budget=0, live_override=None,
    **kwargs,
):
    """Bypass the v0.9.2 fog-of-war landing check for legacy tests.

    The new rule (harvester drops require live/echo on the target
    cell) breaks pre-v0.9.2 fixtures that drop straight out of orbit
    without any probe vision setup. We pre-stamp a memory entry for
    ``(x, y)`` (so the check passes) then delegate to the original
    implementation. Tests that explicitly assert the fog-of-war
    rule (``test_v092_*``) build their own session without going
    through this patch.

    Every keyword past ``live_override`` is forwarded blind. It used to
    name ``emp_blocked_cells`` explicitly, and the day a second gate
    joined it (``snap_hot_cells``, v1.36) that shim swallowed the new
    kwarg and every drop in the legacy suite raised ``TypeError`` — a
    hundred red tests for a change that had touched none of them. A
    passthrough cannot fall behind the real signature again.
    """
    from sea_of_colours.game.session import _xy_key, cell_to_paint
    if 0 <= x < self.width and 0 <= y < self.height:
        mem = self.memory_tiles.setdefault(owner, {})
        k = _xy_key(x, y)
        if k not in mem:
            mem[k] = {
                "paint": dict(cell_to_paint(self.grid[y][x])),
                "stale": True,
                "_legacy_drop_stamp": True,
            }
    return _orig_try_drop(
        self, owner, harvester_id, x, y, harvest_budget,
        live_override=live_override,
        **kwargs,
    )


_session_module.GameSession.new = classmethod(_patched_new)  # type: ignore[assignment]
_engine_module.init_session = _patched_init  # type: ignore[assignment]
_simulator_module.NightSimulator.run = _patched_night_run  # type: ignore[assignment]
_session_module.GameSession.try_drop_unit = _patched_try_drop  # type: ignore[assignment]
