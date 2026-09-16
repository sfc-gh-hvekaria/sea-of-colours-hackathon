"""An agent's journal is its own, on every backend and in every fork.

Issue 38. ``SOC_AGENT_MEMORY`` is a hybrid table keyed
``(session_id, player, kind)``, and a range predicate on the *trailing*
key column — ``kind LIKE 'arena:day%'`` — made the scan return the next
player's rows too, despite ``player = ?`` being an equality filter right
beside it. Entries are keyed by day on the way into the cache, so a
rival's day-3 note simply overwrote your own and the agent reflected on
a night it never played.

Two things kept it fixed and both are easy to undo by tidying:

* the query carries no range predicate on ``kind``, and
* the seat is re-checked in Python, because nothing in the result can be
  trusted to satisfy a predicate the scan dropped.

The existing regression test covers the lab's own reader. This covers
``_v7/memory.py``, which is the copy **every fork inherits** — so a fork
that hand-optimises the query back into a ``LIKE`` goes red here rather
than quietly leaking its opponent's plans into its own prompt months
later, on Snowflake only, where nobody is looking.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

_HARNESSES = (
    pathlib.Path(__file__).resolve().parent.parent
    / "sea_of_colours" / "orchestrator_2" / "harnesses"
)


def _memory_modules():
    """Every fork's ``_v7.memory``, by harness directory name.

    Discovered rather than listed: the point of the test is that a fork
    inherits the fix, so a fork added tomorrow has to be covered without
    anyone remembering to add it here.
    """
    found = []
    for path in sorted(_HARNESSES.glob("*/_v7/memory.py")):
        fork = path.parent.parent.name
        found.append((fork, importlib.import_module(
            f"sea_of_colours.orchestrator_2.harnesses.{fork}._v7.memory"
        )))
    return found


_MODULES = _memory_modules()
_IDS = [fork for fork, _ in _MODULES]


class _Contaminated:
    """A Snowpark session that reproduces the hybrid-table bug.

    Returns p2's rows alongside p1's for a query asking only for p1 —
    which is exactly what the real table did, and the reason the seat is
    re-checked after the scan rather than trusted.
    """

    def __init__(self):
        self.queries: list[str] = []

    def sql(self, query, params=None):
        self.queries.append(query)
        rows = []
        for seat, day, note in (
            ("p1", 1, "mine: opened west"),
            ("p1", 2, "mine: took the seam"),
            ("p2", 2, "theirs: they are blind on the east flank"),
            ("p2", 3, "theirs: EMP held back for the pure"),
        ):
            rows.append({
                "PLAYER": seat,
                "KIND": f"arena:day{day}",
                "PAYLOAD": {"intent": note},
            })
        # Non-journal rows share the key space and must be ignored too.
        rows.append({
            "PLAYER": "p1", "KIND": "arena:probe_map", "PAYLOAD": {"x": 1},
        })
        return _Result(rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


@pytest.mark.parametrize("fork,mem", _MODULES, ids=_IDS)
def test_a_seat_never_reads_the_next_seats_journal(fork, mem, monkeypatch):
    session = _Contaminated()
    monkeypatch.setattr(
        "sea_of_colours.snowpark.backend.snowpark_session_for",
        lambda store: session,
    )
    mem.clear_in_memory_store()

    mem._hydrate_from_snowflake("S1", "p1", store=object())

    landed = dict(mem._IN_MEMORY_STORE)
    # Assert the happy path FIRST. The hydrate swallows every exception,
    # so a test that only checked "no p2 entries" would pass just as
    # cheerfully if the whole thing had thrown and loaded nothing.
    assert landed, f"{fork}: nothing was hydrated at all — the test is vacuous"
    assert set(landed) == {mem._key("S1", "p1", 1), mem._key("S1", "p1", 2)}, (
        f"{fork}: hydrated {sorted(landed)}"
    )
    for key, payload in landed.items():
        assert "theirs" not in payload.get("intent", ""), (
            f"{fork}: {key} carries the opponent's note — issue 38 is back"
        )


@pytest.mark.parametrize("fork,mem", _MODULES, ids=_IDS)
def test_the_query_puts_no_range_predicate_on_the_key(fork, mem, monkeypatch):
    """The half of the fix that a tidy-up would remove first.

    Filtering ``kind`` in Python looks redundant next to a WHERE clause
    that could do it, which is precisely why the comment above the query
    is long and why this test exists.
    """
    session = _Contaminated()
    monkeypatch.setattr(
        "sea_of_colours.snowpark.backend.snowpark_session_for",
        lambda store: session,
    )
    mem.clear_in_memory_store()

    mem._hydrate_from_snowflake("S1", "p1", store=object())

    assert session.queries, f"{fork}: no query was issued"
    for sql in session.queries:
        flat = " ".join(sql.split()).upper()
        assert not re.search(r"\bKIND\b[^,)]*\b(LIKE|STARTSWITH)\b", flat), (
            f"{fork}: the query range-scans KIND, the trailing column of a "
            f"hybrid-table primary key. That returns the next seat's rows. "
            f"Filter kind in Python instead — see the docstring on "
            f"_hydrate_from_snowflake and issue 38.\n  {flat}"
        )
        assert not re.search(r"\bSTARTSWITH\s*\(\s*KIND", flat), (
            f"{fork}: same bug via STARTSWITH — {flat}"
        )


def test_every_fork_that_keeps_a_journal_is_covered():
    """The parametrisation is a glob, so an empty one would pass silently."""
    assert _MODULES, (
        "no _v7/memory.py was found under harnesses/ — either the layout "
        "moved or this test is no longer checking anything"
    )
    assert "tabula_v12" in _IDS, "V12 is the copy every fork is cut from"
