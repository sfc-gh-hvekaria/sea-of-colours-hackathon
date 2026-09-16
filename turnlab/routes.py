"""Every lab HTTP route, in one router the server includes in one line.

These used to be spliced into ``server/app.py``. Moving them here is not
tidiness for its own sake: the lab is a side experiment that clones real
games, and having its routes interleaved with the game's own made it far
too easy for one to reach for the other's store by habit. Here there is
exactly one store in scope — the lab's — and reaching the real one takes
an import somebody would have to justify.

Two of these routes exist only because the game UI lost something.
``/api/lab/open`` hands a frozen turn to the ordinary interface, and
``/api/lab/invoke`` is the per-seat agent invocation that live play
dropped in v1.1. The rest are the launcher's supporting cast.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from . import store as lab_store

router = APIRouter()

_STATIC = pathlib.Path(__file__).resolve().parent / "static"
_PAGE = _STATIC / "lab.html"
_DIFF_PAGE = _STATIC / "diff.html"
_DIFF_JS = _STATIC / "diff.js"

# Edits to the page must show up on a refresh, like the rest of the UI.
_NO_CACHE = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


@router.get("/lab/", include_in_schema=False)
@router.get("/lab", include_in_schema=False)
def lab_page() -> Response:
    if not _PAGE.is_file():
        raise HTTPException(status_code=404, detail="the lab page is missing")
    return Response(
        content=_PAGE.read_text(encoding="utf-8"),
        media_type="text/html; charset=utf-8",
        headers=_NO_CACHE,
    )


@router.get("/lab/diff.js", include_in_schema=False)
@router.get("/lab/diff", include_in_schema=False)
def lab_diff_page(request: Request) -> Response:
    """The divergence view, and its script.

    Two files rather than one because the script is long enough that
    inlining it would make the page unreadable in an editor. The page
    itself is inert on arrival — whoever opens it writes the two takes
    onto ``window.__LAB_DIFF__`` first, so nothing here fetches.
    """
    js = request.url.path.endswith(".js")
    path = _DIFF_JS if js else _DIFF_PAGE
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{path.name} is missing")
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type=(
            "application/javascript; charset=utf-8" if js
            else "text/html; charset=utf-8"
        ),
        headers=_NO_CACHE,
    )


@router.get("/api/lab/boards")
def api_lab_boards() -> dict[str, Any]:
    """Every frozen board in the lab's own store.

    Discovered rather than declared, so minting a new set makes them
    appear with no code change.
    """
    from . import boards as lab_boards

    return {"boards": [b.as_dict() for b in lab_boards.discover(lab_store.store())]}


@router.get("/api/lab/board")
def api_lab_board(
    board: str = Query(..., description="Lab board id."),
) -> dict[str, Any]:
    """The opening board, drawn by the engine's own view builder.

    Not a bespoke render and not a replay frame: the same ``get_view``
    the live game calls, so what the page shows is what the agent will
    be shown.
    """
    from sea_of_colours.snowpark import engine as soc_engine

    from . import boards as lab_boards

    store = lab_store.store()
    try:
        brd = lab_boards.get(board, store)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        seat_view = soc_engine.get_view(store, brd.id, brd.seat)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=f"cannot open board {board}: {type(exc).__name__}: {exc}",
        ) from exc

    return {"board": brd.as_dict(), "view": seat_view}


@router.get("/api/lab/agents")
def api_lab_agents() -> dict[str, Any]:
    """Who can be cast — discovered, so a new fork appears by itself."""
    from . import cast as lab_cast

    entries, problems = lab_cast.roster()
    return {
        "agents": [
            {
                "value": e.label,
                "label": e.display,
                "needs_llm": e.needs_llm,
                "baseline": e.baseline,
                "team": e.team,
                "participants": list(e.participants),
            }
            for e in entries
        ],
        "problems": problems,
    }


@router.get("/api/lab/baseline")
def api_lab_baseline(board: str = Query(""), seat: str = Query("p1")) -> dict[str, Any]:
    """Stock V12's frozen take on this board — the thing a fork is diffed against.

    Served from disk rather than recomputed. V12 is an LLM, so asking it
    again would give a different answer and the comparison would be
    against a moving target rather than against what you forked.
    """
    from . import baseline as lab_baseline

    take = lab_baseline.load(board, seat)
    if take is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no V12 baseline recorded for {board or '(none)'} {seat}. "
                f"Record one with: python -m turnlab.baseline"
            ),
        )
    return take


@router.get("/api/lab/racks")
def api_lab_racks(board: str = "") -> dict[str, Any]:
    """The ordnance a seat can be handed when a board is opened.

    ``board`` is optional and worth passing: with it, each rack says
    whether that particular frozen turn can hold it. A season frozen
    before SNAP existed cannot (v1.36), and the picker should say so
    rather than offer a choice ``arm()`` will refuse.
    """
    from . import arms as lab_arms

    blob = None
    if board:
        row = lab_store.store().load_session(board) or {}
        raw = row.get("json_state")
        while isinstance(raw, (str, bytes)):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = None
                break
        blob = raw if isinstance(raw, dict) else None
    return {"racks": lab_arms.catalogue(blob), "default": lab_arms.DEFAULT}


@router.post("/api/lab/open")
def api_lab_open(payload: dict[str, Any]) -> dict[str, Any]:
    """Open a frozen turn in the ordinary game UI.

    Returns the url to send the browser to. From there it is the real
    interface on a real session — the board, the fog toggles, the hour
    transport, the replay — sitting on a night that already happened,
    with nobody's orders in yet.
    """
    from . import arms as lab_arms
    from . import boards as lab_boards
    from . import turn as lab_turn

    board_id = str(payload.get("board") or "")
    if not board_id:
        raise HTTPException(status_code=400, detail="board is required")

    store = lab_store.store()
    try:
        brd = lab_boards.get(board_id, store)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # {seat: rack_id}. Applied to the clone, so the frozen board is the
    # same board whether you open it armed or empty.
    raw_arms = payload.get("arms")
    arms = (
        {str(k): str(v) for k, v in raw_arms.items()}
        if isinstance(raw_arms, dict)
        else {}
    )
    try:
        for rack_id in arms.values():
            lab_arms.get(rack_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    opened = lab_turn.open_board(brd.id, arms=arms, store=store)
    seat = str(payload.get("seat") or brd.seat)
    opened["url"] = play_url(opened["run_id"], seat, brd.id, opened.get("arms"))
    return opened


def play_url(
    run_id: str,
    seat: str,
    board_id: str,
    arms: Mapping[str, str] | None = None,
) -> str:
    """Where to send the browser to play a freshly opened turn.

    The racks ride along in the address rather than being looked up,
    because RESET rebuilds the turn from this URL alone. A reset that
    quietly disarmed the seats would be a different night wearing the
    same name — and the difference would show up as a mysterious change
    in the agent's plan rather than as an error.
    """
    url = f"/?session={run_id}&player={seat}&lab={board_id}"
    if arms:
        url += "&arms=" + ",".join(f"{s}:{r}" for s, r in sorted(arms.items()))
    return url


def _lab_session(payload: Mapping[str, Any]) -> tuple[Any, str]:
    """The store and session id for a lab request, or an HTTP error.

    The check is on the session ID, because that is the one field the
    engine does not rewrite. The obvious alternative — the ``REPLAY:``
    season name a clone is stamped with — is rebuilt from the state blob
    on the first save, so it survived exactly one invocation and then
    refused the second.
    """
    session_id = str(payload.get("session") or "")
    if not session_id:
        raise HTTPException(status_code=400, detail="session is required")
    if not lab_store.is_run(session_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "playing an agent is a lab-session privilege; this is a "
                "real game and the lab does not play in those"
            ),
        )
    store = lab_store.store()
    if not store.load_session(session_id):
        raise HTTPException(status_code=404, detail=f"no session {session_id}")
    return store, session_id


@router.post("/api/lab/plan")
def api_lab_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Ask an agent what it would do on this seat — and commit nothing.

    This is the control the game UI lost in v1.1, when live-play
    invocation moved to the CLI, with the read-only manners of the V12
    advisor: the plan comes back to be looked at, and the human decides
    whether it stands. ``/api/lab/commit`` is the other half.

    Restoring this for real games would mean anyone with a link could
    hand somebody's seat to a model mid-season, so it is offered only
    for throwaway clones.
    """
    from . import turn as lab_turn

    store, session_id = _lab_session(payload)
    seat = str(payload.get("seat") or "")
    agent = str(payload.get("agent") or "")
    if not (seat and agent):
        raise HTTPException(status_code=400, detail="seat and agent are required")

    try:
        out = lab_turn.plan_only(session_id, seat, agent, store=store)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"{type(exc).__name__}: {exc}"
        ) from exc

    out["session"] = session_id
    return out


@router.post("/api/lab/card")
def api_lab_card(payload: dict[str, Any]) -> dict[str, Any]:
    """Render a lab take as the same agent card a season produces.

    Deliberately the shared renderer in :mod:`sea_of_colours.evals.cards`
    rather than anything the lab owns. A card is the artefact people
    actually pass around — into a coding agent, into a team chat, into
    a bug report — and one that came out of the lab looking different
    from one that came out of a season would be read as a different
    kind of evidence. It is not. It is the same turn.

    The take is posted back rather than looked up because a plan is
    never stored: it is offered, and most are thrown away. Shaping it as
    a database row on the way in means the renderer takes the same path
    it takes for a finished season, including recovering the option menu
    out of the prompt.
    """
    from sea_of_colours.evals import cards

    take = payload.get("take")
    if not isinstance(take, dict):
        raise HTTPException(status_code=400, detail="a take is required")

    prompts = take.get("prompts") if isinstance(take.get("prompts"), dict) else {}
    thinking = take.get("thinking") if isinstance(take.get("thinking"), dict) else {}
    row = {
        "session_id": str(take.get("session") or payload.get("session") or ""),
        "day": take.get("day"),
        "player": str(take.get("seat") or ""),
        "agent_id": str(take.get("agent") or ""),
        "prompt_excerpt": prompts.get("think") or prompts.get("plan") or "",
        "rationale": take.get("rationale") or "",
        "response_text": thinking.get("reasoning") or "",
        "ms_elapsed": take.get("elapsed_ms"),
        "status": "ok",
    }
    card = cards.normalise(
        row,
        season=str(payload.get("board") or "turn lab"),
        agent=row["agent_id"],
        player=row["player"],
    )
    card["moves"] = take.get("moves") or []

    fmt = str(payload.get("format") or "md").lower()
    if fmt == "html":
        body = cards.render_html(
            [card],
            title=f"{row['agent_id']} · {row['player']} · day {row['day']}",
        )
    else:
        body = cards.render(card)
    return {
        "ok": True,
        "format": fmt,
        "filename": cards.filename(card, "html" if fmt == "html" else "md"),
        "body": body,
    }


@router.post("/api/lab/commit")
def api_lab_commit(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept a plan: submit those exact moves for that seat.

    No agent runs here. What lands is what was on screen — which is the
    only way an ACCEPT button can mean anything, since asking the agent
    again could return something else.

    The night is not forced. Submitting the last outstanding seat
    resolves it, exactly as a live game does.
    """
    from . import turn as lab_turn

    store, session_id = _lab_session(payload)
    seat = str(payload.get("seat") or "")
    moves = payload.get("moves")
    if not seat or not isinstance(moves, list):
        raise HTTPException(
            status_code=400, detail="seat and a list of moves are required"
        )

    try:
        out = lab_turn.commit(session_id, seat, moves, store=store)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"{type(exc).__name__}: {exc}"
        ) from exc

    out["session"] = session_id
    return out


@router.post("/api/lab/settle")
def api_lab_settle(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the orbit the frozen night opened, and stop at the next vespera.

    Separate from ``/commit`` rather than folded into it because the
    night and its settlement are two things a person watches: the
    cinematic plays the night, and only when it has finished does the
    board want to jump to the morning's numbers.
    """
    from . import turn as lab_turn

    store, session_id = _lab_session(payload)
    try:
        out = lab_turn.settle(session_id, store=store)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"{type(exc).__name__}: {exc}"
        ) from exc

    out["session"] = session_id
    return out
