"""v0.9.6 — visibility-mode contract tests.

Two modes:

* ``hidden`` (default, legacy behaviour) — every seat sees only what
  its own units / probes have observed. Fog of war is real.
* ``open`` — every seat sees the entire board, regardless of where
  its units are. Used for the OBS / training / replay-style views.

These tests pin both modes through the engine's view builder.
"""

from __future__ import annotations

from sea_of_colours.snowpark import engine as soc_engine
from sea_of_colours.snowpark.store import InMemorySocStore
from sea_of_colours.snowpark.view import build_agent_view
from sea_of_colours.snowpark.engine import _hydrate_session


def _spawn(visibility: str) -> tuple[InMemorySocStore, str]:
    store = InMemorySocStore()
    res = soc_engine.init_session(
        store, seed=314, width=30, height=20,
        players=("p1", "p2"),
        visibility_mode=visibility,
    )
    return store, res["session_id"]


def test_hidden_visibility_yields_partial_world() -> None:
    """Default hidden mode — the world payload only contains cells
    the seat can see (probes + harvester vision + echoes)."""
    store, sid = _spawn("hidden")
    sess = _hydrate_session(store, sid)
    view = build_agent_view(sess, "p1")
    world = view.get("world") or {}
    fog_count = int(world.get("fog_count") or 0)
    # At session start no probes have been deployed, so the vast
    # majority of cells should still be fogged. Allowing some
    # slack for vision around the harvester staging area.
    total_cells = sess.width * sess.height
    assert fog_count >= total_cells * 0.6, (
        f"hidden mode should leave most cells in fog: {fog_count}/{total_cells}"
    )


def test_open_visibility_reveals_entire_board_to_spectator() -> None:
    """Open mode — the spectator (``/view`` ``cells``) all-reveal.

    v0.9.6 — open mode is a PRESENTATION concern: the OBS / replay
    watcher sees every tile, but the per-seat AGENT view stays
    fog-of-war (otherwise bot planners would think their fleet can
    drop anywhere, hitting the engine's seat-private drop guard).
    So the spectator's ``cells`` payload (built from
    :meth:`GameSession.player_dense_view`) must contain zero fog
    cells, while ``world.fog_count`` from the agent payload still
    reflects the seat's private fog.
    """
    store, sid = _spawn("open")
    spec_view = soc_engine.get_view(store, sid, "p1")
    spec_cells = spec_view.get("cells") or []
    fog_spec = sum(1 for c in spec_cells if c.get("kind") == "fog")
    assert fog_spec == 0, (
        f"open mode spectator must reveal every cell, got {fog_spec} fog"
    )
    # The AGENT-view side stays seat-private so bots plan correctly.
    sess = _hydrate_session(store, sid)
    agent_world = (build_agent_view(sess, "p1").get("world") or {})
    total_cells = sess.width * sess.height
    assert int(agent_world.get("fog_count") or 0) >= total_cells * 0.6, (
        "open mode must NOT bleed all-reveal into the agent view; "
        "the bot's world must stay seat-private so the engine's "
        "drop validator and the agent's _is_valid_drop agree."
    )


def test_view_response_surfaces_visibility_mode() -> None:
    """The /view response includes ``visibility_mode`` so the UI can
    branch on it (e.g. to gate the OBS tab)."""
    store, sid = _spawn("open")
    view = soc_engine.get_view(store, sid, "p1")
    assert view["visibility_mode"] == "open"


def test_legacy_session_defaults_to_hidden() -> None:
    """A session spawned without ``visibility_mode`` keeps the legacy
    fog-of-war default — no behavioural change for existing callers."""
    store = InMemorySocStore()
    res = soc_engine.init_session(store, seed=1, width=24, height=16)
    sid = res["session_id"]
    view = soc_engine.get_view(store, sid, "p1")
    assert view["visibility_mode"] == "hidden"
