"""Stock V12's own run over the battles, frozen and checked in.

Every board carries a ``baseline`` string describing in prose what V12
did on it. That prose is good for reading and useless for comparing: it
cannot tell you that your fork walked four RED cells where V12 walked
six, and it goes stale the moment a board is retuned.

So the real thing is kept as well. ``baseline/cards/`` holds one card
per battle exactly as ``soc suite --cards`` writes them — the moves V12
issued, the checks it passed, and what it fired. It is checked in
rather than regenerated because generating it needs a Snowflake PAT and
several minutes of model calls, and the whole point is that an attendee
with neither can still answer "is my agent better than the thing I
forked, on this board, right now".

**It is a snapshot, not an oracle.** An LLM is not deterministic, so
this is one run of V12, not V12's true mean. Treat a 5% gap as noise
and a failed check V12 passed as a lead worth following.

Regenerate with::

    python scripts/soc.py suite --agent tabula_v12 --rung armed \\
        --loadout empty,emp --cards sea_of_colours/evals/battles/baseline/cards
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

_DIR = Path(__file__).parent / "baseline"


@lru_cache(maxsize=1)
def index() -> Mapping[str, Any]:
    """The summary table: battle id -> score, failures, ordnance."""
    path = _DIR / "index.json"
    if not path.exists():
        return {"battles": {}}
    return json.loads(path.read_text())


@lru_cache(maxsize=None)
def card(battle_id: str) -> Optional[Mapping[str, Any]]:
    """One frozen V12 turn, or ``None`` if this battle was not recorded.

    ``None`` is expected and must not raise: a board added after the
    snapshot has no baseline until someone regenerates it, and that is
    a missing comparison rather than an error.
    """
    # "board@rung+loadout" is the id; the file is board_rung_loadout_runN.
    slug = battle_id.replace("@", "_").replace("+", "_")
    hits = sorted(_DIR.glob(f"cards/{slug}_run*.json"))
    if not hits:
        return None
    return json.loads(hits[0].read_text())


def compare(battle_id: str, score: float) -> str:
    """One line placing a fork's score against the frozen V12 run.

    Deliberately blunt and deliberately hedged: the useful signal is the
    direction and the failed checks, not the decimal.
    """
    row = (index().get("battles") or {}).get(battle_id)
    if not row:
        return ""
    base = float(row.get("score") or 0.0)
    delta = score - base
    if abs(delta) < 0.05:
        verdict = "level with stock V12"
    elif delta > 0:
        verdict = f"{delta:+.0%} on stock V12"
    else:
        verdict = f"{delta:+.0%} against stock V12 — it did better here"
    failed = row.get("failed") or []
    tail = f"; V12 missed {', '.join(failed)}" if failed else "; V12 was clean"
    return f"baseline {base:.0%} ({verdict}){tail}"
