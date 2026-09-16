"""Integration tests for v0.9.18 player profiles API (§ BACKEND VALIDATION)."""

from __future__ import annotations

import pytest

from sea_of_colours.snowpark.engine import init_session, get_view
from sea_of_colours.snowpark.store import InMemorySocStore


def test_api_new_game_accepts_custom_profiles():
    """POST /api/game/new can accept player_profiles and they roundtrip correctly."""
    store = InMemorySocStore()
    
    profiles = {
        "p1": {"display_name": "Alice", "tag": "ALI", "color": "#FF8A1E"},
        "p2": {"display_name": "Bob", "tag": "BOB", "color": "#B6FF3A"},
    }
    
    result = init_session(
        store,
        seed=42,
        width=20,
        height=14,
        players=["p1", "p2"],
        player_profiles=profiles,
    )
    
    assert "session_id" in result
    session_id = result["session_id"]
    
    # Retrieve via get_view and verify profiles are exposed
    view = get_view(store, session_id, "p1")
    
    assert view["player_profiles"]["p1"]["display_name"] == "Alice"
    assert view["player_profiles"]["p1"]["tag"] == "ALI"
    assert view["player_profiles"]["p1"]["color"] == "#FF8A1E"
    
    assert view["player_profiles"]["p2"]["display_name"] == "Bob"
    assert view["player_profiles"]["p2"]["tag"] == "BOB"
    assert view["player_profiles"]["p2"]["color"] == "#B6FF3A"


def test_api_bot_profiles_auto_generated():
    """Bot seats get Latin names + strategy tags automatically."""
    store = InMemorySocStore()
    
    result = init_session(
        store,
        seed=99,
        width=20,
        height=14,
        players=["p1", "p2"],
        agents={"p1": "human", "p2": "red_harvest"},
    )
    
    session_id = result["session_id"]
    view = get_view(store, session_id, "p1")
    
    # p1 human should have color label default
    assert view["player_profiles"]["p1"]["display_name"] == "WHITE"
    assert view["player_profiles"]["p1"]["tag"] == "WHI"
    
    # p2 bot should have a Latin name and a NAME-derived 3-letter tag
    p2_name = view["player_profiles"]["p2"]["display_name"]
    assert len(p2_name.split()) == 2  # "Colour Animal" format
    assert p2_name != "YELLOW"  # Not the default label
    
    # v0.9.18 — the bot tag is derived from its display NAME (so two
    # RED_HARVEST bots get distinct tags), not the strategy slug.
    from sea_of_colours.game.player_names import generate_player_tag
    p2_tag = view["player_profiles"]["p2"]["tag"]
    assert p2_tag == generate_player_tag(p2_name)
    assert len(p2_tag) == 3 and p2_tag.isupper() and p2_tag.isalpha()


def test_api_color_validation_fallback():
    """Invalid colors fallback to seat defaults."""
    store = InMemorySocStore()
    
    profiles = {
        "p1": {"display_name": "Test", "tag": "TST", "color": "#INVALID"},
    }
    
    result = init_session(
        store,
        seed=123,
        width=20,
        height=14,
        players=["p1"],
        player_profiles=profiles,
    )
    
    session_id = result["session_id"]
    view = get_view(store, session_id, "p1")
    
    # Should fallback to p1 default color
    assert view["player_profiles"]["p1"]["color"] == "#FFFFFF"


def test_api_palette_endpoint_structure():
    """The palette data structure matches expected shape."""
    from sea_of_colours.game.session import SEAT_COLOR_PALETTE, SEAT_DEFAULT_COLORS
    
    # Simulate what the API endpoint returns
    palette_data = {
        "palette": [
            {"hex": hex_color, "rgb": list(rgb)}
            for hex_color, rgb in SEAT_COLOR_PALETTE.items()
        ],
        "defaults": dict(SEAT_DEFAULT_COLORS),
    }
    
    assert len(palette_data["palette"]) == 9
    assert all("hex" in item and "rgb" in item for item in palette_data["palette"])
    assert palette_data["defaults"]["p1"] == "#FFFFFF"
    assert palette_data["defaults"]["p2"] == "#FCF871"
