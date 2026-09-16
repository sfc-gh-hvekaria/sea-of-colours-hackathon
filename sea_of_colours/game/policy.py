"""Player policy schema for the orchestrator.

A *policy* is an ordered list of moves. You may submit up to
``MAX_QUEUE_LEN`` entries — and you can throw anything in there: malformed
JSON, syntactically valid but semantically illegal moves, etc. The
simulator processes the queue in order, **skipping invalid items**
(they're surfaced as yellow error lines in the log next to the attempted
action) and applying at most :data:`MAX_MOVES` *valid* moves per player
per night. Invalid moves no longer burn a tick — see
:class:`sea_of_colours.game.simulator.NightSimulator`.

Move tags::

    probe    {"a": "probe",  "at": [x, y]}
    drop     {"a": "drop",   "unit": <harvester_id>, "at": [x, y]}
    step     {"a": "step",   "unit": <harvester_id>, "to": [x, y]}
    pickup   {"a": "pickup", "unit": <harvester_id>}

The legacy ``deploy_probe`` / per-entity object schema is gone. Lifters are
implied: drop / pickup target a harvester directly and the owning player's
orblift is looked up at execution time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple, Union


MAX_MOVES: int = 21
"""Per-player per-night cap on *applied* (valid) policy actions across the
whole fleet (drop / step / pickup / probe all count as one action each).

The number is sized for 3 harvesters at full tilt:
``3 × (drop + 5 step + pickup) = 3 × 7 = 21``. So a player who manages a
3-harvester wing has no headroom for extra probes that night — choosing
between probes and a richer harvest pattern is a real trade-off. A
player with one or two harvesters has plenty of slots left for probes.

NOTE: this is the *fleet-wide* policy budget, NOT the per-harvester
step limit. Each harvester is independently capped at 5 steps per
night (RULEBOOK §3.x). The simulator enforces both: it skips invalid
items without burning a tick, then applies up to MAX_MOVES *valid*
moves until the queue is empty or the cap is hit."""

MAX_QUEUE_LEN: int = 100
"""Hard cap on raw queue length the orchestrator will accept per player.

Generous on purpose — the rules let players "log whatever you want into
your policy" — but a finite ceiling so a runaway agent can't blow up the
server. Items beyond this index are silently dropped at parse time."""

# NOTE (v0.6.0): the historical ``HARVEST_CAP`` (5 RED→GREEN conversions
# per player per night) was removed when harvesting was extended to all
# colours (§3.12). The natural limit is now the harvester's 6-parcel hold
# capacity (drop + 5 steps), so a per-night colour cap is redundant. A
# back-compat sentinel is preserved below for legacy importers that
# reach for the name; setting it to ``MAX_QUEUE_LEN`` effectively
# disables the cap without making downstream branches crash.
HARVEST_CAP: int = MAX_QUEUE_LEN
"""DEPRECATED (v0.6.0): no per-color harvest cap. Held at MAX_QUEUE_LEN
for back-compat with v0.5 tests that import the name."""


MoveTag = Literal[
    "probe",
    "drop",
    "step",
    "pickup",
    "wait",
    "emp_launch",
    "chaff_flare",
    "snap_launch",
    "waste",
]


# v1.31 — night tags the engine used to accept. Mirrors
# ``_RETIRED_ORBIT_TAGS`` below, added when the caltrop MINE was retired:
# persisted queues, archived replay frames and stale LLM replies still
# carry ``mine_lay``, and a named refusal is worth far more than the
# generic "unknown action" shrug — especially to an agent, which can only
# stop asking for a thing if it is told why the thing failed.
#
# v1.36 — the "here is what you CAN do instead" half of the message is
# now derived from the price table rather than typed out. The old text
# read "EMP and chaff are the remaining weapons", which was true for
# five versions and then quietly was not: a refusal that names a stale
# roster is worse than one that names none, because the agent it is
# talking to has no other source for that list.
def _live_weapons_phrase(singular: str, plural: str) -> str:
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND

    names = sorted(k.upper() for k in BLUE_COST_BY_KIND)
    if not names:
        return "nothing else ships in its place"
    if len(names) == 1:
        return f"{names[0]} {singular}"
    return f"{', '.join(names[:-1])} and {names[-1]} {plural}"


_RETIRED_MOVE_TAGS: Dict[str, str] = {
    "mine_lay": (
        "the caltrop mine was retired in v1.31 — "
        + _live_weapons_phrase(
            "is the remaining weapon", "are the remaining weapons"
        )
    ),
}


@dataclass(frozen=True)
class ProbeMove:
    at: Tuple[int, int]
    tag: MoveTag = "probe"


@dataclass(frozen=True)
class DropMove:
    unit: str
    at: Tuple[int, int]
    tag: MoveTag = "drop"


@dataclass(frozen=True)
class StepMove:
    unit: str
    to: Tuple[int, int]
    tag: MoveTag = "step"


@dataclass(frozen=True)
class PickupMove:
    unit: str
    tag: MoveTag = "pickup"


@dataclass(frozen=True)
class WaitMove:
    """A no-op that consumes one of the 21 hour slots without acting.

    The bedrock of v0.9 hour-scheduling: a player who wants to drop at
    hour 10 pads their queue with 9 ``WaitMove``s first. WAITs also
    surface during EMP / chaff resolution as the engine-substituted
    action for a disabled / chaffed seat — the simulator tags those
    frames distinctly (``empd`` / ``chaffed``) so the watcher can tell
    a voluntary wait from a forced one.
    """

    tag: MoveTag = "wait"


@dataclass(frozen=True)
class EmpLaunchMove:
    """Fire an orbital EMP salvo (RULEBOOK §5, v0.9).

    One launch fires ``EMP_MISSILES_PER_LAUNCH`` simultaneous missiles
    for one stock/cost. ``at`` is the primary target; ``extra_ats``
    holds the rest of the salvo. Each target spawns its own Manhattan
    radius-``EMP_RADIUS`` cloud for ``EMP_CLOUD_HOURS`` of the SAME
    night. Any harvester sitting in a cloud cell at the start of a
    subsequent hour gets its action replaced by a WAIT (logged as
    ``tag="empd"``); any PROBE/MINE caught in the blast is destroyed.
    Friendly fire is on. Open action: every seat sees the launch + cloud.
    """

    at: Tuple[int, int]
    extra_ats: Tuple[Tuple[int, int], ...] = ()
    tag: MoveTag = "emp_launch"

    @property
    def ats(self) -> Tuple[Tuple[int, int], ...]:
        """Full salvo (primary target first)."""
        return (self.at,) + tuple(self.extra_ats)


@dataclass(frozen=True)
class ChaffFlareMove:
    """Fire an orbital chaff flare (RULEBOOK §5, v0.9).

    Cost debited on apply. At the hour this move occupies (and for
    ``CHAFF_DURATION_HOURS - 1`` subsequent hours) every OTHER seat's
    hour-N action is cancelled (replay tag ``"chaffed"``). The
    triggerer's chaff itself proceeds; the triggerer's *later* hours
    are NOT affected. Open action.
    """

    tag: MoveTag = "chaff_flare"


@dataclass(frozen=True)
class SnapLaunchMove:
    """Fire a SNAP at one cell (RULEBOOK §4.9.4, v1.36).

    One missile, one square, a cloud that lasts ``SNAP_CLOUD_HOURS``.
    Any PROBE on the cell is destroyed and any HARVESTER on it is
    damaged — including one that arrives during the hour, which is the
    half that lets a SNAP guard a square rather than merely punish one.

    The reason it is its own move rather than an EMP with a radius of
    zero is ordering, not shape. SNAP resolves above the hour-start
    vision snapshot and an EMP resolves below it (see
    ``NightSimulator._snap_preempt_phase``), so a SNAP can deny the drop
    its target beacon was validating and an EMP never can. Two weapons
    that differ only in which side of one line they sit on still have
    to be two weapons.
    """

    at: Tuple[int, int]
    tag: MoveTag = "snap_launch"


@dataclass(frozen=True)
class WasteMove:
    """A queue slot the parser kept because it was structurally bad.

    Carries the original payload + a human reason for replay captions.
    """

    reason: str
    raw: Any = None
    tag: MoveTag = "waste"


Move = Union[
    ProbeMove,
    DropMove,
    StepMove,
    PickupMove,
    WaitMove,
    EmpLaunchMove,
    ChaffFlareMove,
    SnapLaunchMove,
    WasteMove,
]


def _pair(obj: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(obj, list) or len(obj) != 2:
        return None
    try:
        return int(obj[0]), int(obj[1])
    except (TypeError, ValueError):
        return None


def _parse_one(raw: Any) -> Move:
    if not isinstance(raw, dict):
        return WasteMove(reason="move must be an object", raw=raw)
    action = raw.get("a") or raw.get("action")
    if not isinstance(action, str):
        return WasteMove(reason="move missing 'a' field", raw=raw)
    action = action.lower()

    if action == "probe":
        xy = _pair(raw.get("at"))
        if xy is None:
            return WasteMove(reason="probe.at must be [x,y]", raw=raw)
        return ProbeMove(at=xy)

    if action == "drop":
        unit = raw.get("unit")
        xy = _pair(raw.get("at"))
        if not isinstance(unit, str) or not unit:
            return WasteMove(reason="drop.unit missing", raw=raw)
        if xy is None:
            return WasteMove(reason="drop.at must be [x,y]", raw=raw)
        return DropMove(unit=unit, at=xy)

    if action == "step":
        unit = raw.get("unit")
        to = _pair(raw.get("to"))
        if not isinstance(unit, str) or not unit:
            return WasteMove(reason="step.unit missing", raw=raw)
        if to is None:
            return WasteMove(reason="step.to must be [x,y]", raw=raw)
        return StepMove(unit=unit, to=to)

    if action == "pickup":
        unit = raw.get("unit")
        if not isinstance(unit, str) or not unit:
            return WasteMove(reason="pickup.unit missing", raw=raw)
        return PickupMove(unit=unit)

    if action == "wait":
        # No payload required. The hour-stamp the simulator assigns
        # is implicit (slot index). Extra fields in ``raw`` are
        # silently ignored so a frontend that adds ``{"a": "wait",
        # "for_hour": 10}`` for debugging round-trips cleanly.
        return WaitMove()

    if action in ("emp", "emp_launch", "emp-launch"):
        raw_at = raw.get("at")
        # Accept either a single ``[x,y]`` or a salvo ``[[x,y], ...]``.
        targets: List[Tuple[int, int]] = []
        if (
            isinstance(raw_at, list)
            and raw_at
            and all(isinstance(t, list) for t in raw_at)
        ):
            for t in raw_at:
                p = _pair(t)
                if p is not None:
                    targets.append(p)
        else:
            p = _pair(raw_at)
            if p is not None:
                targets.append(p)
        if not targets:
            return WasteMove(
                reason="emp_launch.at must be [x,y] or [[x,y], ...]",
                raw=raw,
            )
        return EmpLaunchMove(at=targets[0], extra_ats=tuple(targets[1:]))

    if action in ("chaff", "chaff_flare", "chaff-flare"):
        return ChaffFlareMove()

    # v1.36 — SNAP takes a single cell, never a list. Accepting one is
    # the difference between the weapon and the salvo, so a payload that
    # offers several is refused by name rather than silently taking the
    # first: an agent that thinks it bought a spread should find out.
    if action in ("snap", "snap_launch", "snap-launch"):
        pt = _pair(raw.get("at"))
        if pt is None:
            return WasteMove(
                reason="snap_launch.at must be [x,y] — SNAP hits one cell",
                raw=raw,
            )
        return SnapLaunchMove(at=pt)

    # v1.31 — retired night moves. Normalise the punctuation variants the
    # old parser accepted so a stale queue gets the real reason back.
    _canon = action.replace("-", "_")
    if _canon == "mine":
        _canon = "mine_lay"
    if _canon in _RETIRED_MOVE_TAGS:
        return WasteMove(reason=_RETIRED_MOVE_TAGS[_canon], raw=raw)

    return WasteMove(reason=f"unknown action '{action}'", raw=raw)


def parse_moves(payload: Any) -> Tuple[List[Move], List[str]]:
    """Return ``(moves, hard_errors)``.

    ``hard_errors`` is only populated when the payload itself is malformed
    (not a list / dict, etc.). Per-entry parse failures become
    :class:`WasteMove` markers — the simulator surfaces them as yellow
    error lines but they no longer consume a tick (RULEBOOK §3.10).

    Queue length is capped at :data:`MAX_QUEUE_LEN`; entries beyond that
    are silently dropped at parse time.
    """

    if payload is None:
        return [], []

    if isinstance(payload, dict):
        seq = payload.get("moves")
    else:
        seq = payload

    if seq is None:
        return [], []

    if not isinstance(seq, list):
        return [], ["policy 'moves' must be a list"]

    out: List[Move] = []
    for raw in seq[:MAX_QUEUE_LEN]:
        out.append(_parse_one(raw))
    return out, []


def move_to_wire(m: Move) -> dict:
    """Round-trip a parsed move back to the JSON shape (for echo/debug)."""
    if isinstance(m, ProbeMove):
        return {"a": "probe", "at": list(m.at)}
    if isinstance(m, DropMove):
        return {"a": "drop", "unit": m.unit, "at": list(m.at)}
    if isinstance(m, StepMove):
        return {"a": "step", "unit": m.unit, "to": list(m.to)}
    if isinstance(m, PickupMove):
        return {"a": "pickup", "unit": m.unit}
    if isinstance(m, WaitMove):
        return {"a": "wait"}
    if isinstance(m, EmpLaunchMove):
        if m.extra_ats:
            return {
                "a": "emp_launch",
                "at": [list(t) for t in m.ats],
            }
        return {"a": "emp_launch", "at": list(m.at)}
    if isinstance(m, ChaffFlareMove):
        return {"a": "chaff_flare"}
    if isinstance(m, SnapLaunchMove):
        return {"a": "snap_launch", "at": list(m.at)}
    return {"a": "waste", "reason": m.reason}


def moves_to_wire(moves: Iterable[Move]) -> List[dict]:
    return [move_to_wire(m) for m in moves]


# ── Orbit actions (v0.8.0; simplified v1.13) ─────────────────────────
#
# The Orbit phase runs once per game-day before PRAXIS (RULEBOOK §4).
# A seat declares purchases (harvester / probe / repair) and weapons
# buys (EMP / mine / chaff) — nothing else. The orchestrator parses the
# submission into this discriminated union and the resolver
# (sea_of_colours.game.orbit_resolver.OrbitResolver) walks the validated
# list, then settles RED and GREEN automatically.
#
# v1.13 — there is **no action cap**. Credits and blue are the only
# constraint. The old MAX_ORBIT_ACTIONS=3 limited how many *kinds* of
# thing a seat did per orbit, never the volume of any one of them (every
# build action already carries a ``count``), so it only ever produced
# the "one thing too many" refusal. Refine, ship_catapult and
# solar_jettison were removed in the same pass — see _RETIRED_ORBIT_TAGS.


OrbitTag = Literal[
    "build_harvester",
    "build_probe",
    "build_emp",
    "build_chaff",
    "build_snap",
    "repair",
    "orbit_waste",
]


# v1.13 — tags the engine used to accept. Persisted pre-1.13 sessions and
# archived replay frames still carry them, so they get a named refusal
# rather than falling through to the generic "unknown orbit action"
# branch: a stale client deserves a diagnosis, not a shrug.
_RETIRED_ORBIT_TAGS: Dict[str, str] = {
    "refine": "refining was removed in v1.13 — parcels ship at the tier they were mined",
    "refine_cascade": "the final refinery run was removed in v1.13",
    "ship_catapult": "RED now ships automatically at settlement (v1.13) — no bid needed",
    "solar_jettison": "GREEN is now auto-settled at a flat -100/parcel (v1.13)",
    # v1.31 — the caltrop went with the mechanic, not the buy button.
    "build_mine": (
        "the caltrop mine was retired in v1.31 — build "
        + _live_weapons_phrase("instead", "instead")
    ),
}


@dataclass(frozen=True)
class BuildHarvesterAction:
    """Mint a new harvester in orbit (paid via credits)."""

    tag: OrbitTag = "build_harvester"


@dataclass(frozen=True)
class BuildProbeAction:
    """Top up the seat's probe stock by ``count`` (paid via credits).

    v0.9.1 — accepts an explicit ``count`` so a single Orbit-action
    slot can mint multiple probes. ``count`` defaults to 1 for
    back-compat with v0.8.x callers and persisted sessions; the
    resolver clamps it to ``max(1, count)``. v1.2 — the buy PARTIAL-
    FILLS: it mints as many probes as the seat can afford rather than
    rejecting the whole batch, and logs a red amendment line when the
    order is trimmed to budget (only a batch that can't afford even one
    probe is a hard rejection).
    """

    count: int = 1
    tag: OrbitTag = "build_probe"


@dataclass(frozen=True)
class BuildEmpAction:
    """v0.9.3 — Construct ``count`` EMP warheads in orbit.

    Cost: ``count × (EMP_COST_BLUE_PURITY + EMP_COST_CREDITS)``. The
    resolver refuses the entire batch if the seat can't pay it in
    one go (matches the BuildProbeAction "no partial fill" rule).
    On success, ``count`` is added to ``weapon_stock[seat]["emp"]``
    and the matching ``EmpLaunchMove`` during PRAXIS drains the
    stock instead of debiting resources live.
    """

    count: int = 1
    tag: OrbitTag = "build_emp"


@dataclass(frozen=True)
class BuildSnapAction:
    """v1.36 — Construct ``count`` SNAP rounds in orbit (§4.9.4).

    Cost: ``count × (SNAP_COST_BLUE_PURITY + SNAP_COST_CREDITS)`` —
    100 blue and 250 credits each, the only weapon that costs more
    credits than blue. Same whole-batch-or-nothing rule as the others.
    """

    count: int = 1
    tag: OrbitTag = "build_snap"


@dataclass(frozen=True)
class BuildChaffAction:
    """v0.9.3 — Construct ``count`` orbital chaff flares.

    Cost: ``count × (CHAFF_COST_BLUE_PURITY + CHAFF_COST_CREDITS)``.
    Each ``ChaffFlareMove`` during the night drains one from
    ``weapon_stock[seat]["chaff"]``.
    """

    count: int = 1
    tag: OrbitTag = "build_chaff"


@dataclass(frozen=True)
class RepairAction:
    """Repair a damaged harvester in orbit (paid via credits)."""

    unit: str = ""
    tag: OrbitTag = "repair"


@dataclass(frozen=True)
class OrbitWasteAction:
    """A queue slot the parser kept because it was structurally bad.

    Mirrors :class:`WasteMove` for Orbit submissions — surfaced as a
    yellow log line by the resolver so a seat can see *why* something it
    submitted did nothing.
    """

    reason: str = ""
    raw: Any = None
    tag: OrbitTag = "orbit_waste"


OrbitAction = Union[
    BuildHarvesterAction,
    BuildProbeAction,
    BuildEmpAction,
    BuildChaffAction,
    BuildSnapAction,
    RepairAction,
    OrbitWasteAction,
]


def _parse_orbit_one(raw: Any) -> OrbitAction:
    if not isinstance(raw, dict):
        return OrbitWasteAction(reason="orbit action must be an object", raw=raw)
    action = raw.get("a") or raw.get("action")
    if not isinstance(action, str):
        return OrbitWasteAction(reason="orbit action missing 'a' field", raw=raw)
    a = action.lower()

    if a in ("build_harvester", "build-harvester", "buildharvester"):
        return BuildHarvesterAction()

    if a in ("build_probe", "build-probe", "buildprobe"):
        raw_count = raw.get("count", raw.get("n"))
        try:
            count = int(raw_count) if raw_count is not None else 1
        except (TypeError, ValueError):
            count = 1
        return BuildProbeAction(count=max(1, count))

    # v0.9.3 — Build interdiction weapons (RULEBOOK §5.0). Same
    # batch-count shape as ``build_probe``: missing/invalid counts
    # clamp to 1, single-pay-or-bust at the resolver.
    if a in ("build_emp", "build-emp", "buildemp"):
        raw_count = raw.get("count", raw.get("n"))
        try:
            count = int(raw_count) if raw_count is not None else 1
        except (TypeError, ValueError):
            count = 1
        return BuildEmpAction(count=max(1, count))

    if a in ("build_chaff", "build-chaff", "buildchaff"):
        raw_count = raw.get("count", raw.get("n"))
        try:
            count = int(raw_count) if raw_count is not None else 1
        except (TypeError, ValueError):
            count = 1
        return BuildChaffAction(count=max(1, count))

    if a in ("build_snap", "build-snap", "buildsnap"):
        raw_count = raw.get("count", raw.get("n"))
        try:
            count = int(raw_count) if raw_count is not None else 1
        except (TypeError, ValueError):
            count = 1
        return BuildSnapAction(count=max(1, count))

    if a == "repair":
        unit = raw.get("unit")
        if not isinstance(unit, str) or not unit:
            return OrbitWasteAction(reason="repair.unit missing", raw=raw)
        return RepairAction(unit=unit)

    # v1.13 — retired mechanics. Normalise the punctuation variants the
    # old parser accepted so a stale client gets the real reason back.
    _canon = a.replace("-", "_")
    if _canon in ("refinery", "refine_all_up"):
        _canon = "refine_cascade"
    if _canon in ("shipcatapult",):
        _canon = "ship_catapult"
    if _canon in ("jettison", "green_catapult"):
        _canon = "solar_jettison"
    if _canon in ("buildmine", "build_caltrop", "mine"):
        _canon = "build_mine"
    if _canon in _RETIRED_ORBIT_TAGS:
        return OrbitWasteAction(reason=_RETIRED_ORBIT_TAGS[_canon], raw=raw)

    return OrbitWasteAction(reason=f"unknown orbit action '{action}'", raw=raw)


def parse_orbit_actions(
    payload: Any,
    *,
    max_actions: Optional[int] = None,
) -> Tuple[List[OrbitAction], List[str]]:
    """Return ``(actions, hard_errors)`` from a raw Orbit submission.

    Accepts either ``{"actions": [...]}`` or a bare list. Per-item parse
    failures become :class:`OrbitWasteAction` markers and are surfaced by
    the resolver as yellow log lines rather than failing the submission.

    v1.13 — there is no action cap; ``max_actions`` defaults to ``None``
    (no truncation) and is kept only so persisted callers that still pass
    it keep working. Credits and blue are the only constraint now.
    """
    if payload is None:
        return [], []
    if isinstance(payload, dict):
        seq = payload.get("actions") or payload.get("orbit_actions")
    else:
        seq = payload
    if seq is None:
        return [], []
    if not isinstance(seq, list):
        return [], ["orbit submission 'actions' must be a list"]
    trimmed = seq if max_actions is None else seq[:max_actions]
    out: List[OrbitAction] = []
    for raw in trimmed:
        out.append(_parse_orbit_one(raw))
    return out, []


def orbit_action_to_wire(a: OrbitAction) -> dict:
    if isinstance(a, BuildHarvesterAction):
        return {"a": "build_harvester"}
    if isinstance(a, BuildProbeAction):
        out: dict = {"a": "build_probe"}
        # Only emit the count field when it's a batch — keeps the wire
        # shape clean for the legacy single-probe path and stops the
        # v0.8 prompt-side parser from tripping on an unknown key.
        if int(getattr(a, "count", 1) or 1) > 1:
            out["count"] = int(a.count)
        return out
    # v0.9.3 — symmetric serialiser for the weapon-build actions. Same
    # "skip count when 1" rule as build_probe. (v1.31 — ``build_mine``
    # retired with the caltrop; v1.36 — ``build_snap`` took the slot.)
    if isinstance(a, BuildEmpAction):
        out = {"a": "build_emp"}
        if int(getattr(a, "count", 1) or 1) > 1:
            out["count"] = int(a.count)
        return out
    if isinstance(a, BuildChaffAction):
        out = {"a": "build_chaff"}
        if int(getattr(a, "count", 1) or 1) > 1:
            out["count"] = int(a.count)
        return out
    if isinstance(a, BuildSnapAction):
        out = {"a": "build_snap"}
        if int(getattr(a, "count", 1) or 1) > 1:
            out["count"] = int(a.count)
        return out
    if isinstance(a, RepairAction):
        return {"a": "repair", "unit": a.unit}
    return {"a": "orbit_waste", "reason": getattr(a, "reason", "")}


def orbit_actions_to_wire(actions: Iterable[OrbitAction]) -> List[dict]:
    return [orbit_action_to_wire(a) for a in actions]
