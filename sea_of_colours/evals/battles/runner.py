"""Run an agent through the battles and score what it played.

The loop is small on purpose: stage a board, let the agent plan one
night, read the moves that actually reached the engine, and run the
board's predicates over them. Everything interesting is in the boards
and the predicates; this file only has to be correct.

**Repeats are the point, not a formality.** An LLM agent is not
deterministic, and a board it passes three times in five is a board it
does not understand — that distinction is invisible at ``--runs 1`` and
it is usually the most useful thing the suite tells you. Runs are scored
independently and reported as a fraction.

**Every run keeps its card.** When a board fails, the next question is
always "what was it looking at" — the prompt it was given, the reasoning
it returned, and the moves that survived packaging. Reconstructing that
after the fact is impossible, so it is captured as the run happens and
written next to the result.

Nothing here needs Snowflake. The staging is in-memory and the heuristic
agent runs offline, so the whole suite is exercisable on a laptop on a
train; only an LLM-backed agent needs credentials.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from sea_of_colours.evals import dispatch
from sea_of_colours.evals.battles import predicates
from sea_of_colours.evals.battles.boards import Board
from sea_of_colours.evals.battles.ladder import Loadout, Rung
from sea_of_colours.evals.battles.stage import StagedBattle, stage
from sea_of_colours.snowpark import engine as soc_engine



@dataclass
class RunResult:
    """One agent, one board, one attempt."""

    battle_id: str
    board_id: str
    rung_id: str
    loadout_id: str
    run_index: int
    checks: list[predicates.Check] = field(default_factory=list)
    moves: list[Mapping[str, Any]] = field(default_factory=list)
    weapons_fired: Mapping[str, int] = field(default_factory=dict)
    rationale: str = ""
    seconds: float = 0.0
    error: str = ""
    # Did the harness fail to reach its model and play its own safety
    # net instead? Recorded because the alternative is scoring a
    # heuristic under a team's name — an agent whose credentials were
    # down can otherwise outrank one that actually worked, and nothing
    # in the numbers would show it.
    fell_back: bool = False

    @property
    def passed(self) -> bool:
        return not self.error and all(c.passed for c in self.checks)

    @property
    def score(self) -> float:
        """Fraction of predicates met. Partial credit is deliberate.

        A night that banks the pure but wanders afterwards is a
        different failure from one that never found the seam, and
        collapsing both to "failed" throws away the signal that tells
        you which one you are looking at.
        """
        if self.error or not self.checks:
            return 0.0
        return sum(1 for c in self.checks if c.passed) / len(self.checks)

    @property
    def failures(self) -> list[predicates.Check]:
        return [c for c in self.checks if not c.passed]


@dataclass
class BattleResult:
    """All attempts at one board/rung/loadout."""

    battle_id: str
    board: Board
    rung: Rung
    loadout: Loadout
    runs: list[RunResult] = field(default_factory=list)

    @property
    def passes(self) -> int:
        return sum(1 for r in self.runs if r.passed)

    @property
    def rate(self) -> float:
        return self.passes / len(self.runs) if self.runs else 0.0

    @property
    def score(self) -> float:
        return (
            sum(r.score for r in self.runs) / len(self.runs)
            if self.runs else 0.0
        )

    @property
    def weapons_fired(self) -> dict[str, int]:
        """Ordnance spent across this battle's runs.

        Summed rather than averaged: with ``--runs 3`` the question is
        whether the agent EVER reaches for the rack on this shape of
        night, and one salvo in three attempts is a different finding
        from none in three.
        """
        out: dict[str, int] = {}
        for r in self.runs:
            for k, v in (r.weapons_fired or {}).items():
                out[k] = out.get(k, 0) + int(v)
        return out

    @property
    def flaky(self) -> bool:
        """Passed sometimes. Usually more informative than a clean fail."""
        return 0 < self.passes < len(self.runs)

    def recurring_failures(self) -> list[tuple[str, int]]:
        """Which predicates failed, and how often. The thing to fix first."""
        tally: dict[str, int] = {}
        for run in self.runs:
            for check in run.failures:
                tally[check.name] = tally.get(check.name, 0) + 1
        return sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class SuiteResult:
    agent: str
    battles: list[BattleResult] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    @property
    def score(self) -> float:
        return (
            sum(b.score for b in self.battles) / len(self.battles)
            if self.battles else 0.0
        )

    @property
    def clean(self) -> int:
        return sum(1 for b in self.battles if b.rate == 1.0)

    def by_rung(self) -> dict[str, tuple[int, int]]:
        """``rung -> (clean, total)``. Where an agent stops is the finding."""
        out: dict[str, list[int]] = {}
        for b in self.battles:
            slot = out.setdefault(b.rung.id, [0, 0])
            slot[0] += 1 if b.rate == 1.0 else 0
            slot[1] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    def weapons_fired(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for b in self.battles:
            for r in b.runs:
                for k, v in (r.weapons_fired or {}).items():
                    total[k] = total.get(k, 0) + v
        return total

    @property
    def fallback_rate(self) -> float:
        """Share of turns where the harness never got a usable reply.

        Anything above zero makes the rest of the numbers suspect: the
        score is partly the safety net's, not the agent's.
        """
        runs = [r for b in self.battles for r in b.runs]
        if not runs:
            return 0.0
        return sum(1 for r in runs if r.fell_back) / len(runs)


def _extract_moves(store, session_id: str, day: int, player: str) -> list:
    """Read the moves that actually reached the engine.

    Deliberately read back out of the store rather than taken from the
    agent's return value: what the agent proposed and what survived
    packaging are different things, and the second is what got played.
    Several of these boards exist precisely because those two diverged —
    on one of them the packager deleted an entire harvester run and the
    corrector substituted a plan nobody asked for.

    Reuses the eval runner's reader rather than reimplementing it. That
    function is phase-aware and knows the store's two shapes; a second
    copy here would be one more thing to keep in step with the engine.
    """
    from sea_of_colours.evals.runner import _extract_policy_from_store

    return list(_extract_policy_from_store(store, session_id, day, player) or [])


def run_battle(
    board: Board,
    rung: Rung,
    loadout: Loadout,
    *,
    agent: str = "red_harvest",
    runs: int = 1,
    card_dir: Path | None = None,
    seed: int = 4242,
    recorder=None,
) -> BattleResult:
    """Stage one battle and play it ``runs`` times."""
    out = BattleResult(
        battle_id=f"{board.id}@{rung.id}+{loadout.id}",
        board=board, rung=rung, loadout=loadout,
    )
    for i in range(runs):
        # Restaged every run so an attempt cannot inherit the previous
        # one's board. Cheap, and the alternative is a suite whose
        # later runs quietly test a different position.
        battle = stage(board, rung, loadout, seed=seed + i)
        out.runs.append(
            _play(battle, agent=agent, run_index=i, card_dir=card_dir,
                  recorder=recorder)
        )
    return out


def _play(
    battle: StagedBattle, *, agent: str, run_index: int, card_dir: Path | None,
    recorder=None,
) -> RunResult:
    result = RunResult(
        battle_id=battle.id,
        board_id=battle.board.id,
        rung_id=battle.rung.id,
        loadout_id=battle.loadout.id,
        run_index=run_index,
    )
    started = time.time()
    env: Mapping[str, Any] | None = None
    # The board BEFORE the agent moved — what it was looking at. Held for
    # the recorder, which has to show the position that prompted the play
    # rather than the wreckage afterwards.
    sess = None
    try:
        sess = soc_engine._hydrate_session(battle.store, battle.session_id)
        day = int(sess.day)

        env = _dispatch(battle, agent)
        moves = _extract_moves(battle.store, battle.session_id, day, battle.seat)
        after = soc_engine._hydrate_session(battle.store, battle.session_id)

        play = predicates.Play(moves=moves, session=after)
        result.moves = list(moves)
        result.checks = predicates.evaluate(play, battle.board)
        result.weapons_fired = predicates.weapon_usage(play)
        result.rationale = str(
            (env or {}).get("rationale") or (env or {}).get("agent_rationale") or ""
        )
        result.fell_back = _detect_fallback(env, result.rationale)
    except Exception as exc:
        # A crash is a result, not an abort: one board that blows up
        # must not cost you the other forty-four, and the traceback is
        # more useful in the report than on a dead terminal.
        result.error = f"{type(exc).__name__}: {exc}"
        result.rationale = traceback.format_exc(limit=6)
    result.seconds = round(time.time() - started, 2)

    if card_dir is not None:
        _write_card(card_dir, battle, result)
    if recorder is not None and sess is not None:
        # A crash still gets recorded — a turn that blew up is exactly
        # the one you want to open in the room.
        recorder.capture(battle, result, env=env, session=sess)
    return result


def _detect_fallback(env: Mapping[str, Any] | None, rationale: str) -> bool:
    """Did the harness play its safety net rather than its own plan?

    V12 and its forks answer honestly — they stamp ``fallback=True`` into
    the rationale when the model could not be reached or its reply would
    not parse. Nothing consumed that, so a suite run with no credentials
    scored the fallback heuristic and reported it as the agent.

    Checked structurally first and by marker second, because the marker
    is a formatting detail of one harness and a fork may reword it.
    """
    for key in ("fallback", "used_fallback", "is_fallback"):
        val = (env or {}).get(key)
        if isinstance(val, bool):
            return val
    text = (rationale or "").lower()
    return "fallback=true" in text or "plan=[fallback]" in text


def _dispatch(battle: StagedBattle, agent: str) -> Mapping[str, Any]:
    """Hand the turn to whichever runtime owns this agent."""
    return dispatch.play_turn(
        battle.store, battle.session_id, battle.seat, agent,
    )


def _write_card(card_dir: Path, battle: StagedBattle, result: RunResult) -> None:
    """Persist everything needed to understand this run later.

    Written even when the run passed: the most valuable comparison in
    tuning is a passing card against a failing one on the same board.
    """
    card_dir.mkdir(parents=True, exist_ok=True)
    name = f"{battle.id.replace('@', '_').replace('+', '_')}_run{result.run_index}"
    payload = {
        "battle": battle.id,
        "board": {
            "id": battle.board.id,
            "day": battle.board.day,
            "shape": battle.board.shape,
            "question": battle.board.question,
            "canonical": battle.board.canonical,
            "baseline": battle.board.baseline,
        },
        "rung": {"id": battle.rung.id, "summary": battle.rung.summary,
                 "teaches": battle.rung.teaches},
        "loadout": {"id": battle.loadout.id, "note": battle.loadout.note},
        "result": {
            "passed": result.passed,
            "score": round(result.score, 3),
            "seconds": result.seconds,
            "error": result.error,
            "weapons_fired": dict(result.weapons_fired),
        },
        "checks": [asdict(c) for c in result.checks],
        "moves": list(result.moves),
        "rationale": result.rationale,
    }
    (card_dir / f"{name}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def run_suite(
    boards_: Sequence[Board],
    rungs: Sequence[Rung],
    loadouts: Sequence[Loadout],
    *,
    agent: str = "red_harvest",
    runs: int = 1,
    card_dir: Path | None = None,
    seed: int = 4242,
    on_battle=None,
    recorder=None,
) -> SuiteResult:
    """Every board against every rung against every loadout.

    ``on_battle`` is called with each :class:`BattleResult` as it lands
    so a CLI can print progress. An LLM agent takes real seconds per
    turn, and a suite that prints nothing for ten minutes looks hung.
    """
    suite = SuiteResult(agent=agent)
    for board in boards_:
        for rung in rungs:
            for loadout in loadouts:
                res = run_battle(
                    board, rung, loadout,
                    agent=agent, runs=runs, card_dir=card_dir, seed=seed,
                    recorder=recorder,
                )
                suite.battles.append(res)
                if on_battle is not None:
                    on_battle(res)
    return suite
