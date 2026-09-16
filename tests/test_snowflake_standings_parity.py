"""The Snowflake picker must score identically to the results screen.

Background: ``SnowparkSocStore.bulk_session_scores`` used to delegate to
the :sql:`SOC_SESSION_STANDINGS` view, which is a *second* copy of the
scoring rules written in SQL::

    SUM(LEAST(255, GREATEST(0, COALESCE(origin_purity, 0))))

That copy drifted from :func:`compute_player_score` in two ways. It
applies no RED tier multiplier, and — far worse — GREEN parcels are
auto-disposed at settlement and appended to SHIPPED carrying their GREEN
origin tile (RULEBOOK §4.7), so a bare purity sum *credits* each one
``+255`` where the scorer *charges* ``-100``. A 355-point error per
parcel, which is enough to invert a finished season: Serpens_Lattice
(46615fbd) showed p1 1436 / p2 1853 in the picker against a true
1527 / 1418 on the endgame card, i.e. the picker crowned the loser.

Only the Snowflake store had the duplicate — the memory and file stores
both call the real scorer — so every offline season and the whole test
suite agreed while live Snowflake games quietly did not. These tests
pin the parity from the Snowflake side, offline, via a fake session.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from sea_of_colours.game.session import (
    GREEN_ENDGAME_PENALTY,
    Tile,
    compute_player_score,
)
from sea_of_colours.snowpark.snowpark_store import SnowparkSocStore

SESSION_ID = "sess-standings"


class _Row:
    """Row stand-in exposing the ``as_dict`` hook ``_row_to_dict`` prefers."""

    def __init__(self, mapping: Dict[str, Any]) -> None:
        self._mapping = mapping

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._mapping)


class _FakeDF:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def collect(self) -> List[Any]:
        return list(self._rows)


def _parcel(owner: str, slot: int, tile: Tile, purity: int, tier: str) -> Dict[str, Any]:
    """A parcel row shaped exactly as Snowflake hands it back.

    ``payload`` is a VARIANT, which the connector returns as JSON *text*,
    and it carries the catapult-stamped ``effective_purity`` /
    ``score_tier`` the live session uses.
    """
    return {
        "session_id": SESSION_ID,
        "owner": owner,
        "origin_tile": int(tile),
        "origin_purity": purity,
        "payload": json.dumps(
            {
                "tile_at_harvest": int(tile),
                "purity_at_harvest": purity,
                "effective_purity": purity,
                "score_tier": tier,
                "slot": slot,
            },
            indent=2,
        ),
    }


# p1 banks two real RED parcels and jettisons one GREEN; p2 ships nothing
# but a single jettisoned GREEN. Under the old view p2 scored +255 and
# could outrank p1; under the scorer p2 is deep negative.
SHIPPED = [
    _parcel("p1", 0, Tile.RED, 255, "pure"),
    _parcel("p1", 1, Tile.RED, 120, "vein"),
    _parcel("p1", 2, Tile.GREEN, 255, "green"),
    _parcel("p2", 0, Tile.GREEN, 255, "green"),
]
HOARD = [_parcel("p1", 0, Tile.RED, 60, "vein")]


class _FakeSession:
    """Answers the three grouped SELECTs ``bulk_session_scores`` issues."""

    def __init__(self, phase: str = "season_complete") -> None:
        self.statements: List[str] = []
        self._phase = phase

    def sql(self, query: str) -> _FakeDF:
        self.statements.append(query)
        if "SOC_GAME_SESSION" in query:
            return _FakeDF([_Row({"session_id": SESSION_ID, "phase": self._phase})])
        if "SOC_SHIPPED_PARCEL" in query:
            return _FakeDF([_Row(r) for r in SHIPPED])
        if "SOC_HOARD_PARCEL" in query:
            return _FakeDF([_Row(r) for r in HOARD])
        return _FakeDF([])


def _expected(owner: str, *, is_complete: bool = True) -> int:
    """Score the same parcels through the canonical scorer."""
    unwrap = [json.loads(r["payload"]) for r in SHIPPED if r["owner"] == owner]
    hoard = [json.loads(r["payload"]) for r in HOARD if r["owner"] == owner]
    return compute_player_score(unwrap, hoard, is_complete=is_complete)


def test_picker_matches_the_canonical_scorer() -> None:
    """Every seat's picker score must equal ``compute_player_score``."""
    store = SnowparkSocStore(_FakeSession())

    scores = store.bulk_session_scores()[SESSION_ID]

    assert scores == {"p1": _expected("p1"), "p2": _expected("p2")}


def test_disposed_green_is_charged_not_credited() -> None:
    """The regression itself: GREEN in SHIPPED costs 100, never pays 255."""
    store = SnowparkSocStore(_FakeSession())

    scores = store.bulk_session_scores()[SESSION_ID]

    # p2 shipped exactly one auto-disposed GREEN parcel and nothing else.
    assert scores["p2"] == -GREEN_ENDGAME_PENALTY
    # The old view summed raw purity and would have paid +255 here.
    naive_purity_sum = sum(
        r["origin_purity"] for r in SHIPPED if r["owner"] == "p2"
    )
    assert scores["p2"] != naive_purity_sum


def test_red_carries_its_tier_multiplier() -> None:
    """A bare purity sum is not a score — the RED tiers must be weighted."""
    store = SnowparkSocStore(_FakeSession())

    scores = store.bulk_session_scores()[SESSION_ID]

    naive_purity_sum = sum(
        r["origin_purity"] for r in SHIPPED if r["owner"] == "p1"
    )
    assert scores["p1"] != naive_purity_sum


def test_scoreboard_does_not_read_the_standings_view() -> None:
    """Guard against the SQL duplicate being reintroduced.

    The view still exists for ad-hoc querying, but nothing user-facing
    may score off it — that is what let the two surfaces disagree.
    """
    session = _FakeSession()

    SnowparkSocStore(session).bulk_session_scores()

    assert not any("SOC_SESSION_STANDINGS" in s for s in session.statements)


def test_vault_red_fire_sale_waits_for_season_end() -> None:
    """The 50% vault-RED fire-sale only realises once the season is over."""
    live = SnowparkSocStore(_FakeSession(phase="planning")).bulk_session_scores()
    done = SnowparkSocStore(_FakeSession()).bulk_session_scores()

    assert live[SESSION_ID]["p1"] == _expected("p1", is_complete=False)
    assert done[SESSION_ID]["p1"] == _expected("p1", is_complete=True)
    assert done[SESSION_ID]["p1"] > live[SESSION_ID]["p1"]
