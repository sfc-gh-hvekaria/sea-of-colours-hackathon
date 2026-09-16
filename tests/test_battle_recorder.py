"""The bake format, and the promises the review room relies on.

The room is a static page that cannot report its own breakage — if a
bake changes shape it renders an empty board and says nothing. So the
contract is pinned here instead: what a turn carries, how the pooling
works, and that the file it writes is something a browser can load off
disk without a server.
"""

from __future__ import annotations

import json
import re

import pytest

from sea_of_colours.evals.battles import boards, ladder, recorder, runner
from sea_of_colours.evals.battles.recorder import (
    Recorder, diff_orders, extract_card, read_bakes, snapshot_grid,
)
from sea_of_colours.snowpark import engine as soc_engine


@pytest.fixture(scope="module")
def bake_dir(tmp_path_factory):
    """One recorded mini-suite, reused by everything that only reads it."""
    root = tmp_path_factory.mktemp("battles")
    rec = Recorder(agent="red_harvest", root=root)
    runner.run_suite(
        [boards.get("early_solo_seam"), boards.get("two_pures_poker")],
        [ladder.get_rung("quiet")],
        [ladder.get_loadout("empty")],
        agent="red_harvest", runs=2, recorder=rec,
    )
    rec.finish()
    return root


def _bake(root):
    path = next((root / "data").glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


# ── what a bake contains ──────────────────────────────────────────


def test_every_played_turn_is_recorded(bake_dir):
    blob = _bake(bake_dir)
    # two boards x one rung x one loadout x two runs
    assert len(blob["turns"]) == 4
    assert blob["agent"] == "red_harvest"


def test_a_turn_carries_everything_the_room_draws(bake_dir):
    turn = _bake(bake_dir)["turns"][0]
    for key in ("board", "grid", "vision", "entities", "redsigns",
                "moves", "checks", "card", "result", "rung", "loadout"):
        assert key in turn, f"the room reads {key!r} and it is not in the bake"
    assert turn["result"]["seat"], "no seat — the room cannot colour the fleets"


def test_the_checks_come_through_with_their_reasons(bake_dir):
    turn = _bake(bake_dir)["turns"][0]
    assert turn["checks"], "a turn with no checks is unreviewable"
    for check in turn["checks"]:
        assert set(check) >= {"name", "passed", "detail"}


def test_the_fog_the_seat_planned_under_is_kept(bake_dir):
    """Ground truth alone cannot distinguish a misread from a blind guess."""
    turn = _bake(bake_dir)["turns"][0]
    assert turn["vision"], "no visibility captured"
    assert all(len(c) == 2 for c in turn["vision"])


# ── the shape the room decodes ────────────────────────────────────


def test_terrain_is_sparse_and_matches_the_session():
    from sea_of_colours.evals.battles import stage

    battle = stage.stage(
        boards.get("two_pures_poker"), ladder.get_rung("quiet"),
        ladder.get_loadout("empty"), seed=99,
    )
    sess = soc_engine._hydrate_session(battle.store, battle.session_id)
    grid = snapshot_grid(sess)

    assert (grid["width"], grid["height"]) == (sess.width, sess.height)
    assert len(grid["cells"]) < grid["width"] * grid["height"], "not sparse"

    live = {
        (x, y): sess.grid[y][x]
        for y in range(sess.height) for x in range(sess.width)
        if sess.grid[y][x].tile.name != "EMPTY"
    }
    assert len(grid["cells"]) == len(live)
    for x, y, code, purity in grid["cells"]:
        cell = live[(x, y)]
        assert cell.tile.name.startswith(code), f"({x},{y}) decoded wrong"
        assert cell.purity == purity


def test_board_prose_is_stored_once_not_per_turn(bake_dir):
    """Four turns over two boards must not carry four copies of the prose."""
    blob = _bake(bake_dir)
    assert set(blob["boards"]) == {"early_solo_seam", "two_pures_poker"}
    for turn in blob["turns"]:
        assert turn["board"] in blob["boards"]
        assert turn["grid"] in blob["grids"]


def test_identical_terrain_is_stored_once(tmp_path):
    """The saving that matters: one board across the whole ladder.

    Rungs change who is standing on the board, not the ground, so a
    board played at five rungs is one grid. Left inline, a full suite is
    ~500 copies of the terrain and the room takes seconds to open.
    """
    rec = Recorder(agent="red_harvest", root=tmp_path)
    runner.run_suite(
        [boards.get("early_solo_seam")],
        list(ladder.up_to("armed")),
        [ladder.get_loadout("empty")],
        agent="red_harvest", runs=1, recorder=rec,
    )
    rec.finish()
    blob = _bake(tmp_path)
    assert len(blob["turns"]) == 3
    assert len(blob["grids"]) == 1, "the same ground was stored three times"


# ── the browser has to be able to load it ─────────────────────────


def test_the_bake_loads_as_a_script_not_a_fetch(bake_dir):
    """A page opened over file:// cannot fetch a sibling. It can run one."""
    path = next((bake_dir / "data").glob("*.js"))
    text = path.read_text(encoding="utf-8")
    assert text.startswith("window.SOC_BATTLE_BAKES")
    body = text[text.index("] = ") + 4:].rstrip().rstrip(";")
    assert json.loads(body)["agent"] == "red_harvest"


def test_the_index_lists_the_bake(bake_dir):
    text = (bake_dir / "index.js").read_text(encoding="utf-8")
    assert text.startswith("window.SOC_BATTLE_INDEX")
    listed = json.loads(text[text.index("=") + 1:].rstrip().rstrip(";"))
    assert len(listed) == 1
    assert listed[0]["turns"] == 4
    assert listed[0]["boards"] == ["early_solo_seam", "two_pures_poker"]


def test_the_room_lands_as_index_html(bake_dir):
    """Named so one directory works by double-click and under /battles/."""
    room = bake_dir / "index.html"
    assert room.is_file()
    text = room.read_text(encoding="utf-8")
    assert "SOC_BATTLE_INDEX" in text
    assert 'src="index.js"' in text

    # Comments stripped first — the file warns against fetch() in prose,
    # and that warning must not be what satisfies the check.
    code = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert "fetch(" not in code, "a fetch will fail over file://"


def test_a_second_bake_joins_the_index_without_evicting_the_first(bake_dir):
    rec = Recorder(agent="other_agent", root=bake_dir, bake_id="second")
    rec.finish()
    listed = read_bakes(bake_dir)
    assert {b["agent"] for b in listed} == {"red_harvest", "other_agent"}


def test_deleting_a_bake_removes_it_from_the_index(tmp_path):
    Recorder(agent="a", root=tmp_path, bake_id="one").finish()
    Recorder(agent="b", root=tmp_path, bake_id="two").finish()
    (tmp_path / "data" / "one.json").unlink()
    recorder.write_index(tmp_path)
    assert [b["id"] for b in read_bakes(tmp_path)] == ["two"]


# ── the card ──────────────────────────────────────────────────────


def test_a_heuristic_has_no_card_and_says_so(bake_dir):
    """Not an error: a heuristic has no prompt and nothing to reason with."""
    assert all(t["card"] == {} for t in _bake(bake_dir)["turns"])


def test_the_card_is_lifted_out_of_the_envelope():
    env = {"extras": {
        "thinker_prompt": "you are a house",
        "thinker_reasoning": "the pure is worth the risk",
        "option_menu_block": "A) drop on pure",
        "selected_option_ids": ["A"],
        "final_moves": [{"a": "drop"}],
    }}
    card = extract_card(env)
    assert card["prompt"] == "you are a house"
    assert card["reasoning"] == "the pure is worth the risk"
    assert card["options_chosen"] == ["A"]


def test_a_renamed_prompt_field_still_finds_the_prompt():
    """Forks rename things; a missing panel should be missing, not wrong."""
    assert extract_card({"extras": {"plan_prompt": "x"}})["prompt"] == "x"
    assert extract_card({"extras": {}}) == {}
    assert extract_card(None) == {}


def test_the_compiler_rewriting_an_order_is_visible():
    proposed = [{"a": "drop", "at": [5, 5]}, {"a": "step", "to": [5, 6]}]
    played = [{"a": "drop", "at": [5, 5]}]
    diff = diff_orders(proposed, played)
    assert [d["kind"] for d in diff] == ["dropped"]
    assert diff[0]["index"] == 1


def test_orders_that_survived_intact_produce_no_diff():
    same = [{"a": "drop", "at": [1, 1]}]
    assert diff_orders(same, list(same)) == []


# ── failure modes ─────────────────────────────────────────────────


def test_a_turn_that_crashed_is_still_recorded(tmp_path, monkeypatch):
    """The turn that blew up is the one you most want to open."""
    monkeypatch.setattr(
        runner, "_dispatch",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model exploded")),
    )
    rec = Recorder(agent="broken", root=tmp_path)
    runner.run_battle(
        boards.get("early_solo_seam"), ladder.get_rung("quiet"),
        ladder.get_loadout("empty"), agent="red_harvest", recorder=rec,
    )
    rec.finish()
    turns = _bake(tmp_path)["turns"]
    assert len(turns) == 1
    assert "model exploded" in turns[0]["result"]["error"]
    assert turns[0]["grid"] in _bake(tmp_path)["grids"], "board still shown"


def test_recording_is_off_unless_asked(tmp_path):
    """A plain suite run must not write anything anywhere."""
    runner.run_battle(
        boards.get("early_solo_seam"), ladder.get_rung("quiet"),
        ladder.get_loadout("empty"), agent="red_harvest",
    )
    assert not list(tmp_path.iterdir())
