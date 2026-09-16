"""Play a whole season headlessly, with any agent in any seat.

The battles suite asks whether an agent plays one hard night correctly.
This asks the other question: whether it can hold a season together —
bank early, buy the right thing, survive contact, and still be scoring
on the last night. An agent can be excellent at both and terrible at one.

Two callers, one loop:

* an attendee running their fork against opponents they picked, offline,
  to watch it back in the normal replay UI;
* tournament night, running every submitted agent against every other
  on Snowflake.

They differ in scale and backend, not behaviour, so both come through
:func:`run_season`.

**Persistence is the point, not a side effect.** A season is only worth
running headlessly if you can look at it afterwards, so this writes
through the same store the live server reads — which means a durable
backend (``file`` or ``snowflake``) produces a season that opens in the
normal UI, complete with the per-turn agent rows the AGENT tab fills
from. On ``memory`` the season evaporates when the process exits, which
is fine for a test and useless for a review.

**Cards are kept for every move.** The store already keeps prompt,
reasoning and timing per turn for a model-backed seat. What it has no
column for is the structured extras — the option menu, the plan
directive, the compiler's corrections — so those are written alongside
as Markdown, one file per turn. Between them you can reconstruct any
night in the season without rerunning it.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from sea_of_colours.evals import cards, dispatch

#: Where season records go unless a caller says otherwise.
DEFAULT_ROOT = Path("reports/seasons")

#: Hard ceiling on the turn loop. A season is ``seats x days`` turns
#: plus a resolve per night; anything past a wide multiple of that is a
#: seat that never submits, and an unattended tournament must not hang
#: on one bad fork.
MAX_TURNS = 400


@dataclass
class Turn:
    """One seat's turn, and what it was thinking."""

    day: int
    seat: str
    agent: str
    # A seat plans twice on most days — once in orbit, once for the
    # night — and the two are different decisions with different cards.
    # Collapsing them loses the buying half of the game entirely.
    phase: str = ""
    rationale: str = ""
    card: dict = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""
    fell_back: bool = False
    night_resolved: bool = False


@dataclass
class SeasonResult:
    """One completed (or abandoned) season."""

    season_name: str
    session_id: str
    seed: int
    backend: str
    seats: dict = field(default_factory=dict)
    turns: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    days_played: int = 0
    season_day_cap: int = 0
    seconds: float = 0.0
    aborted: str = ""

    @property
    def winner(self) -> str:
        """The top-scoring seat, or "" if nobody scored or it was a tie."""
        if not self.scores:
            return ""
        ranked = sorted(self.scores.items(), key=lambda kv: -kv[1])
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return ""
        return ranked[0][0]

    @property
    def winning_agent(self) -> str:
        return self.seats.get(self.winner, "")

    @property
    def fallback_turns(self) -> int:
        """Turns where a model-backed seat played its safety net instead."""
        return sum(1 for t in self.turns if t.fell_back)

    def turns_for(self, seat: str) -> list:
        return [t for t in self.turns if t.seat == seat]


def _fell_back(env: Mapping[str, Any] | None, rationale: str) -> bool:
    """Did the harness play its safety net rather than its own plan?

    Same check the battles runner makes, and for the same reason: a
    season scored against a fork that never reached its model is a
    season measuring the built-in heuristic.
    """
    for key in ("fallback", "used_fallback", "is_fallback"):
        val = (env or {}).get(key)
        if isinstance(val, bool):
            return val
    text = (rationale or "").lower()
    return "fallback=true" in text or "plan=[fallback]" in text


def run_season(
    seats: Mapping[str, str],
    *,
    seed: int = 42,
    width: int = 40,
    height: int = 28,
    days: int | None = None,
    season_name: str | None = None,
    store: Any = None,
    on_turn: Callable[[Turn], None] | None = None,
    on_night: Callable[[int], None] | None = None,
) -> SeasonResult:
    """Play one season to completion.

    ``seats`` maps seat id to agent label, e.g.
    ``{"p1": "redwatch_reaper", "p2": "red_harvest"}``. Seat order is
    the map's order, which is also turn order.
    """
    from sea_of_colours.game.session import SEASON_DAY_CAP
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import engine as soc_engine

    if not seats:
        raise ValueError("a season needs at least one seat")
    # Resolved up front so a typo fails before a long run rather than
    # silently seating the heuristic and producing a plausible lie.
    for seat, agent in seats.items():
        dispatch.resolve(agent)

    store = store if store is not None else soc_backend.get_store()
    order: list[str] = list(seats)

    info = soc_engine.init_session(
        store,
        seed=seed,
        width=width,
        height=height,
        season_name=season_name,
        season_day_cap=days,
        players=order,
        agents={s: dispatch.strategy_slug(a) for s, a in seats.items()},
        # Named seats, not Latin house names. A season is reviewed after
        # the fact and often by someone who did not run it, so the
        # scoreboard has to say which agent held which seat.
        player_profiles=dispatch.seat_profiles(seats, seed=seed),
    )
    result = SeasonResult(
        season_name=info.get("season_name") or season_name or "(unnamed)",
        session_id=info["session_id"],
        seed=seed,
        backend=soc_backend.resolution().name,
        seats=dict(seats),
        season_day_cap=int(info.get("season_day_cap") or SEASON_DAY_CAP),
    )
    sid = result.session_id
    started = time.time()

    for _ in range(MAX_TURNS):
        status = soc_engine.get_session_status(store, sid)
        if status.get("phase") == "season_complete":
            break

        pending = status.get("pending") or {}
        seat = next((s for s in order if not pending.get(s, False)), None)
        if seat is None:
            # Everyone submitted but the phase did not move. Resolve it
            # rather than spin: the engine advances on its own in the
            # server, but nothing is driving it here.
            soc_engine.run_night(store, sid)
            continue

        turn = _play_turn(
            store, sid, seat, seats[seat], soc_engine,
            phase=str(status.get("phase") or ""),
        )
        result.turns.append(turn)
        if on_turn is not None:
            on_turn(turn)
        if turn.night_resolved and on_night is not None:
            on_night(turn.day)
    else:
        result.aborted = (
            f"gave up after {MAX_TURNS} turns — a seat is not submitting"
        )

    view = soc_engine.get_view(store, sid, order[0])
    result.days_played = max(0, int(view.get("day", 1)) - 1)
    result.scores = _read_scores(view, order)
    result.seconds = round(time.time() - started, 1)
    return result


def _play_turn(
    store, sid: str, seat: str, agent: str, soc_engine, *, phase: str = "",
) -> Turn:
    started = time.time()
    turn = Turn(day=0, seat=seat, agent=agent, phase=phase)
    env: Mapping[str, Any] = {}
    try:
        env = dispatch.play_turn(store, sid, seat, agent) or {}
        turn.rationale = str(
            env.get("rationale") or env.get("agent_rationale") or ""
        )
        turn.fell_back = _fell_back(env, turn.rationale)
        turn.night_resolved = bool(env.get("night_resolved"))
    except Exception as exc:
        # One seat blowing up must not cost the season. The turn is
        # recorded as failed and the loop moves on; the engine will
        # resolve the night without it.
        turn.error = f"{type(exc).__name__}: {exc}"
        turn.rationale = traceback.format_exc(limit=6)

    post = soc_engine.get_session_status(store, sid)
    post_day = int(post.get("day", 1))
    # When the night resolves the engine has already rolled the
    # calendar, so the turn just played belonged to the previous day.
    turn.day = post_day - 1 if turn.night_resolved else post_day

    turn.card = cards.normalise(
        envelope=env, session_id=sid, day=turn.day, player=seat, agent=agent,
        phase=phase,
    )
    turn.seconds = round(time.time() - started, 2)
    return turn


def _read_scores(view: Mapping[str, Any], order: Sequence[str]) -> dict:
    """Final score per seat, from whichever shape the view offers."""
    scores: dict[str, int] = {}
    board = view.get("scoreboard") or view.get("scores") or {}
    if isinstance(board, Mapping):
        for seat in order:
            val = board.get(seat)
            if isinstance(val, Mapping):
                val = val.get("score")
            if isinstance(val, (int, float)):
                scores[seat] = int(val)
    elif isinstance(board, list):
        for row in board:
            if not isinstance(row, Mapping):
                continue
            seat = str(row.get("player") or row.get("seat") or "")
            val = row.get("score")
            if seat and isinstance(val, (int, float)):
                scores[seat] = int(val)
    return scores


# ── writing a season down ─────────────────────────────────────────


def write_record(
    result: SeasonResult, root: Path | str = DEFAULT_ROOT,
) -> Path:
    """Write the season's cards and a summary next to each other.

    Markdown, one file per turn, plus an ``all-cards.md`` for reading
    the whole season in one pass and a ``season.json`` for tooling. The
    board itself is not written here — it is already in the store, which
    is what the replay UI reads.

    Also an ``all-cards.html``: the same cards with a day rail and
    section tabs, because the Markdown is what you feed to a model and
    the HTML is what you read yourself. Both come from the same
    normalised cards, so they cannot disagree.
    """
    import json

    safe = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in result.season_name
    )
    out = Path(root) / f"{safe}_{result.session_id[:8]}"
    (out / "cards").mkdir(parents=True, exist_ok=True)

    for turn in result.turns:
        card = dict(turn.card)
        card["season"] = result.season_name
        card["moves"] = card.get("moves") or []
        (out / "cards" / cards.filename(card)).write_text(
            cards.render(card), encoding="utf-8",
        )

    all_cards = [dict(t.card, season=result.season_name) for t in result.turns]
    (out / "all-cards.md").write_text(
        cards.render_many(
            all_cards, title=f"{result.season_name} — every turn",
        ),
        encoding="utf-8",
    )
    (out / "all-cards.html").write_text(
        cards.render_html(all_cards, title=result.season_name),
        encoding="utf-8",
    )

    (out / "season.json").write_text(
        json.dumps({
            "season_name": result.season_name,
            "session_id": result.session_id,
            "seed": result.seed,
            "backend": result.backend,
            "seats": result.seats,
            "scores": result.scores,
            "winner": result.winner,
            "winning_agent": result.winning_agent,
            "days_played": result.days_played,
            "season_day_cap": result.season_day_cap,
            "seconds": result.seconds,
            "aborted": result.aborted,
            "fallback_turns": result.fallback_turns,
            "turns": [
                {
                    "day": t.day, "seat": t.seat, "agent": t.agent,
                    "rationale": t.rationale, "seconds": t.seconds,
                    "error": t.error, "fell_back": t.fell_back,
                }
                for t in result.turns
            ],
        }, indent=1, default=str),
        encoding="utf-8",
    )
    return out
