"""v1.0 end-of-season state: final settlement orbit, vault-RED-at-loss
scoring, Latin player names, per-seat season tallies, and the
``get_endgame_summary`` payload (direct + HTTP)."""

from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "memory")

import pytest
from fastapi.testclient import TestClient

from sea_of_colours.generator import Tile
from sea_of_colours.game.orbit_resolver import OrbitResolver
from sea_of_colours.game.player_names import (
    SEAT_LABEL,
    generate_player_name,
    generate_player_names,
)
from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.snowpark import backend as soc_backend
from sea_of_colours.snowpark import engine as soc_engine
from sea_of_colours.snowpark.store import InMemorySocStore


# ── Latin player names ───────────────────────────────────────────────
def test_player_name_is_deterministic() -> None:
    assert generate_player_name(4444, "p1") == generate_player_name(4444, "p1")
    # Format is "<Colour> <Animal>".
    name = generate_player_name(4444, "p2")
    parts = name.split(" ")
    assert len(parts) == 2 and parts[0][0].isupper() and parts[1][0].isupper()


def test_generate_player_names_only_names_bots_and_is_unique() -> None:
    seats = ("p1", "p2", "p3", "p4")
    agents = {"p1": "human", "p2": "red_harvest", "p3": "red_harvest", "p4": "red_harvest"}
    names = generate_player_names(4444, seats, agents)
    # Human seat omitted; the three bots are present and distinct.
    assert "p1" not in names
    assert set(names) == {"p2", "p3", "p4"}
    assert len(set(names.values())) == 3


def test_new_session_assigns_bot_names() -> None:
    sess = GameSession.new(
        20, 14, seed=4444,
        players=["p1", "p2"],
        agents={"p1": "human", "p2": "red_harvest"},
    )
    assert "p1" not in sess.player_names
    assert sess.player_names.get("p2")
    # Humans fall back to a seat-colour label.
    assert SEAT_LABEL["p1"] == "WHITE"


# ── Final settlement orbit ───────────────────────────────────────────
def test_final_orbit_no_longer_restricts_what_you_may_buy() -> None:
    """v1.13 — the final orbit used to drop everything except refine /
    ship / green-flush, because those were the only things that still
    mattered. All three are gone and settlement is automatic, so there
    is nothing left to restrict: a seat may buy on the last orbit and
    simply gets no value from it. Wasting your own credits is a legal
    move, not an error."""
    sess = GameSession.new(20, 14, seed=11)
    sess.phase = Phase.ORBIT
    sess.final_orbit = True

    ok, _ = sess.stash_orbit_actions("p1", [{"a": "build_harvester"}])
    assert ok
    kept = sess.pending_orbit_actions["p1"]
    assert len(kept) == 1 and kept[0].tag == "build_harvester"

    ok, _ = sess.stash_orbit_actions(
        "p1", [{"a": "build_probe"}, {"a": "repair", "unit": "harvester_p1"}],
    )
    assert ok
    assert [a.tag for a in sess.pending_orbit_actions["p1"]] == [
        "build_probe", "repair",
    ]

    # An empty submission still locks the seat so the orbit can resolve.
    ok, _ = sess.stash_orbit_actions("p2", [])
    assert ok


def test_final_orbit_resolves_to_season_complete() -> None:
    sess = GameSession.new(20, 14, seed=12)
    sess.phase = Phase.ORBIT
    sess.final_orbit = True
    OrbitResolver().run(sess, {p: [] for p in sess.players})
    assert sess.phase == Phase.SEASON_COMPLETE
    assert sess.is_season_complete()


def test_full_season_runs_final_orbit_then_completes() -> None:
    """Through the (conftest-patched) night auto-skip, a capped season
    still flips ``final_orbit`` on and lands in SEASON_COMPLETE."""
    sess = GameSession.new(20, 14, seed=13, season_day_cap=3)
    for _ in range(3):
        assert sess.phase == Phase.PLANNING
        sess.stash_policy("p1", [])
        sess.stash_policy("p2", [])
        sess.maybe_resolve_if_ready()
    assert sess.final_orbit is True
    assert sess.is_season_complete()


# ── v1.30: the terminal orbit settles itself ─────────────────────────
#
# READ THIS BEFORE TRUSTING A GREEN RUN HERE. `tests/conftest.py`
# monkey-patches `NightSimulator.run` to auto-resolve any ORBIT the night
# leaves behind, which is what keeps the pre-v0.8.0 suite green — and it
# means almost NO test in this repo can see the terminal-orbit gate at
# all. The retirement of that gate passed the entire suite before it was
# written, which is exactly the kind of "green proves nothing" that these
# two tests exist to close. Both deliberately restore the real
# `NightSimulator.run` first.


def _unpatched_night_run(monkeypatch):
    """The real `NightSimulator.run`, with conftest's orbit auto-skip off.

    Do NOT get this by importing conftest. Importing it re-executes the
    module, which re-captures `_orig_night_run` from an already-patched
    class and then patches it AGAIN — so the "original" you get back is
    the wrapper, and every later test in the run inherits a double
    auto-skip. (conftest guards `try_drop_unit` against exactly this and
    says so in a comment; the night hook has no such guard.)

    Instead, walk the wrapper chain that is already installed. Each
    wrapper closes over the function it replaced, so following
    `_orig_night_run` through its globals lands on the genuine method.
    """
    from sea_of_colours.game.simulator import NightSimulator

    fn = NightSimulator.run
    seen = 0
    while getattr(fn, "__name__", "") == "_patched_night_run" and seen < 10:
        fn = fn.__globals__["_orig_night_run"]
        seen += 1
    assert getattr(fn, "__name__", "") == "run", (
        f"could not find the real NightSimulator.run (stopped at {fn!r})"
    )
    monkeypatch.setattr(NightSimulator, "run", fn)
    return NightSimulator


def test_the_last_night_settles_itself_with_nobody_submitting(monkeypatch) -> None:
    """The season must END on the last night, not ask one more question.

    Since v1.13 the terminal orbit has had nothing to decide — RED ships
    and GREEN clears automatically, and any hardware bought there is
    never used. It still made every seat submit an empty orbit before
    anyone could be told who won, which in a 4-player game is three
    people waiting on a fourth to click through an empty screen.
    """
    sim = _unpatched_night_run(monkeypatch)
    sess = GameSession.new(20, 14, seed=13, season_day_cap=2, players=["p1", "p2"])

    sim().run(sess, {p: [] for p in sess.players})     # night 1 of 2
    assert sess.phase == Phase.ORBIT, "a mid-season night still opens orbit"
    OrbitResolver().run(sess, {p: [] for p in sess.players})

    sim().run(sess, {p: [] for p in sess.players})     # the LAST night
    assert sess.final_orbit is True
    assert sess.phase == Phase.SEASON_COMPLETE, (
        "the last night parked in ORBIT waiting for a submission nobody "
        "has a reason to make"
    )
    assert sess.is_season_complete()
    # Nothing is owed by anyone — the gate is gone, not merely pre-filled.
    assert all(v is None for v in sess.pending_orbit_actions.values())


def test_the_automatic_settlement_actually_settles(monkeypatch) -> None:
    """Retiring the gate must not skip the settlement it was gating.

    The failure this guards against is subtle and expensive: end the
    season without running the resolver and the last night's haul is
    never shipped, so every seat's final score silently loses its best
    day. Assert on the CARGO, not just the phase.
    """
    sim = _unpatched_night_run(monkeypatch)
    sess = GameSession.new(20, 14, seed=21, season_day_cap=1, players=["p1", "p2"])

    # Put known cargo in the vault: one RED parcel to be shipped, one
    # GREEN to be disposed of.
    sess.hoard_squares["p1"] = [
        {"tile_at_harvest": int(Tile.RED), "purity_at_harvest": 200},
        {"tile_at_harvest": int(Tile.GREEN), "purity_at_harvest": 0},
    ]

    sim().run(sess, {p: [] for p in sess.players})

    assert sess.phase == Phase.SEASON_COMPLETE
    assert not sess.hoard_squares.get("p1"), (
        "the vault still holds cargo — the terminal settlement did not run"
    )
    assert sess.score_for("p1") > 0, "the last night's RED was never shipped"
    assert sess.catapult_history, "no settlement was recorded for the final day"
    assert sess.catapult_history[-1]["day"] == sess.day


# ── Vault-RED sold at a loss ─────────────────────────────────────────
def test_vault_red_loss_value_and_score() -> None:
    sess = GameSession.new(20, 14, seed=14)
    sess.hoard_squares["p1"] = [
        {"tile_at_harvest": int(Tile.RED), "purity_at_harvest": 200},
        {"tile_at_harvest": int(Tile.RED), "purity_at_harvest": 100},
    ]
    # 50% of raw purity, no tier multiplier: (200 + 100) * 0.5 = 150.
    assert sess.vault_red_loss_value("p1") == pytest.approx(150.0)

    # Not realised mid-season.
    sess.phase = Phase.PLANNING
    assert sess.score_for("p1") == 0
    # Realised once the season is complete.
    sess.phase = Phase.SEASON_COMPLETE
    assert sess.score_for("p1") == 150


# ── Season stats tallies ─────────────────────────────────────────────
def test_season_stats_track_awards_builds_and_blue() -> None:
    sess = GameSession.new(20, 14, seed=15)
    stats = sess.season_stats["p1"]
    # v1.1 — no birth stipend: income starts at the first Orbit entry,
    # so a freshly minted session has awarded nothing yet.
    assert stats["credits_awarded"] == 0

    sess.credits["p1"] = 2000  # enough for a 1500c harvester
    ok, _ = sess.apply_build_harvester("p1")
    assert ok
    assert sess.season_stats["p1"]["harvesters_built"] == 1

    before = sess.season_stats["p1"]["blue_spent"]
    ok, _, _ = sess.debit_blue_purity("p1", 100)
    assert ok
    assert sess.season_stats["p1"]["blue_spent"] == before + 100


def test_season_stats_round_trip() -> None:
    sess = GameSession.new(20, 14, seed=16)
    sess.credits["p1"] += 2000
    sess.apply_build_harvester("p1")
    revived = GameSession.from_dict(sess.to_dict())
    assert revived.season_stats["p1"]["harvesters_built"] == 1
    assert revived.player_names == sess.player_names
    # final_orbit flag round-trips too.
    sess.final_orbit = True
    assert GameSession.from_dict(sess.to_dict()).final_orbit is True


# ── get_endgame_summary (direct, completed season) ───────────────────
def _persist(store: InMemorySocStore, sess: GameSession) -> None:
    store.save_session({
        "session_id": sess.session_id,
        "season_name": sess.season_name,
        "width": sess.width,
        "height": sess.height,
        "seed": sess.seed,
        "day": sess.day,
        "phase": sess.phase.value,
        "json_state": sess.to_dict(),
    })


def test_endgame_summary_shape_for_completed_season() -> None:
    sess = GameSession.new(
        20, 14, seed=17,
        players=["p1", "p2"],
        agents={"p1": "red_harvest", "p2": "red_harvest"},
        season_day_cap=3,
    )
    # Harvest history feeds the per-day RED series + colour totals.
    sess.harvest_log["p1"] = [
        {"tile_at_harvest": int(Tile.RED), "harvested_on_planning_day": 1},
        {"tile_at_harvest": int(Tile.RED), "harvested_on_planning_day": 2},
        {"tile_at_harvest": int(Tile.GREEN), "harvested_on_planning_day": 2},
    ]
    sess.harvest_log["p2"] = [
        {"tile_at_harvest": int(Tile.RED), "harvested_on_planning_day": 1},
    ]
    # p1 shipped a pure parcel; p2 is left holding a green liability.
    # ``shipped_day`` is the key OrbitResolver stamps at settlement.
    # This fixture used to say ``shipped_on_day``, which nothing writes,
    # so the test passed while every real season charted a flat zero.
    sess.shipped_squares["p1"] = [{
        "tile_at_harvest": int(Tile.RED), "purity_at_harvest": 255,
        "effective_purity": 250, "score_tier": "pure", "score": 750,
        "shipped_day": 2,
    }]
    sess.cumulative_shipped_score["p1"] = 750.0
    sess.hoard_squares["p2"] = [
        {"tile_at_harvest": int(Tile.GREEN), "purity_at_harvest": 255},
    ]
    sess.phase = Phase.SEASON_COMPLETE
    sess.final_orbit = True

    store = InMemorySocStore()
    _persist(store, sess)
    summary = soc_engine.get_endgame_summary(store, sess.session_id)

    assert summary["is_season_complete"] is True
    assert summary["days"] == [1, 2, 3]
    players = summary["players"]
    assert len(players) == 2
    # Ranked: p1 (shipped 750) ahead of p2 (green-penalised).
    assert players[0]["seat"] == "p1"
    assert players[0]["rank"] == 1 and players[1]["rank"] == 2
    assert all(p["is_human"] is False for p in players)
    assert all(p["name"] for p in players)

    # Per-day cumulative RED series is the right length + monotonic.
    p1_series = summary["red_by_day"]["p1"]
    assert len(p1_series) == 3
    assert p1_series == sorted(p1_series)
    assert p1_series[-1] == 2  # two red parcels total for p1

    # Manifest carries the shipped parcel, stamped with the day it
    # actually shipped rather than a phantom 0.
    assert any(
        r["value"] == 750 and r["owner"] == "p1" and r["day"] == 2
        for r in summary["manifest"]
    )

    # The cumulative shipped-score series must bank the parcel on the
    # night it shipped and carry it forward — a flat zero line here is
    # the regression this pins.
    assert summary["shipped_by_day"]["p1"] == [0, 750, 750]

    # p2's green is a standing -100 liability.
    p2 = next(p for p in players if p["seat"] == "p2")
    assert p2["green_held"] == 1
    assert p2["breakdown"]["green_penalty"] == 100


# ── extraction efficiency (v1.20) ────────────────────────────────────
def _genesis_red(sess: GameSession) -> tuple[int, int]:
    """``(value, cells)`` of RED as generated, computed independently.

    Deliberately re-derived here rather than imported, so the test fails
    if ``compute_extraction`` ever quietly changes what it counts.
    """
    from sea_of_colours.game.session import RED_QUALITY_MULTIPLIER

    value = cells = 0
    for entry in sess.ledger.entries.values():
        if int(entry["tile_at_generation"]) != int(Tile.RED):
            continue
        p = int(entry["purity_at_generation"])
        if p <= 0:
            continue  # RED terrain at purity 0 is not a prize
        tier = GameSession._tier_for_purity(p)
        value += int(round(p * RED_QUALITY_MULTIPLIER[tier]))
        cells += 1
    return value, cells


def test_extraction_denominator_is_genesis_not_remaining() -> None:
    """The map's RED value is fixed at generation.

    Harvesting turns RED into synthetic GREEN, so a denominator read off
    the *live* grid would shrink as the season went on and the
    percentage could never reach 100. Pin it to the ledger.
    """
    sess = GameSession.new(30, 20, seed=99, players=["p1", "p2"], season_day_cap=3)
    expected_value, expected_cells = _genesis_red(sess)

    before = soc_engine.compute_extraction(sess, [1, 2, 3])
    assert before["map_red_value"] == expected_value
    assert before["map_red_cells"] == expected_cells
    assert before["pct_harvested"] == 0.0
    assert before["unmined_value"] == expected_value

    # Burn a RED cell off the board the way the engine does.
    red_xy = next(
        (x, y)
        for y in range(sess.height)
        for x in range(sess.width)
        if sess.grid[y][x].tile == Tile.RED
    )
    from sea_of_colours.generator import Cell

    sess.grid[red_xy[1]][red_xy[0]] = Cell(Tile.GREEN, 255)

    after = soc_engine.compute_extraction(sess, [1, 2, 3])
    assert after["map_red_value"] == expected_value, (
        "denominator must not move when the live grid loses a RED cell"
    )


def test_extraction_splits_harvested_from_banked() -> None:
    """Harvested is what left the map; shipped is what scored.

    The gap is red that died with a harvester or is still in a vault,
    and the waffle renders the two differently, so they must not be
    collapsed into one number.
    """
    sess = GameSession.new(30, 20, seed=99, players=["p1", "p2"], season_day_cap=3)
    map_value, _ = _genesis_red(sess)

    # p1 lifts a pure-255 (765) on night 1 and a vein-100 (100) on night
    # 2, but only ever ships the pure one.
    sess.harvest_log["p1"] = [
        {"tile_at_harvest": int(Tile.RED), "purity_at_harvest": 255,
         "harvested_on_planning_day": 1},
        {"tile_at_harvest": int(Tile.RED), "purity_at_harvest": 100,
         "harvested_on_planning_day": 2},
        # Non-RED harvests must not touch these numbers at all.
        {"tile_at_harvest": int(Tile.GREEN), "purity_at_harvest": 255,
         "harvested_on_planning_day": 2},
        {"tile_at_harvest": int(Tile.BLUE), "purity_at_harvest": 200,
         "harvested_on_planning_day": 2},
    ]
    sess.shipped_squares["p1"] = [{
        "tile_at_harvest": int(Tile.RED), "purity_at_harvest": 255,
        "effective_purity": 255, "score_tier": "pure", "shipped_day": 2,
    }]

    x = soc_engine.compute_extraction(sess, [1, 2, 3])
    p1 = x["by_seat"]["p1"]

    assert p1["harvested_value"] == 765 + 100
    assert p1["shipped_value"] == 765
    assert p1["unbanked_value"] == 100
    assert p1["cells"] == 2, "green + blue harvests are not RED cells"

    # Cumulative, and banked on the night it was lifted.
    assert x["harvested_by_day"]["p1"] == [765, 865, 865]
    assert x["harvested_by_day"]["p2"] == [0, 0, 0]

    # Percentages are of the map's genesis value, tier-weighted.
    assert x["pct_harvested"] == round(100.0 * 865 / map_value, 1)
    assert x["pct_shipped"] == round(100.0 * 765 / map_value, 1)
    assert x["harvested_value"] == 865 and x["shipped_value"] == 765


def test_extraction_value_and_cell_shares_differ() -> None:
    """A tier-weighted share is not a share of the ground.

    Seats mine the good squares, so value% runs well ahead of cell% —
    the waffle legend has to say "value", and this pins that the two
    numbers really are computed differently.
    """
    sess = GameSession.new(40, 28, seed=1695867309, players=["p1", "p2"],
                           season_day_cap=3)
    # One pure cell is a large slice of value and a single cell.
    sess.harvest_log["p1"] = [
        {"tile_at_harvest": int(Tile.RED), "purity_at_harvest": 255,
         "harvested_on_planning_day": 1},
    ]
    x = soc_engine.compute_extraction(sess, [1, 2, 3])
    assert x["cells_mined"] == 1
    assert x["pct_harvested"] > x["pct_cells"]


def test_summary_and_replay_carry_seed_and_extraction() -> None:
    sess = GameSession.new(30, 20, seed=4242, players=["p1", "p2"],
                           season_day_cap=2)
    store = InMemorySocStore()
    _persist(store, sess)

    summary = soc_engine.get_endgame_summary(store, sess.session_id)
    assert summary["seed"] == 4242
    assert summary["extraction"]["map_red_value"] > 0

    replay = soc_engine.get_replay(store, sess.session_id)
    assert replay["seed"] == 4242
    assert (
        replay["extraction"]["map_red_value"]
        == summary["extraction"]["map_red_value"]
    ), "replay header and report card must quote the same denominator"


# ── HTTP route ───────────────────────────────────────────────────────
@pytest.fixture()
def client():
    soc_backend.reset_for_tests()
    from server.app import app
    return TestClient(app)


def test_summary_route_returns_shape(client) -> None:
    sid = client.post(
        "/api/game/new", params={"seed": 21, "width": 12, "height": 8},
    ).json()["session_id"]
    body = client.get(f"/api/game/{sid}/summary").json()
    assert "players" in body and "red_by_day" in body
    assert "manifest" in body and "days" in body
    for p in body["players"]:
        assert "rank" in p and "score" in p and "name" in p


def test_summary_route_404_for_unknown(client) -> None:
    assert client.get("/api/game/nope/summary").status_code == 404
