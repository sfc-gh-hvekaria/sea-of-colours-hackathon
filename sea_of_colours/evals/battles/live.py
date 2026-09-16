"""The fast loop — run ONE frozen turn and diff it against stock V12.

The suite answers "is my agent good": every board, every rung, several
runs each, and against a model that is twenty minutes. That is the right
shape for a question you ask twice a day and the wrong shape for the
question you actually ask every ten minutes, which is **"did the thing I
just changed do anything at all?"**

That question needs one turn. One V12-grade turn measures ~19s, and the
comparison is free because stock V12's run over every board is already
frozen in ``baseline/``. So the loop here is: pick a board, pick your
agent, wait twenty seconds, read a diff. Same boards, same predicates,
same cards — a hundredth of the wait.

**What this can and cannot tell you.** A single sample of a
non-deterministic model answers *categorical* questions reliably and
*quality* questions not at all:

* "Is BLIND_SCORCH in the menu now?" — structural. One run settles it.
  If your option is registered it is offered every time; if the schema
  forbids the verb it never appears. These are the questions you have
  while building, and they are the ones the four rungs are made of.
* "Is 71% better than 64%?" — not a finding. That is two samples of a
  distribution whose spread you have not measured.

So :func:`diff` sorts its output into ``categorical`` (trust it) and
``indicative`` (do not), and says so in the payload rather than leaving
the reader to know. ``--runs 3`` narrows the second kind without ever
making it a score; for a score, run the suite.

**Caching.** Every run is a model call against somebody's PAT, and the
whole point is to click around a menagerie of frozen turns. Results are
cached on the board, the dials, the agent and a fingerprint of the
agent's own source — so re-opening a turn is instant and free, and only
an agent you actually edited pays the twenty seconds again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from sea_of_colours.evals.battles import baseline, boards, ladder, recorder
from sea_of_colours.evals.battles import runner as battle_runner

#: Where cached live runs go. Under the repo rather than a temp dir so a
#: menagerie survives a reboot, and one directory so it can be deleted.
CACHE_ROOT = Path(
    os.environ.get("SOC_LIVE_CACHE")
    or Path(__file__).resolve().parents[3] / ".soc_livecache"
)


# ── identifying a version of an agent ───────────────────────────────

def _agent_source_dir(label: str) -> Optional[Path]:
    """Where this agent's code lives, for fingerprinting.

    Returns ``None`` for an agent we cannot locate, which makes the
    fingerprint fall back to "unknown" and disables caching for it. That
    is the safe direction: a stale cached turn is a lie, a missing one
    is twenty seconds.
    """
    from sea_of_colours.orchestrator_2 import agent_manifest

    found, _ = agent_manifest.discover()
    for man in found:
        if man.label == label:
            return man.directory
    built_in = agent_manifest.harness_root() / label
    if built_in.is_dir():
        return built_in
    if label.startswith("red_harvest"):
        return Path(__file__).resolve().parents[1].parent / "agent"
    return None


def fingerprint(label: str) -> str:
    """A short hash of everything this agent would run.

    Content, not mtime: a coding assistant rewriting a file it did not
    change must not invalidate the menagerie, and `git checkout` of an
    older version must not keep serving the newer result.
    """
    root = _agent_source_dir(label)
    if root is None:
        return "unknown"
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _cache_key(battle_id: str, agent: str, runs: int, fp: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in f"{battle_id}-{agent}")
    return f"{safe}-r{runs}-{fp}"


# ── running one turn ────────────────────────────────────────────────

def battle_ids() -> list[str]:
    """Every turn in the menagerie, as ``board@rung+loadout``."""
    return [
        f"{b.id}@{r.id}+{l.id}"
        for b in boards.BOARDS
        for r in ladder.RUNGS
        for l in ladder.LOADOUTS
    ]


def parse_battle_id(battle_id: str) -> tuple:
    """``board@rung+loadout`` -> the three objects, or raise ValueError."""
    board_id, _, rest = battle_id.partition("@")
    rung_id, _, loadout_id = (rest or "armed+empty").partition("+")
    matches = [b for b in boards.BOARDS if b.id == board_id]
    if not matches:
        raise ValueError(
            f"no board {board_id!r}. Try one of: "
            + ", ".join(b.id for b in boards.BOARDS)
        )
    return (
        matches[0],
        ladder.get_rung(rung_id or "armed"),
        ladder.get_loadout(loadout_id or "empty"),
    )


def run(
    battle_id: str,
    agent: str,
    *,
    runs: int = 1,
    use_cache: bool = True,
) -> dict:
    """Play one battle and return a bake-shaped payload plus a diff.

    The payload is deliberately the same shape the recorder writes, so
    the battle room renders a turn it has just produced with exactly the
    code that renders one frozen last week.
    """
    board, rung, loadout = parse_battle_id(battle_id)
    fp = fingerprint(agent)
    key = _cache_key(battle_id, agent, runs, fp)
    cached = _read_cache(key) if (use_cache and fp != "unknown") else None
    if cached is not None:
        cached["cached"] = True
        return cached

    rec = recorder.Recorder(agent=agent, bake_id=f"live-{key}")
    started = time.time()
    result = battle_runner.run_battle(
        board, rung, loadout, agent=agent, runs=runs, recorder=rec,
    )
    payload = _bake_payload(rec)
    payload.update(
        battle_id=battle_id,
        agent=agent,
        runs=runs,
        fingerprint=fp,
        seconds=round(time.time() - started, 1),
        cached=False,
        score=round(result.score, 3),
        passed=result.rate == 1.0,
        fell_back=any(r.fell_back for r in result.runs),
        diff=diff(result, rec, battle_id, runs),
    )
    if fp != "unknown":
        _write_cache(key, payload)
    return payload


def _bake_payload(rec: recorder.Recorder) -> dict:
    """The recorder's own on-disk shape, without touching the disk."""
    turns = [asdict(t) for t in rec.bake.turns]
    pool = rec._pool(turns)
    return {
        "id": rec.bake.id,
        "created": rec.bake.created,
        "grids": pool["grids"],
        "boards": pool["boards"],
        "turns": turns,
    }


# ── the diff ────────────────────────────────────────────────────────

def _menu_lines(card: Mapping[str, Any]) -> list[str]:
    """The option menu as a list of lines, from the blob the harness logs."""
    blob = card.get("options_offered") or ""
    if isinstance(blob, list):
        return [str(x).strip() for x in blob if str(x).strip()]
    return [ln.strip() for ln in str(blob).splitlines() if ln.strip()]


def _option_ids(lines: Sequence[str]) -> set[str]:
    """Leading option id of each menu line.

    Comparing whole lines would report a diff every time a coordinate
    moved. What a builder wants to know is whether a *kind* of play
    appeared or vanished.

    An id has to contain a digit or an underscore — ``PR1``,
    ``BLIND_AND_GRAB``, ``HD1L``. Without that test the menu's own
    section labels (``BUDGET:``, ``WHY:``) parse as options and every
    diff opens with two plays that do not exist.
    """
    out = set()
    for line in lines:
        # The id is bracketed and leads the line — "[PR1] probe at
        # (7,13): extends the sign". Reading the last token before the
        # colon instead picks up the coordinate and drops every real id.
        match = _BRACKET_ID.search(line)
        if match:
            token = match.group(1)
        else:
            head = line.split(":")[0].strip().split()
            token = head[0].strip("[]()") if head else ""
        if not token or not token.isupper():
            continue
        if not token.replace("_", "").isalnum():
            continue
        if any(c.isdigit() for c in token) or "_" in token:
            out.add(token)
    return out


_BRACKET_ID = re.compile(r"\[([A-Z][A-Z0-9_]*)\]")


_PLAN_RE = re.compile(r"\[plan=[^:\]]*:\s*([^\]]+)\]")


def _chosen(card: Mapping[str, Any], rationale: str) -> list[str]:
    """The option ids this turn actually settled on.

    Read from the card when the harness recorded them. The frozen V12
    turns pre-date that field, but their rationale carries the same
    thing as ``[plan=aggressive: BLIND_AND_GRAB, PR1, …]`` — and the
    plan comparison is the single most useful line in the diff, so it
    is worth parsing rather than leaving blank.
    """
    ids = card.get("options_chosen")
    if isinstance(ids, (list, tuple)) and ids:
        return [str(x) for x in ids]
    match = _PLAN_RE.search(str(rationale or ""))
    if not match:
        return []
    return [p.strip() for p in match.group(1).split(",") if p.strip()]


def _verbs(moves: Sequence[Mapping[str, Any]]) -> dict:
    out: dict[str, int] = {}
    for m in moves:
        verb = str(m.get("a") or "?")
        out[verb] = out.get(verb, 0) + 1
    return out


def diff(result, rec: recorder.Recorder, battle_id: str, runs: int) -> dict:
    """Compare this run against the frozen V12 turn for the same battle.

    Sorted into what a single sample can and cannot support. The
    ``categorical`` block is structural — an option is registered or it
    is not, a verb reaches the engine or it cannot. The ``indicative``
    block is sampled, and carries its own health warning.
    """
    theirs = baseline.card(battle_id)
    mine_turn = rec.bake.turns[0] if rec.bake.turns else None
    if mine_turn is None:
        return {"available": False, "why": "the run produced no turn"}
    if theirs is None:
        return {
            "available": False,
            "why": (
                f"no frozen V12 turn for {battle_id} — this board or dial "
                f"combination was added after the baseline was recorded. "
                f"Re-record it (see baseline/README.md) or pick another."
            ),
        }

    my_card = mine_turn.card or {}
    my_menu = _option_ids(_menu_lines(my_card))
    their_menu = _option_ids(_menu_lines(theirs.get("card") or {}))
    my_moves = list(mine_turn.moves)
    their_moves = list(theirs.get("moves") or [])
    my_verbs, their_verbs = _verbs(my_moves), _verbs(their_moves)

    my_fired = sum((mine_turn.result.get("weapons_fired") or {}).values())
    their_fired = sum(
        (theirs.get("result", {}).get("weapons_fired") or {}).values()
    )

    my_failed = {c["name"] for c in mine_turn.checks if not c["passed"]}
    their_failed = {c["name"] for c in theirs.get("checks") or []
                    if not c.get("passed")}

    my_score = float(result.score)
    their_score = float(theirs.get("result", {}).get("score") or 0.0)

    # The frozen turns do not carry the menu they were offered, only
    # what they picked. Comparing against an empty menu would report
    # every option the fork has as newly "gained", which is the most
    # misleading thing this tool could say — so the comparison is
    # skipped and labelled rather than faked.
    menus_comparable = bool(my_menu and their_menu)
    categorical = {
        "menus_comparable": menus_comparable,
        "options_gained": sorted(my_menu - their_menu) if menus_comparable else [],
        "options_lost": sorted(their_menu - my_menu) if menus_comparable else [],
        "options_chosen": {
            "mine": _chosen(my_card, mine_turn.result.get("rationale") or ""),
            "theirs": _chosen(
                theirs.get("card") or {}, theirs.get("rationale") or "",
            ),
        },
        "verbs_gained": sorted(set(my_verbs) - set(their_verbs)),
        "verbs_lost": sorted(set(their_verbs) - set(my_verbs)),
        "weapons_fired": {"mine": my_fired, "theirs": their_fired},
        "corrections": {
            "mine": len(my_card.get("corrections_diff") or []),
            "theirs": len(
                (theirs.get("card") or {}).get("corrections_diff") or []
            ),
        },
    }
    indicative = {
        "score": {
            "mine": round(my_score, 3),
            "theirs": round(their_score, 3),
            "delta": round(my_score - their_score, 3),
        },
        "checks_only_you_failed": sorted(my_failed - their_failed),
        "checks_only_v12_failed": sorted(their_failed - my_failed),
        "checks_both_failed": sorted(my_failed & their_failed),
        "moves": {"mine": len(my_moves), "theirs": len(their_moves)},
        "caveat": (
            "one sample each of a non-deterministic model — read the "
            "direction, never the decimal. Run the suite for a score."
            if runs < 3 else
            f"{runs} runs against one frozen V12 run. Better, still not a "
            f"score: the baseline is a single sample too."
        ),
    }
    return {
        "available": True,
        "baseline_agent": "tabula_v12",
        "categorical": categorical,
        "indicative": indicative,
        "headline": _headline(categorical, indicative),
    }


def _headline(categorical: dict, indicative: dict) -> str:
    """One line answering "did anything change?" — the whole question.

    Leads with structure because that is what a single run can prove,
    and refuses to lead with the score even when the score moved.
    """
    bits = []
    if categorical["weapons_fired"]["mine"] and not \
            categorical["weapons_fired"]["theirs"]:
        bits.append(
            f"you fired {categorical['weapons_fired']['mine']} "
            f"where V12 fired nothing"
        )
    mine = categorical["options_chosen"]["mine"]
    theirs = categorical["options_chosen"]["theirs"]
    swapped = sorted(set(mine) - set(theirs))
    if swapped and theirs:
        bits.append(
            "you played " + ", ".join(swapped) + " where V12 played "
            + ", ".join(sorted(set(theirs) - set(mine)) or theirs)
        )
    if categorical["options_gained"]:
        bits.append("new options: " + ", ".join(categorical["options_gained"]))
    if categorical["verbs_gained"]:
        bits.append("new verbs reached the engine: "
                    + ", ".join(categorical["verbs_gained"]))
    if categorical["verbs_lost"]:
        bits.append("verbs V12 used and you did not: "
                    + ", ".join(categorical["verbs_lost"]))
    if not bits:
        d = indicative["score"]["delta"]
        if abs(d) < 0.01:
            return (
                "nothing structural changed, and the score matched. If you "
                "expected a difference, the change did not reach this turn."
            )
        return (
            "nothing structural changed — same options, same verbs, same "
            "ordnance. The score moved, but one run cannot tell you whether "
            "that is your change or the model's mood."
        )
    return "; ".join(bits)


# ── cache ───────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    return CACHE_ROOT / f"{key}.json"


def _read_cache(key: str) -> Optional[dict]:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # A half-written or hand-mangled cache entry is not worth an
        # error — it is worth twenty seconds.
        return None


def _write_cache(key: str, payload: dict) -> None:
    try:
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = _cache_path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, default=str))
        tmp.replace(_cache_path(key))
    except OSError:
        pass  # caching is an optimisation, never a requirement


def clear_cache() -> int:
    """Forget every cached run. Returns how many were dropped."""
    if not CACHE_ROOT.exists():
        return 0
    n = 0
    for path in CACHE_ROOT.glob("*.json"):
        path.unlink()
        n += 1
    return n


def cached_runs() -> list[dict]:
    """What is already in the menagerie, for a UI to grey in."""
    out = []
    for path in sorted(CACHE_ROOT.glob("*.json")) if CACHE_ROOT.exists() else []:
        try:
            blob = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "battle_id": blob.get("battle_id"),
            "agent": blob.get("agent"),
            "runs": blob.get("runs"),
            "score": blob.get("score"),
            "fingerprint": blob.get("fingerprint"),
        })
    return out
