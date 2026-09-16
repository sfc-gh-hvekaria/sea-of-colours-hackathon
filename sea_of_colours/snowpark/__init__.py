"""Snowflake-facing engine wrappers.

The pure-Python engine in :mod:`sea_of_colours.game` is unchanged; this
package adds a thin **storage-agnostic** layer around it so the same code
can run inside Snowpark Python stored procedures (Phase 2/3) or in unit
tests (against an in-memory store that mirrors the SOC_* table shape).

Public entry points (see :mod:`sea_of_colours.snowpark.engine`):

* ``init_session(store, seed, width, height)``
* ``submit_policy(store, session_id, player, moves)``
* ``run_night(store, session_id)`` — runs :class:`NightSimulator` then
  persists every replay frame + log entry through the store.
* ``get_view(store, session_id, player)`` — the agent-friendly JSON
  payload described in the plan (HUD + filtered tile projections + ASCII
  map + entity detail + recent log).
* ``get_observer(store, session_id)``
* ``get_replay(store, session_id, day_from, day_to)``
* ``get_log(store, session_id, day_from, day_to, limit)``
* ``get_inventory(store, session_id, player)``
* ``get_session_status(store, session_id)``
* ``list_sessions(store)``

These mirror the SOC_* stored procedures one-for-one. The Snowpark proc
handlers in :mod:`sea_of_colours.snowpark.procs` simply forward each call
to the function above, paired with a :class:`SnowparkSocStore` that
writes the same rows we test against in-process.
"""

from sea_of_colours.snowpark.engine import (
    get_inventory,
    get_log,
    get_observer,
    get_replay,
    get_session_status,
    get_view,
    init_session,
    list_sessions,
    run_night,
    submit_policy,
)
from sea_of_colours.snowpark.store import InMemorySocStore, SocStore

__all__ = [
    "InMemorySocStore",
    "SocStore",
    "get_inventory",
    "get_log",
    "get_observer",
    "get_replay",
    "get_session_status",
    "get_view",
    "init_session",
    "list_sessions",
    "run_night",
    "submit_policy",
]
