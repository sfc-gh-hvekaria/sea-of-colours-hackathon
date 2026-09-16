#!/usr/bin/env python3
"""Bake the data behind `manual/agent.html` — the agent & harness guide.

The guide is an autopsy of ONE real turn, and every number on the page has to
come off that turn rather than out of a writer's head. This script is what makes
that true: it reads the frozen snapshot and the card the harness printed for it,
and writes a single JSON the page loads.

Two sources, deliberately kept apart:

  * the SNAPSHOT (`SNAP_408ddd46_d4_p1`) — engine truth. The whole 40x28 grid
    including everything p1 cannot see. This is what lets the guide show "what
    is really there" next to "what the agent gets", which is the single most
    useful picture in the document.
  * the CARD (`reports/turn_suite/cards/post_phaseD/own_seam_d4.txt`) — the
    agent's side. The verbatim prompt, its reasoning, the plan it committed, the
    packager log and the 15 wire moves.

Run once; the output is committed so the page works offline with no Snowflake.

    SOC_BACKEND=snowflake python scripts/export_agent_guide_data.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SNAPSHOT = "SNAP_408ddd46_d4_p1"
CARD = ROOT / "reports/turn_suite/cards/post_phaseD/own_seam_d4.txt"
HARNESS_DIR = ROOT / "sea_of_colours/orchestrator_2/harnesses/tabula_v12"
# Emitted as JS, not JSON, on purpose: the manual is routinely opened straight
# off disk, and a file:// page is not allowed to fetch() a sibling file. A
# plain global assignment loads by <script src> under any origin.
OUT = ROOT / "manual/agent-data.js"

# Engine tile encoding, mirrored by manual.js:11. The snapshot grid stores each
# cell as [tile_code, purity], which is already the shape renderBoard wants.
TILE_NAMES = {0: "EMPTY", 1: "GREEN", 2: "RED", 3: "BLUE"}


# ── the snapshot: engine truth ────────────────────────────────────────

def load_board() -> Dict[str, Any]:
    from sea_of_colours.snowpark import backend, engine
    from sea_of_colours.game.session import GameSession
    import scripts.turn_suite as ts

    store = backend.get_store()
    state = ts._state_of(store, SNAPSHOT)
    session = GameSession.from_dict(state)
    view = engine.build_agent_view(session, "p1")

    grid = state["grid"]
    height = len(grid)
    width = len(grid[0])

    # Flattened row-major so the page can index it as cells[y * width + x] with
    # no reshaping, matching renderBoard's own ordering.
    truth = []
    for row in grid:
        for tile, purity in row:
            truth.append([int(tile), int(purity)])

    live = [
        {"x": c["x"], "y": c["y"], "tile": c.get("tile", "EMPTY"),
         "purity": int(c.get("purity") or 0)}
        for c in view.get("world", {}).get("live", [])
    ]
    echo = [
        {"x": c["x"], "y": c["y"], "tile": c.get("tile", "EMPTY"),
         "purity": int(c.get("purity") or 0),
         "last_seen_day": c.get("last_seen_day")}
        for c in view.get("world", {}).get("echo", [])
        if c.get("tile")
    ]

    # Probe lifetime is not on the entity; the seat's own view carries it.
    nights_left = {}
    for row in view.get("my_assets") or []:
        if row.get("kind") != "probe":
            continue
        at = row.get("at") or []
        if len(at) == 2 and at[0] is not None:
            nights_left[(int(at[0]), int(at[1]))] = row.get("nights_remaining")

    probes = []
    for ent in (state.get("entities") or {}).values():
        if ent.get("type") != "probe" or ent.get("x") is None:
            continue
        xy = (int(ent["x"]), int(ent["y"]))
        probes.append({
            "id": ent.get("id"), "owner": ent.get("owner"),
            "x": xy[0], "y": xy[1],
            "nights_remaining": nights_left.get(xy),
        })

    # Rival probes p1 only knows about through intel, not through sight.
    known_rival = []
    for row in (view.get("competitor_intel", {}) or {}).get("probes", []) or []:
        at = row.get("at") or [row.get("x"), row.get("y")]
        if at and at[0] is not None:
            known_rival.append({"x": int(at[0]), "y": int(at[1]),
                                "owner": row.get("owner")})

    signs = []
    for sign in state.get("redsign") or []:
        signs.append({
            "id": sign.get("id"),
            "discoverer": sign.get("discoverer"),
            "mine": sign.get("discoverer") == "p1",
            "day_found": sign.get("day"),
            "live": bool(sign.get("live")),
            # The broadcast centre is deliberately off the real cell — showing
            # the two together is the whole point of the smear section.
            "center": [round(float(v), 2) for v in (sign.get("center") or [])],
            "pure_cells": [[int(c[0]), int(c[1])]
                           for c in (sign.get("pure_cells") or [])],
            "cells": [[int(c[0]), int(c[1]), round(float(c[2]), 3)]
                      for c in (sign.get("cells") or [])],
        })

    hud = view.get("hud") or {}
    return {
        "width": width, "height": height,
        # The top-level shape of what the engine hands one seat. The novice
        # walkthrough shows this to make "the engine issues game state"
        # concrete, so it is read from the view rather than transcribed.
        "view_keys": sorted(view.keys()),
        "truth": truth,
        "live": live, "echo": echo,
        "probes": probes, "known_rival_probes": known_rival,
        "redsigns": signs,
        "day": int((view.get("meta") or {}).get("day") or 0),
        "day_cap": int(hud.get("season_day_cap") or 7),
        "score": int((hud.get("scores") or {}).get("p1") or 0),
        "counts": {
            "live": len(live), "echo": len(echo),
            "fog": view.get("grid", {}).get("cell_counts", {}).get("fog"),
        },
    }


# ── the card: the agent's side ────────────────────────────────────────

def _between(text: str, start: str, end: Optional[str]) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    if end is None:
        return text[i:].strip("\n")
    j = text.find(end, i)
    return text[i: j if j > 0 else len(text)].strip("\n")


def _block(text: str, start: str, end: Optional[str]) -> str:
    """Like :func:`_between`, but KEEPS the marker line.

    The markers here are the prompt's own section headings, and an excerpt that
    drops its heading opens mid-sentence — which reads as a bug on the page.
    """
    body = _between(text, start, end)
    return (start + body).strip("\n") if body else ""


def _dedent(block: str) -> str:
    lines = [ln for ln in block.split("\n")]
    pads = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    cut = min(pads) if pads else 0
    return "\n".join(ln[cut:] if ln.strip() else "" for ln in lines).strip("\n")


def parse_options(prompt: str) -> List[Dict[str, Any]]:
    """Each menu entry is `  [ID] headline` followed by indented detail lines."""
    menu = _between(prompt, "OPTION MENU (SELECT by ID", "=== YOUR TASK")
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for raw in menu.split("\n"):
        head = re.match(r"^  \[([A-Za-z0-9_#]+)\]\s+(.*)$", raw)
        if head:
            if cur:
                out.append(cur)
            cur = {"id": head.group(1), "headline": head.group(2).strip(),
                   "why": "", "walk": "", "yield": "", "risk": "", "dedupe": ""}
            continue
        if cur is None:
            continue
        line = raw.strip()
        for key, prefix in (("why", "WHY: "), ("walk", "walk: "),
                            ("yield", "yield: "), ("risk", "crush: "),
                            ("dedupe", "ALREADY COUNTED ELSEWHERE: ")):
            if line.startswith(prefix):
                cur[key] = line[len(prefix):]
                break
    if cur:
        out.append(cur)
    return out


def parse_card() -> Dict[str, Any]:
    text = CARD.read_text()

    header = _between(text, "=" * 72 + "\n", "\n" + "=" * 72)

    # The card prints BOTH prompts back to back under a single banner: the
    # THINK pass, then the PLAN pass, which repeats the whole board and adds
    # the think output. Only the first is banner-marked, so split on the second
    # SECTION 1 — otherwise "the prompt" reads as twice its real size.
    both = _between(
        text, "EXACTLY WHAT THE AGENT RECEIVES — THINK PROMPT (verbatim)",
        "  THE AGENT THINKING",
    )
    # Drop the banner rule, line-wise. A bare lstrip("=\n") also eats the "==="
    # off the first section header, which silently collapses the split below.
    lines = both.split("\n")
    while lines and (not lines[0].strip() or set(lines[0].strip()) == {"="}):
        lines.pop(0)
    while lines and (not lines[-1].strip() or set(lines[-1].strip()) == {"="}):
        lines.pop()
    both = "\n".join(lines)

    s1 = "=== SECTION 1 - THE GAME (how it works) ==="
    split = both.find(s1, both.find(s1) + 1)
    if split > 0:
        think_prompt = both[:split].rstrip("=\n ")
        plan_prompt = both[split:].strip("\n")
    else:
        think_prompt, plan_prompt = both, ""

    think = _dedent(_between(text, "STAGE 1 — THINK (bounded reasoning):",
                             "STAGE 2 — PLAN"))
    plan_block = _between(text, "STAGE 2 — PLAN (the decision it committed to):",
                          "-" * 72)
    raw_json = _between(plan_block, "raw PLAN JSON:", None)
    plan_json = _dedent(raw_json)

    packager_log = [
        ln.strip().lstrip("· ").strip()
        for ln in _between(text, "packager log:", "moves:").split("\n")
        if ln.strip()
    ]

    moves: List[Dict[str, Any]] = []
    for ln in _between(text, "moves:", "=" * 72).split("\n"):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                moves.append(json.loads(ln))
            except json.JSONDecodeError:
                pass

    world_view = []
    wv = _between(think_prompt, "cells:\n[", "]")
    for ln in wv.split("\n"):
        ln = ln.strip().rstrip(",")
        if ln.startswith("{"):
            try:
                world_view.append(json.loads(ln))
            except json.JSONDecodeError:
                pass

    # Section spans, for the "anatomy of the card" section: how much of the
    # prompt each part actually occupies.
    # Note the fourth mark: the option menu is physically inside SECTION 3,
    # after the journal, so measuring by section header alone credits ~40k
    # characters of menu to "memory". Split it out or the breakdown lies.
    sections = []
    marks = [
        ("RULES & DOCTRINE", "=== SECTION 1 - THE GAME (how it works) ==="),
        ("THE BOARD NOW",
         "=== SECTION 2 - THE BOARD NOW (what you can see right now) ==="),
        ("LAST NIGHT & JOURNAL",
         "=== SECTION 3 - WHAT HAPPENED LAST NIGHT (learn from it) ==="),
        ("THE OPTION MENU", "SITUATIONAL FACTS (reason over these"),
        ("YOUR TASK", "=== YOUR TASK (THINK"),
    ]
    for i, (label, marker) in enumerate(marks):
        start = think_prompt.find(marker)
        if start < 0:
            continue
        end = (think_prompt.find(marks[i + 1][1])
               if i + 1 < len(marks) else len(think_prompt))
        if end < 0:
            end = len(think_prompt)
        body = think_prompt[start:end]
        sections.append({
            "label": label,
            "chars": len(body),
            "lines": body.count("\n") + 1,
        })

    # The card hard-wraps the JSON to a fixed column, which breaks json.loads.
    # Unwrapping on whitespace is safe here because none of the string values
    # contain a newline of their own.
    plan_obj: Dict[str, Any] = {}
    try:
        plan_obj = json.loads(re.sub(r"\s*\n\s*", " ", plan_json).strip())
    except json.JSONDecodeError:
        pass

    plan_ids = plan_obj.get("plan") or []
    if not plan_ids:
        m = re.search(r'"plan":\s*\[(.*?)\]', plan_json, re.S)
        if m:
            plan_ids = re.findall(r'"([^"]+)"', m.group(1))

    return {
        "header": header,
        "prompt_chars": len(think_prompt),
        "prompt_lines": think_prompt.count("\n") + 1,
        "plan_prompt_chars": len(plan_prompt),
        "sections": sections,
        "world_view": world_view,
        "options": parse_options(think_prompt),
        "think": think,
        "plan_json": plan_json,
        "plan_obj": plan_obj,
        "plan_ids": plan_ids,
        # The whole prompt, so the guide can show it rather than describe it.
        # It is the single largest thing in this payload and that is the point.
        "full_think_prompt": think_prompt,
        "packager_log": packager_log,
        "moves": moves,
        "excerpts": {
            "world_view_intro": _dedent(_block(
                think_prompt, "WORLD VIEW — everything you can SEE right now,",
                "cells:")),
            "out_of_grid": _dedent(_block(
                think_prompt,
                "OUT-OF-GRID KNOWLEDGE — facts you KNOW but have NO direct",
                "RED SIGNS")),
            "red_signs": _dedent(_block(
                think_prompt, "RED SIGNS — a pure-RED seam", "\n\n  ~(")),
            "menu_preamble": _dedent(_block(
                think_prompt, "OPTION MENU (SELECT by ID —", "\n  [")),
            # Section 3 is the memory loop: the intent the agent set last
            # night, the engine's own per-hour log of what that became, and
            # the running journal of every night so far.
            "last_night": _block(think_prompt, "LAST NIGHT (day", "\nREFLECT ("),
            "journal": _block(
                think_prompt, "STRATEGY JOURNAL", "\nSITUATIONAL FACTS"),
        },
    }


# ── the harness itself ───────────────────────────────────────────────

def load_harness() -> Dict[str, Any]:
    """Line and def counts per module.

    The guide uses these to show how much Python stands between the engine's
    view and the model's prompt. Measured rather than transcribed so the bars
    cannot drift as the harness grows.
    """
    mods: Dict[str, Dict[str, int]] = {}
    for path in sorted(HARNESS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        lines = src.splitlines()
        mods[path.name] = {
            "lines": len(lines),
            # Rough but honest: top-level and nested `def`s, which is a better
            # proxy for "number of distinct operations" than raw line count.
            "defs": sum(1 for ln in lines if re.match(r"\s*def ", ln)),
        }
    return {
        "dir": str(HARNESS_DIR.relative_to(ROOT)),
        "modules": mods,
        "total_lines": sum(m["lines"] for m in mods.values()),
        "total_defs": sum(m["defs"] for m in mods.values()),
    }


def main() -> int:
    if not CARD.exists():
        print(f"card not found: {CARD}", file=sys.stderr)
        return 1
    print(f"reading card   {CARD.relative_to(ROOT)}")
    card = parse_card()
    print(f"  prompt {card['prompt_chars']:,} chars · "
          f"{len(card['world_view'])} world-view cells · "
          f"{len(card['options'])} options · {len(card['moves'])} moves")

    print(f"reading board  {SNAPSHOT}")
    board = load_board()
    print(f"  {board['width']}x{board['height']} · "
          f"{board['counts']['live']} live · {board['counts']['echo']} echo · "
          f"{len(board['redsigns'])} redsigns")

    harness = load_harness()
    print(f"reading harness {harness['dir']}")
    print(f"  {len(harness['modules'])} modules · "
          f"{harness['total_lines']:,} lines · {harness['total_defs']} defs")

    payload = {
        "_generated_by": "scripts/export_agent_guide_data.py",
        "snapshot": SNAPSHOT,
        "card_path": str(CARD.relative_to(ROOT)),
        "board": board,
        "card": card,
        "harness": harness,
    }
    OUT.write_text(
        "/* GENERATED by scripts/export_agent_guide_data.py — do not edit.\n"
        f" * Turn: {SNAPSHOT} · card: {CARD.relative_to(ROOT)}\n"
        " * Regenerate with:\n"
        " *   SOC_BACKEND=snowflake python scripts/export_agent_guide_data.py\n"
        " */\n"
        "window.AG_DATA = " + json.dumps(payload, indent=1) + ";\n"
    )
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("SOC_BACKEND", "snowflake")
    raise SystemExit(main())
