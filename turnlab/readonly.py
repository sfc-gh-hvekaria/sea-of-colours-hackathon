"""A store the lab cannot write through.

The lab reads real seasons. On a Snowflake-backed server that means the
board on screen is being read out of the live account — the same rows a
running game is using. Reading them is the whole point and is harmless;
writing anything back would not be, and "we were careful" is not a
guarantee. So the lab never holds a real store handle. It holds this,
which forwards reads and raises on everything else.

The check is a deny-list of mutating verbs first, then an allow-list of
read-shaped prefixes, rather than either alone. A bare allow-list would
wave through ``get_or_create_session``; a bare deny-list would wave
through anything named in a way nobody predicted. Requiring a name to
be both non-mutating AND read-shaped means a method the lab has never
seen fails closed, which is the correct direction for a mistake that
would otherwise land in someone's Snowflake account.

Boards are frozen history. There is no legitimate reason for the lab to
write to a past season, so there is no escape hatch here on purpose —
when headless seasons are commissioned they will take a real store
directly and write under their own new session ids, which is a
different door.
"""

from __future__ import annotations

from typing import Any


class LabWriteBlocked(RuntimeError):
    """Raised when lab code tries to mutate a real season."""


#: Verbs that mean "this changes something". Checked as substrings, so
#: ``get_or_create`` and ``save_session`` are both caught.
_MUTATING = (
    "save", "write", "put", "upsert", "insert", "update", "delete",
    "drop", "create", "append", "record", "commit", "register",
    "apply", "advance", "resolve", "run", "set_", "purge", "reset",
    "init", "deploy", "migrate", "close",
)

#: How a read is spelled. A method must look like one of these to pass.
_READ_SHAPED = (
    "list_", "get_", "read_", "fetch_", "load_", "has_", "is_",
    "exists", "describe_", "count_", "latest_", "find_",
)


class ReadOnlyStore:
    """Forwards reads to ``inner``; refuses anything that could mutate."""

    __slots__ = ("_inner",)

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)

    @property
    def inner(self) -> Any:
        """The wrapped store. Deliberately awkward to reach."""
        return self._inner

    def __getattr__(self, name: str) -> Any:
        lowered = name.lower()
        if any(verb in lowered for verb in _MUTATING):
            raise LabWriteBlocked(
                f"the lab tried to call {name!r} on a real season. Lab "
                f"boards are frozen history and are opened read-only; if "
                f"something needs to write, it needs its own session, not "
                f"this one."
            )
        if not name.startswith("_") and not any(
            lowered.startswith(p) or p in lowered for p in _READ_SHAPED
        ):
            raise LabWriteBlocked(
                f"{name!r} is not a recognised read on a lab store, so it "
                f"is refused rather than assumed safe. If it really is a "
                f"read, add its prefix to _READ_SHAPED."
            )
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise LabWriteBlocked(f"cannot set {name!r} on a read-only lab store")

    def __repr__(self) -> str:
        return f"ReadOnlyStore({type(self._inner).__name__})"


def wrap(store: Any) -> ReadOnlyStore:
    """Open ``store`` read-only for the lab.

    Idempotent, so a caller that is unsure whether it already has a
    read-only handle can wrap again without stacking proxies.
    """
    if isinstance(store, ReadOnlyStore):
        return store
    return ReadOnlyStore(store)
