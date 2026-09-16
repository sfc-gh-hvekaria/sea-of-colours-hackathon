"""One place that knows how to make an agent take a turn.

Two runtimes can drive a seat and they take different arguments. The
in-process heuristic lives in :mod:`sea_of_colours.agent.runtime` and is
selected by ``runtime_override``; everything else — the shipped LLM
agent and every attendee fork — goes through
:mod:`sea_of_colours.orchestrator_2.runtime` and is selected by
``agent_label``. Neither accepts the other's vocabulary, and the
heuristic runtime rejects ``red_harvest`` outright even though that is
what the rest of the kit calls it.

That is three small traps, and before this module there was a copy of
the workaround in the battles runner and a different, less complete copy
in each season script — which is why ``run_season.py`` cannot run an
LLM seat at all and says so in its docstring. Anything that wants "let
this agent play this seat" should call :func:`play_turn` and stop
caring.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Labels the offline heuristic runtime serves without credentials.
#: ``human`` is deliberately absent: a human seat has no agent to run.
OFFLINE_LABELS = frozenset({"red_harvest", "red_harvest_lite", "heuristic"})

#: What the heuristic runtime calls its own default. It rejects
#: ``red_harvest``, which is what everything else calls the same thing.
_HEURISTIC_DEFAULT = None

#: Strategy slugs understood by ``init_session``'s ``agents`` map. That
#: map only drives identity — display name, tag, palette — not which
#: runtime plays the turn, so an unknown fork can safely borrow the slug
#: of the thing it is descended from.
_SLUGS = {
    "heuristic": "red_harvest",
    "red_harvest": "red_harvest",
    "red_harvest_lite": "red_harvest_lite",
}


#: Labels that mean the same agent, for naming purposes only.
_DISPLAY_ALIASES = {"heuristic": "red_harvest"}


def normalise(agent: str) -> str:
    return (agent or "").strip().lower()


def is_offline(agent: str) -> bool:
    """Can this agent play with no credentials and no network?"""
    return normalise(agent) in OFFLINE_LABELS


def strategy_slug(agent: str) -> str:
    """The ``init_session`` slug for a seat held by this agent.

    Anything with a model behind it is ``cortex``, which is what the
    engine uses to pick bot naming and colours for an LLM seat.
    """
    return _SLUGS.get(normalise(agent), "cortex")


def known_agents() -> list[str]:
    """Every label a seat can be assigned, heuristics first."""
    from sea_of_colours.orchestrator_2 import binding_registry as br

    known = list(OFFLINE_LABELS)
    known += [
        label
        for label in br.AGENT_LABEL_BINDINGS
        if label not in OFFLINE_LABELS and label != "human"
    ]
    return sorted(set(known), key=lambda a: (a not in OFFLINE_LABELS, a))


def resolve(agent: str) -> str:
    """Check a label is playable, or raise with the list that is.

    Raised rather than defaulted on purpose. A mistyped agent that
    silently falls back to the heuristic produces a complete, plausible
    season that measures the wrong thing, and the tournament path runs
    unattended where nobody would catch it.
    """
    label = normalise(agent)
    if not label:
        raise ValueError("no agent given")
    if label in OFFLINE_LABELS:
        return label

    from sea_of_colours.orchestrator_2 import binding_registry as br

    if label in br.AGENT_LABEL_BINDINGS:
        return label
    raise ValueError(
        f"unknown agent {agent!r}. Known: {', '.join(known_agents())}"
    )


def needs_llm(agent: str) -> bool:
    """Will this seat call out to a model every night?"""
    label = normalise(agent)
    if label in OFFLINE_LABELS:
        return False
    from sea_of_colours.orchestrator_2 import binding_registry as br

    binding = br.AGENT_LABEL_BINDINGS.get(label)
    return bool(getattr(binding, "needs_llm", True))


def display_name(agent: str) -> str:
    """What to call this agent on a scoreboard.

    ``menu_label`` is written for a dropdown — ``"DRYRUN_PILOT — dryrun's
    agent (needs a Snowflake PAT · slow)"`` — so take the part before the
    dash and fall back to the label itself.
    """
    # ``heuristic`` and ``red_harvest`` are two names for one bot; the
    # former has no binding, so without this a seat shows as HEURISTIC
    # in one season and RED_HARVEST in the next depending on how it was
    # spelled on the command line.
    label = _DISPLAY_ALIASES.get(normalise(agent), normalise(agent))
    try:
        from sea_of_colours.orchestrator_2 import binding_registry as br

        menu = getattr(br.AGENT_LABEL_BINDINGS.get(label), "menu_label", "")
    except Exception:
        menu = ""
    head = str(menu or "").split("—")[0].split(" - ")[0].strip()
    return head or label.upper()


def _tag_for(name: str, taken: set[str], seat: str) -> str:
    """Three distinct letters for a seat, as the UI expects.

    Two agents from the same team collide on a naive first-three
    (``RED_HARVEST`` and ``RED_HARVEST_LITE`` are both ``RED``), and a
    scoreboard with two identical tags is worse than an ugly one.
    """
    letters = [c for c in name.upper() if c.isalnum()]
    candidates = ["".join(letters[:3])]
    if len(letters) >= 3:
        # Initials of the underscore-separated parts, then first-two plus
        # a later letter — both stay recognisably derived from the name.
        parts = [p for p in name.upper().replace("-", "_").split("_") if p]
        if len(parts) >= 2:
            candidates.append("".join(p[0] for p in parts)[:3])
        candidates += [
            "".join(letters[:2]) + letters[i] for i in range(2, len(letters))
        ]
    candidates.append(seat.upper()[:3])
    for cand in candidates:
        cand = cand[:3]
        if len(cand) == 3 and cand not in taken:
            return cand
    # Numbered last resort, so this can never return a duplicate.
    for n in range(1, 100):
        cand = f"{(name.upper() + 'XX')[:1]}{n:02d}"
        if cand not in taken:
            return cand
    return seat.upper()[:3]


def seat_profiles(seats: Mapping[str, str], *, seed: int = 0) -> dict:
    """Name every seat after the agent holding it.

    Without this the engine mints Latin house names for bot seats, which
    is good flavour for a game you are playing and useless for a season
    you are reviewing: a scoreboard reading ``AMU`` and ``VLE`` does not
    tell you which one was your fork. Every seat in a headless season is
    an agent, so naming them all is unambiguous.

    Colours are assigned here rather than left to the engine because
    supplying a profile opts a seat out of the engine's palette pass —
    half-supplying would leave named seats on the flat seat defaults
    while unnamed ones kept distinct colours.
    """
    from sea_of_colours.game.session import SEAT_COLOR_PALETTE

    palette = list(SEAT_COLOR_PALETTE)
    profiles: dict[str, dict] = {}
    taken: set[str] = set()
    for index, (seat, agent) in enumerate(seats.items()):
        name = display_name(agent)
        tag = _tag_for(name, taken, seat)
        taken.add(tag)
        profiles[seat] = {
            "display_name": name,
            "tag": tag,
            "color": palette[(index + seed) % len(palette)],
        }
    return profiles


def play_turn(store, session_id: str, seat: str, agent: str) -> Mapping[str, Any]:
    """Hand one turn to whichever runtime owns this agent.

    Returns the harness envelope — the rationale and, for a harness that
    builds one, the ``extras`` that make up the card.
    """
    label = normalise(agent)
    if label in OFFLINE_LABELS:
        from sea_of_colours.agent.runtime import run_agent_turn

        override = (
            _HEURISTIC_DEFAULT
            if label in ("heuristic", "red_harvest")
            else label
        )
        return run_agent_turn(
            store, session_id, seat, runtime_override=override,
        ) or {}

    from sea_of_colours.orchestrator_2.runtime import run_agent_turn

    return run_agent_turn(
        store, session_id, seat, agent_label=label,
    ) or {}
