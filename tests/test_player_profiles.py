"""Tests for v0.9.18 player identity profiles (names, tags, colors)."""

from __future__ import annotations

import pytest

from sea_of_colours.game.session import (
    GameSession,
    SEAT_COLOR_PALETTE,
    SEAT_DEFAULT_COLORS,
)
from sea_of_colours.game.player_names import generate_player_tag


def test_player_profiles_default_for_human_seats():
    """Human seats get auto-generated profiles with seat color labels."""
    sess = GameSession.new(20, 14, seed=42, players=["p1", "p2"])
    
    p1_profile = sess.player_profiles.get("p1", {})
    assert p1_profile["display_name"] == "WHITE"
    assert p1_profile["tag"] == "WHI"
    assert p1_profile["color"] == "#FFFFFF"
    
    p2_profile = sess.player_profiles.get("p2", {})
    assert p2_profile["display_name"] == "YELLOW"
    assert p2_profile["tag"] == "YEL"
    assert p2_profile["color"] == "#FCF871"


def test_player_profiles_bot_auto_population():
    """Bot seats get Latin names + auto-generated tags."""
    sess = GameSession.new(
        20, 14, seed=99,
        players=["p1", "p2"],
        agents={"p1": "human", "p2": "red_harvest"}
    )
    
    # p1 human
    p1_profile = sess.player_profiles["p1"]
    assert p1_profile["display_name"] == "WHITE"
    assert p1_profile["tag"] == "WHI"
    
    # p2 bot gets Latin name from player_names
    p2_profile = sess.player_profiles["p2"]
    assert p2_profile["display_name"] == sess.player_names["p2"]  # e.g. "Aureus Vulpes"
    assert len(p2_profile["tag"]) == 3
    assert p2_profile["tag"].isupper()
    assert p2_profile["tag"].isalpha()


def test_player_profiles_custom_via_new():
    """Custom profiles can be passed to GameSession.new()."""
    profiles = {
        "p1": {"display_name": "Alice", "tag": "ALI", "color": "#FF8A1E"},
        "p2": {"display_name": "Bob", "tag": "BOB", "color": "#B6FF3A"},
    }
    sess = GameSession.new(
        20, 14, seed=123,
        players=["p1", "p2"],
        player_profiles=profiles,
    )
    
    assert sess.player_profiles["p1"]["display_name"] == "Alice"
    assert sess.player_profiles["p1"]["tag"] == "ALI"
    assert sess.player_profiles["p1"]["color"] == "#FF8A1E"
    
    assert sess.player_profiles["p2"]["display_name"] == "Bob"
    assert sess.player_profiles["p2"]["tag"] == "BOB"
    assert sess.player_profiles["p2"]["color"] == "#B6FF3A"


def test_player_profiles_color_validation():
    """Non-palette colors fallback to seat defaults."""
    profiles = {
        "p1": {"display_name": "Test", "tag": "TST", "color": "#BADCOL"},  # invalid
    }
    sess = GameSession.new(
        20, 14, seed=456,
        players=["p1"],
        player_profiles=profiles,
    )
    
    # Should fallback to p1 default color
    assert sess.player_profiles["p1"]["color"] == SEAT_DEFAULT_COLORS["p1"]


def test_player_profiles_tag_clamped_to_3_chars():
    """Tags are auto-clamped to 3 uppercase chars."""
    profiles = {
        "p1": {"display_name": "Verylongname", "tag": "toolong", "color": "#FFFFFF"},
    }
    sess = GameSession.new(
        20, 14, seed=789,
        players=["p1"],
        player_profiles=profiles,
    )
    
    assert sess.player_profiles["p1"]["tag"] == "TOO"
    assert len(sess.player_profiles["p1"]["tag"]) == 3


def test_player_profiles_serialization_roundtrip():
    """Profiles survive to_dict / from_dict."""
    sess = GameSession.new(
        20, 14, seed=111,
        players=["p1", "p2"],
        player_profiles={
            "p1": {"display_name": "Charlie", "tag": "CHA", "color": "#E45EF0"},
            "p2": {"display_name": "Dana", "tag": "DAN", "color": "#82F4FB"},
        },
    )
    
    blob = sess.to_dict()
    restored = GameSession.from_dict(blob)
    
    assert restored.player_profiles["p1"]["display_name"] == "Charlie"
    assert restored.player_profiles["p1"]["tag"] == "CHA"
    assert restored.player_profiles["p1"]["color"] == "#E45EF0"
    
    assert restored.player_profiles["p2"]["display_name"] == "Dana"
    assert restored.player_profiles["p2"]["tag"] == "DAN"
    assert restored.player_profiles["p2"]["color"] == "#82F4FB"


def test_seat_color_method_returns_palette_rgb():
    """sess.seat_color() returns RGB tuple from palette."""
    sess = GameSession.new(
        20, 14, seed=222,
        players=["p1"],
        player_profiles={
            "p1": {"display_name": "Test", "tag": "TST", "color": "#FF8A1E"},
        },
    )
    
    rgb = sess.seat_color("p1")
    assert rgb == SEAT_COLOR_PALETTE["#FF8A1E"]
    assert rgb == (255, 138, 30)


def test_generate_player_tag_from_strategy():
    """generate_player_tag() extracts 3-letter tags from bot strategy names."""
    assert generate_player_tag("red_harvest") == "RHA"  # first letter + first 2 of second word
    assert generate_player_tag("SOC_RED_REAPER_GRID_FAST") == "RRG"  # strips SOC_, takes initials
    assert generate_player_tag("Aureus Vulpes") == "AVU"  # first letter + first 2 of second word
    
    # Fallback for short names
    tag = generate_player_tag("AB")
    assert len(tag) == 3
    assert tag.isupper()


def test_palette_contains_9_distinct_colors():
    """SEAT_COLOR_PALETTE has 9 unique, high-visibility colors."""
    assert len(SEAT_COLOR_PALETTE) == 9
    
    # All hexes are uppercase 6-char
    for hex_color in SEAT_COLOR_PALETTE.keys():
        assert hex_color.startswith("#")
        assert len(hex_color) == 7
        assert hex_color == hex_color.upper()
    
    # All RGBs are tuples of 3 ints
    for rgb in SEAT_COLOR_PALETTE.values():
        assert isinstance(rgb, tuple)
        assert len(rgb) == 3
        assert all(isinstance(c, int) and 0 <= c <= 255 for c in rgb)


def test_seat_default_colors_match_palette():
    """SEAT_DEFAULT_COLORS keys are all valid palette entries."""
    for seat, hex_color in SEAT_DEFAULT_COLORS.items():
        assert hex_color in SEAT_COLOR_PALETTE, f"{seat} default {hex_color} not in palette"
