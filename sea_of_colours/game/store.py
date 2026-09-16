"""Session storage layer.

The orchestrator talks to a :class:`SessionStore` so we can swap backends
without changing FastAPI routes. For now the only implementation is
:class:`InMemoryStore`; a Snowflake backend is intended to slot in here
later by mapping :meth:`GameSession.to_dict` / :meth:`GameSession.from_dict`
to a row write / read.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Protocol

from sea_of_colours.game.session import GameSession


class SessionStore(Protocol):
    """Persistence surface for game sessions."""

    def save(self, sess: GameSession) -> None: ...
    def load(self, sid: str) -> Optional[GameSession]: ...
    def delete(self, sid: str) -> bool: ...
    def list_ids(self) -> List[str]: ...
    def latest(self) -> Optional[GameSession]: ...


class InMemoryStore:
    """Process-local registry. Sessions are lost on server restart."""

    def __init__(self) -> None:
        self._sessions: dict[str, GameSession] = {}
        self._order: list[str] = []

    def save(self, sess: GameSession) -> None:
        if sess.session_id not in self._sessions:
            self._order.append(sess.session_id)
        self._sessions[sess.session_id] = sess

    def load(self, sid: str) -> Optional[GameSession]:
        return self._sessions.get(sid)

    def delete(self, sid: str) -> bool:
        if sid in self._sessions:
            del self._sessions[sid]
            try:
                self._order.remove(sid)
            except ValueError:
                pass
            return True
        return False

    def list_ids(self) -> List[str]:
        return list(self._order)

    def latest(self) -> Optional[GameSession]:
        if not self._order:
            return None
        return self._sessions.get(self._order[-1])


STORE: SessionStore = InMemoryStore()
"""Module-level singleton used by the FastAPI app."""


def iter_sessions() -> Iterable[GameSession]:
    """Iterate currently-known sessions (for latest-session UI lookups)."""
    for sid in STORE.list_ids():
        sess = STORE.load(sid)
        if sess is not None:
            yield sess


# Snowflake backend will live here once it lands:
#
# class SnowflakeStore(SessionStore):
#     """Persist GameSession.to_dict() rows into a Snowflake table.
#
#     Implement save/load/delete/list_ids/latest to translate to / from
#     :meth:`GameSession.to_dict` rows. The protocol surface is stable.
#     """
#     ...
