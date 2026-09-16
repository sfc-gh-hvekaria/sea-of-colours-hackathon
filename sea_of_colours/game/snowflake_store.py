"""SessionStore implementation backed by SOC_* tables.

Wraps :class:`~sea_of_colours.snowpark.engine` so the existing
:class:`SessionStore` protocol works against a Snowflake (or in-memory
SOC_*) backend. Use this when legacy code expects a :class:`GameSession`
in hand; new code should prefer talking to the engine module directly so
it can take advantage of the structured per-table reads.

Wire-up::

    from sea_of_colours.game.snowflake_store import SnowflakeStore
    STORE = SnowflakeStore()  # uses the configured backend
"""

from __future__ import annotations

from typing import List, Optional

from sea_of_colours.game.session import GameSession
from sea_of_colours.snowpark.backend import get_store
from sea_of_colours.snowpark.engine import save_session_full
from sea_of_colours.snowpark.store import SocStore


class SnowflakeStore:
    """SessionStore backed by the SOC_* schema (via :class:`SocStore`)."""

    def __init__(self, store: Optional[SocStore] = None) -> None:
        self._store = store

    @property
    def store(self) -> SocStore:
        if self._store is None:
            self._store = get_store()
        return self._store

    def save(self, sess: GameSession) -> None:
        save_session_full(self.store, sess)

    def load(self, sid: str) -> Optional[GameSession]:
        row = self.store.load_session(sid)
        if row is None:
            return None
        blob = row.get("json_state")
        if not blob:
            return None
        return GameSession.from_dict(blob)

    def delete(self, sid: str) -> bool:
        # SOC schema currently has no DELETE op — destructive removal
        # belongs in a separate admin path. Return False so callers
        # treat the session as still present.
        return False

    def list_ids(self) -> List[str]:
        return [r["session_id"] for r in self.store.list_sessions()]

    def latest(self) -> Optional[GameSession]:
        latest = self.store.latest_session()
        if latest is None:
            return None
        return self.load(latest["session_id"])
