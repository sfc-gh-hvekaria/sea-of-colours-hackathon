"""Record a suite run so it can be replayed and read afterwards.

``soc suite`` prints a verdict and throws the turn away. That is the
wrong thing to keep: a percentage tells you *that* an agent misplayed a
board, and the only way to learn *why* is to see the position it was
looking at, the prompt it was given, what it thought, what it ordered,
and what the compiler did to those orders on the way to the engine.

A **bake** is one suite run, frozen on disk. It holds, per turn:

* the staged board as the agent found it — terrain, fleets, redsigns;
* the moves that actually reached the engine, in order;
* the card — prompt, reasoning, the option menu it chose from, and the
  packager's corrections;
* every predicate, with the board's canonical play for comparison.

Two deliberate choices about the format.

**It is written as JavaScript, not JSON.** A browser will not ``fetch``
a sibling file over ``file://``, so a review page opened by
double-clicking cannot read JSON off disk. Assigning to a global from a
``<script>`` tag works everywhere, which is what lets the room open with
no server running — the same trick ``manual/agent-data.js`` uses.

**It never touches the player's games.** Bakes live under their own
root and carry no session the normal UI can reach. A review of a
scenario turn cannot appear in someone's season list, and deleting the
whole directory loses nothing but reviews.

The JSON alongside each bake is the machine-readable truth; the ``.js``
file is a one-line wrapper around it for the browser.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

#: Where bakes go unless a caller says otherwise. Under ``reports/``
#: because they are large, regenerable and nobody wants them in a diff.
DEFAULT_ROOT = Path("reports/battles")

#: Card fields lifted out of the harness envelope. Keys are what the
#: room shows; values are the envelope's ``extras`` names in fallback
#: order, because forks rename things and a missing panel should be a
#: missing panel, not a crash.
_CARD_FIELDS: dict[str, tuple[str, ...]] = {
    "prompt": ("thinker_prompt", "plan_prompt", "mover_prompt"),
    "reasoning": ("thinker_reasoning",),
    "directive": ("thinker_directive",),
    "options_offered": ("option_menu_block",),
    "options_chosen": ("selected_option_ids",),
    "proposed_moves": ("final_moves",),
    "corrections": ("packager_log", "sanitizer_changes", "compiler_notes"),
    "plan_label": ("plan_label",),
    "predicted_outcome": ("predicted_outcome",),
}


@dataclass
class Turn:
    """One agent turn, complete enough to replay and read."""

    id: str
    board: dict = field(default_factory=dict)
    rung: dict = field(default_factory=dict)
    loadout: dict = field(default_factory=dict)
    run_index: int = 0
    grid: dict = field(default_factory=dict)
    vision: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    redsigns: list = field(default_factory=list)
    moves: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    card: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)


@dataclass
class Bake:
    """One suite run."""

    id: str
    agent: str
    created: float
    turns: list = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for t in self.turns if t.result.get("passed"))


# ── capture ───────────────────────────────────────────────────────


def _tile_name(cell: Any) -> str:
    tile = getattr(cell, "tile", None)
    return str(getattr(tile, "name", tile) or "EMPTY").upper()


#: Single-letter tile codes. A bake is read by a browser over file://,
#: so it is a script tag's worth of bytes on the main thread — a full
#: suite is 500+ turns and the terrain dominates. Four-element arrays
#: with a one-char code are about a quarter the size of named objects.
_TILE_CODE = {"RED": "R", "GREEN": "G", "BLUE": "B"}


def snapshot_grid(session: Any) -> dict:
    """The board as a sparse, compact cell list.

    Sparse because a 40x28 board is 1,120 cells and all but a few dozen
    are empty. Cells are ``[x, y, code, purity]`` — see ``_TILE_CODE``.
    """
    width = int(getattr(session, "width", 0) or 0)
    height = int(getattr(session, "height", 0) or 0)
    grid = getattr(session, "grid", None) or []
    cells: list[list] = []
    for y in range(min(height, len(grid))):
        row = grid[y] or []
        for x in range(min(width, len(row))):
            cell = row[x]
            name = _tile_name(cell)
            purity = int(getattr(cell, "purity", 0) or 0)
            if name == "EMPTY" and purity == 0:
                continue
            cells.append([x, y, _TILE_CODE.get(name, name[:1]), purity])
    return {"width": width, "height": height, "cells": cells}


def snapshot_entities(session: Any) -> list[dict]:
    """Fleets and probes, with their positions before the turn."""
    out: list[dict] = []
    for uid, ent in (getattr(session, "entities", {}) or {}).items():
        etype = str(getattr(ent, "entity_type", "") or "")
        x, y = getattr(ent, "x", None), getattr(ent, "y", None)
        out.append({
            "id": str(uid),
            "type": etype,
            "owner": str(getattr(ent, "owner", "") or ""),
            "x": None if x is None else int(x),
            "y": None if y is None else int(y),
            "damaged": bool(getattr(ent, "damaged", False)),
        })
    out.sort(key=lambda e: e["id"])
    return out


def snapshot_vision(session: Any, seat: str) -> list[list[int]]:
    """The cells the seat could actually see when it planned.

    Without this the room shows ground truth, which is the one thing the
    agent did not have. Half the failures in this suite are an agent
    behaving sensibly given what it could see, and you cannot tell those
    apart from a genuine misread unless the fog is on the picture.
    """
    try:
        live = session.tiles_visible_now(seat)
    except Exception:
        return []
    return sorted([int(x), int(y)] for x, y in live)


def snapshot_redsigns(session: Any) -> list[dict]:
    out: list[dict] = []
    for sign in getattr(session, "redsign", None) or []:
        if not isinstance(sign, Mapping):
            continue
        centre = sign.get("center") or sign.get("centre") or []
        try:
            cx, cy = int(centre[0]), int(centre[1])
        except (TypeError, ValueError, IndexError):
            continue
        cells = []
        for c in sign.get("cells") or []:
            try:
                cells.append({"x": int(c[0]), "y": int(c[1])})
            except (TypeError, ValueError, IndexError):
                continue
        out.append({"id": str(sign.get("id") or ""), "cx": cx, "cy": cy,
                    "cells": cells})
    return out


def _first(extras: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in extras and extras[name] not in (None, "", [], {}):
            return extras[name]
    return None


def extract_card(env: Mapping[str, Any] | None) -> dict:
    """Pull the readable card out of the harness envelope.

    Structured data, not rendered text — the harness builds all of this
    before it formats anything, so there is nothing to parse.

    A heuristic agent has no card at all. That returns ``{}`` and the
    room says so, which is honest: the answer to "what was it thinking"
    for a heuristic is "it wasn't".
    """
    extras = ((env or {}).get("extras") or {})
    if not isinstance(extras, Mapping):
        return {}
    card: dict[str, Any] = {}
    for key, names in _CARD_FIELDS.items():
        val = _first(extras, names)
        if val is not None:
            card[key] = val
    return card


def diff_orders(proposed: Sequence[Any], played: Sequence[Any]) -> list[dict]:
    """Where the compiler changed the agent's mind.

    Often the most useful panel in the room: an agent blamed for a
    stupid move quite regularly issued a sensible one that the packager
    rewrote. Reported positionally, which is what the compilers do.
    """
    out: list[dict] = []
    for i in range(max(len(proposed), len(played))):
        a = proposed[i] if i < len(proposed) else None
        b = played[i] if i < len(played) else None
        if a == b:
            continue
        out.append({
            "index": i,
            "proposed": a,
            "played": b,
            "kind": ("dropped" if b is None else
                     "added" if a is None else "rewritten"),
        })
    return out


class Recorder:
    """Collects turns during a suite run and writes the bake at the end."""

    def __init__(
        self,
        *,
        agent: str,
        root: Path | str = DEFAULT_ROOT,
        bake_id: str | None = None,
    ) -> None:
        self.agent = agent
        self.root = Path(root)
        self.created = time.time()
        self.bake = Bake(
            id=bake_id or self._default_id(agent, self.created),
            agent=agent,
            created=self.created,
        )

    @staticmethod
    def _default_id(agent: str, when: float) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when))
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in agent)
        return f"{stamp}-{safe}"

    def capture(
        self,
        battle: Any,
        result: Any,
        *,
        env: Mapping[str, Any] | None,
        session: Any,
    ) -> None:
        """Record one turn. ``session`` is the board BEFORE the agent moved."""
        card = extract_card(env)
        proposed = card.get("proposed_moves") or []
        turn = Turn(
            id=f"{battle.id}#{result.run_index}",
            board={
                "id": battle.board.id,
                "day": battle.board.day,
                "season_day_cap": battle.board.season_day_cap,
                "shape": battle.board.shape,
                "question": battle.board.question,
                "canonical": battle.board.canonical,
                "observation": battle.board.observation,
                "baseline": battle.board.baseline,
            },
            rung={"id": battle.rung.id, "summary": battle.rung.summary,
                  "teaches": battle.rung.teaches},
            loadout={"id": battle.loadout.id, "note": battle.loadout.note},
            run_index=result.run_index,
            grid=snapshot_grid(session),
            vision=snapshot_vision(session, battle.seat),
            entities=snapshot_entities(session),
            redsigns=snapshot_redsigns(session),
            moves=list(result.moves),
            checks=[asdict(c) for c in result.checks],
            card=card,
            result={
                "passed": result.passed,
                "score": round(result.score, 3),
                "seconds": result.seconds,
                "error": result.error,
                "rationale": result.rationale,
                "fell_back": result.fell_back,
                "weapons_fired": dict(result.weapons_fired),
                "seat": battle.seat,
            },
        )
        if proposed:
            turn.card["corrections_diff"] = diff_orders(proposed, result.moves)
        self.bake.turns.append(turn)

    # ── writing ───────────────────────────────────────────────────

    def _pool(self, turns: list[dict]) -> dict:
        """Hoist repeated board text and terrain out of the turn list.

        The same board is staged once per rung, per loadout, per run, and
        its terrain and prose are identical every time. Left inline, a
        full suite of 540 turns is ~20MB of duplicated grid — enough to
        make the room slow to open for no information at all. Pooled, the
        same run is a small fraction of that.
        """
        grids: dict[str, dict] = {}
        boards: dict[str, dict] = {}
        for t in turns:
            board = t["board"]
            boards.setdefault(board["id"], board)
            t["board"] = board["id"]

            grid = t["grid"]
            key = f"g{len(grids)}"
            for existing, blob in grids.items():
                if blob == grid:
                    key = existing
                    break
            grids.setdefault(key, grid)
            t["grid"] = key
        return {"grids": grids, "boards": boards}

    def finish(self) -> Path:
        """Write the bake, refresh the index, and install the room."""
        data_dir = self.root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        turns = [asdict(t) for t in self.bake.turns]
        pool = self._pool(turns)
        payload = {
            "id": self.bake.id,
            "agent": self.bake.agent,
            "created": self.bake.created,
            "grids": pool["grids"],
            "boards": pool["boards"],
            "turns": turns,
        }
        (data_dir / f"{self.bake.id}.json").write_text(
            json.dumps(payload, indent=1, default=str), encoding="utf-8",
        )
        # The browser copy is minified — it is parsed, never read, and a
        # bake is loaded synchronously by a script tag.
        compact = json.dumps(payload, separators=(",", ":"), default=str)
        (data_dir / f"{self.bake.id}.js").write_text(
            "window.SOC_BATTLE_BAKES = window.SOC_BATTLE_BAKES || {};\n"
            f"window.SOC_BATTLE_BAKES[{json.dumps(self.bake.id)}] = {compact};\n",
            encoding="utf-8",
        )
        write_index(self.root)
        return install_room(self.root)


def read_bakes(root: Path | str = DEFAULT_ROOT) -> list[dict]:
    """Summaries of every bake on disk, newest first."""
    data_dir = Path(root) / "data"
    if not data_dir.is_dir():
        return []
    out: list[dict] = []
    for path in data_dir.glob("*.json"):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        turns = blob.get("turns") or []
        out.append({
            "id": blob.get("id") or path.stem,
            "agent": blob.get("agent") or "?",
            "created": blob.get("created") or 0,
            "turns": len(turns),
            "passed": sum(
                1 for t in turns if (t.get("result") or {}).get("passed")
            ),
            "boards": sorted(blob.get("boards") or {}),
        })
    out.sort(key=lambda b: b["created"], reverse=True)
    return out


def write_index(root: Path | str = DEFAULT_ROOT) -> Path:
    """Regenerate the browser-loadable index from what is on disk.

    Rebuilt by scanning rather than appended to, so deleting a bake's
    JSON is all it takes to remove it from the room.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / "index.js"
    dest.write_text(
        "window.SOC_BATTLE_INDEX = "
        + json.dumps(read_bakes(root), indent=1)
        + ";\n",
        encoding="utf-8",
    )
    return dest


def install_room(root: Path | str = DEFAULT_ROOT) -> Path:
    """Copy the review room next to the data it reads.

    Copied rather than linked so the whole directory can be zipped and
    opened on a machine with no repo — which is how a bake gets shared.

    Landed as ``index.html`` so the one directory serves both ways: a
    double-click opens it over ``file://``, and the server's ``/battles``
    mount resolves the bare directory to it.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent / "room" / "room.html"
    dest = root / "index.html"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dest
