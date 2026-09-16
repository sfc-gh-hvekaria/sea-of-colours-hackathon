"""Wire-contract tests for the ``world.grid[y][x]`` agent view shape.

The Phase 1 orchestrator-config A/B introduces a second world-view
shape (``grid``) alongside the existing flat-list shape. This file
locks the grid contract so refactors to
:mod:`sea_of_colours.snowpark.view` can't silently break the
agent-spec instructions on the deployed :data:`SOC_RED_REAPER_GRID`.

What we lock:

* Mode switch — ``SOC_AGENT_WORLD_VIEW=grid`` swaps the ``world``
  block from ``{live, echo, fog_count}`` to ``{grid, fog_count}``.
* Grid dimensions match ``width`` × ``height`` rows / cols.
* Fog cells are ``None`` (NOT empty dicts, NOT ``"fog"`` strings).
* Live cells carry tile-specific decorations:
  - RED carries ``purity`` and ``value``.
  - Synthetic GREEN carries ``synthetic: True``.
  - Entity occupants carry ``entity = {kind, owner, ...}``.
* Echo cells carry ``echo: True``.
* Live wins over echo when both exist for a cell.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SOC_BACKEND", "memory")

from sea_of_colours.evals.builder import WorldBuilder  # noqa: E402
from sea_of_colours.snowpark import engine as soc_engine  # noqa: E402
from sea_of_colours.snowpark.view import (  # noqa: E402
    WORLD_VIEW_GRID,
    WORLD_VIEW_LIST,
    build_agent_view,
)


@pytest.fixture
def grid_mode_env(monkeypatch):
    """Pin SOC_AGENT_WORLD_VIEW='grid' for the duration of a test."""
    monkeypatch.setenv("SOC_AGENT_WORLD_VIEW", WORLD_VIEW_GRID)


@pytest.fixture
def list_mode_env(monkeypatch):
    """Explicitly clear the env var so default 'list' mode applies."""
    monkeypatch.delenv("SOC_AGENT_WORLD_VIEW", raising=False)


def _hydrate(builder: WorldBuilder):
    store, sid = builder.build()
    return soc_engine._hydrate_session(store, sid), sid


def _agent_view(builder: WorldBuilder):
    sess, _ = _hydrate(builder)
    return build_agent_view(sess, "p1")


def test_list_mode_emits_live_echo_arrays(list_mode_env):
    """Default mode still emits the flat ``live[]`` / ``echo[]`` arrays.

    Regression sentinel: refactors to view.py mustn't accidentally
    swap the default shape — the deployed ``SOC_RED_REAPER_LIST``
    expects this layout.
    """
    view = _agent_view(
        WorldBuilder(seed=42, day=4, season_day_cap=7)
        .place_harvester("harvester_p1", state="orbit")
        .reveal_red([(10, 10, 200)])
        .grant_live_vision([(10, 10)])
    )
    world = view.get("world") or {}
    assert "live" in world, "list mode must keep world.live"
    assert "echo" in world, "list mode must keep world.echo"
    assert "grid" not in world, "list mode must NOT emit world.grid"


def test_grid_mode_emits_grid_only(grid_mode_env):
    """Grid mode swaps ``live[]`` / ``echo[]`` for ``grid[y][x]``."""
    view = _agent_view(
        WorldBuilder(seed=42, day=4, season_day_cap=7)
        .place_harvester("harvester_p1", state="orbit")
        .reveal_red([(10, 10, 200)])
        .grant_live_vision([(10, 10)])
    )
    world = view.get("world") or {}
    assert "grid" in world, "grid mode must emit world.grid"
    assert "live" not in world, "grid mode must drop world.live"
    assert "echo" not in world, "grid mode must drop world.echo"
    assert "fog_count" in world, "fog_count must survive the mode switch"


def test_grid_dimensions_match_width_height(grid_mode_env):
    """``grid`` is a ``height``-row × ``width``-col nested array.

    Indexing as ``grid[y][x]`` is the documented contract — break
    this and the agent's "north = grid[y-1][x]" reasoning becomes
    nonsense.
    """
    view = _agent_view(
        WorldBuilder(seed=42, day=4, season_day_cap=7)
        .place_harvester("harvester_p1", state="orbit")
    )
    world = view["world"]
    grid = world["grid"]
    assert len(grid) == world["height"], (
        f"grid has {len(grid)} rows, world.height={world['height']}"
    )
    for row_idx, row in enumerate(grid):
        assert len(row) == world["width"], (
            f"row {row_idx} has {len(row)} cols, "
            f"world.width={world['width']}"
        )


def test_grid_fog_cells_are_none(grid_mode_env):
    """Unobserved cells must be the JSON literal ``None`` (= ``null``).

    The deployed agent's "READING THE WORLD" section says ``null`` =
    fog. Empty dicts / "fog" strings here would let the agent
    confuse "no parcel" with "never seen".
    """
    view = _agent_view(
        WorldBuilder(seed=42, day=4, season_day_cap=7)
        .place_harvester("harvester_p1", state="orbit")
        .clear_visibility("p1")
    )
    grid = view["world"]["grid"]
    # Far corner — definitely unobserved.
    assert grid[0][0] is None, "fog cells must serialise as None"
    # And the fog_count should be > 0 (whole board is fog).
    assert view["world"]["fog_count"] > 0


def test_grid_red_cell_carries_value_and_purity(grid_mode_env):
    """RED cells must surface ``purity`` and ``value`` for sort decisions."""
    view = _agent_view(
        WorldBuilder(seed=42, day=4, season_day_cap=7)
        .place_harvester("harvester_p1", state="orbit")
        .reveal_red([(15, 10, 220)])
        .grant_live_vision([(15, 10)])
    )
    grid = view["world"]["grid"]
    cell = grid[10][15]
    assert cell is not None, "live RED cell must not be null"
    assert cell["tile"] == "RED"
    assert cell["purity"] == 220
    assert cell["value"] == 220
    assert "echo" not in cell, "live cell must NOT carry echo:true"


def test_grid_synthetic_green_is_flagged(grid_mode_env):
    """Synthetic GREEN must carry ``synthetic: True`` for routing."""
    view = _agent_view(
        WorldBuilder(seed=42, day=4, season_day_cap=7)
        .place_harvester("harvester_p1", state="orbit")
        .reveal_green_synthetic([(12, 10)])
        .grant_live_vision([(12, 10)])
    )
    grid = view["world"]["grid"]
    cell = grid[10][12]
    assert cell is not None
    assert cell["tile"] == "GREEN"
    assert cell.get("synthetic") is True, (
        "synthetic-green cells must flag synthetic=True so the agent "
        "routes around them instead of banking a zero-value parcel"
    )


def test_grid_echo_cells_carry_echo_flag(grid_mode_env):
    """Echo-only cells must carry ``echo: True`` so stale data is obvious."""
    view = _agent_view(
        WorldBuilder(seed=42, day=4, season_day_cap=7)
        .place_harvester("harvester_p1", state="orbit")
        .reveal_red([(20, 14, 180)])
        # Reveal via stale memory (echo) — not live LOS.
        .reveal_to_player([(20, 14)], player="p1", stale=True)
    )
    grid = view["world"]["grid"]
    cell = grid[14][20]
    assert cell is not None
    assert cell.get("echo") is True, "echo rows must flag echo=True"


def test_grid_live_wins_over_echo_for_same_cell(grid_mode_env):
    """If a cell is BOTH in live LOS and echo memory, live data wins."""
    view = _agent_view(
        WorldBuilder(seed=42, day=4, season_day_cap=7)
        .place_harvester("harvester_p1", state="orbit")
        .reveal_red([(15, 10, 220)])
        # Same cell ALSO has stale echo memory; live should override.
        .reveal_to_player([(15, 10)], player="p1", stale=True)
        .grant_live_vision([(15, 10)])
    )
    cell = view["world"]["grid"][10][15]
    assert cell is not None
    assert cell.get("echo") is not True, (
        "cells in current LOS must not be tagged as echo"
    )
    assert cell["tile"] == "RED"
    assert cell["purity"] == 220


def test_grid_entity_occupant_surfaced(grid_mode_env):
    """Cells with an entity (harvester / probe) must surface owner+kind."""
    view = _agent_view(
        WorldBuilder(seed=42, day=4, season_day_cap=7)
        # Surface harvester at (10, 10) — visible to itself, so the
        # cell appears in the player's live view with an entity block.
        .place_harvester(
            "harvester_p1", at=(10, 10), state="surface", cargo=0,
        )
    )
    cell = view["world"]["grid"][10][10]
    assert cell is not None, "surface harvester cell must be live"
    ent = cell.get("entity")
    assert ent is not None, "entity block must be present"
    assert ent.get("kind") == "harvester"
    assert ent.get("owner") == "p1"


def test_constants_exposed():
    """Public constants must stay importable for downstream callers.

    The eval runner / tests depend on these names — pin them so a
    rename in view.py is caught here instead of at deploy time.
    """
    from sea_of_colours.snowpark.view import WORLD_VIEW_LIST as L
    from sea_of_colours.snowpark.view import WORLD_VIEW_GRID as G

    assert L == "list"
    assert G == "grid"
