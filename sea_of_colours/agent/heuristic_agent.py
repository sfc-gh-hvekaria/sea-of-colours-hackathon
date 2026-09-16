"""RED_HARVEST — Sea of Colours' deterministic mainstay agent.

This is the heuristic Python agent the game launches with by default.
It reads the same structured ``SOC_GET_VIEW`` payload that the AI
agents (Snowflake Cortex agents like ``SOC_RED_REAPER``) consume and
emits a policy plan plus a one-paragraph rationale. Keeping a stable,
fully-tested heuristic alongside the AI agents buys two things:

1. The ``[ LET THE AGENT PLAY ]`` button works end-to-end without a
   live Snowflake / Cortex account — essential for the in-memory
   backend used by tests and dev sandboxes. RED_HARVEST always plays.
2. Any AI agent's responses can be sanity-checked against
   RED_HARVEST's plan. If an AI agent disagrees, the orchestrator log
   carries both so a human can adjudicate.

Strategy (v0.9.5 overhaul — competitive against the new orbit/weapons
economy):

* **Orbit phase** — fixed playbook in priority order, gated on
  credits and the 3-action slot cap:

    1. Repair every damaged harvester (one ``repair`` per slot).
    2. Build 2 probes in a single ``build_probe`` batch (1 if the
       seat can only afford one; 0 if broke).
    3. Build a new harvester if the seat is under the fleet cap
       and can afford it.

  With ORBIT_CREDITS_PER_TURN = 1000c and HARVESTER_BUILD_COST =
  1500c, this naturally lands a fresh harvester roughly every
  third turn after the 500c probe spend — meaning RED_HARVEST
  reliably stays at 3/3 harvesters once it ramps.

  v1.x — WEAPONS ECONOMY. BLUE is the weapons currency, so a surplus is
  spent, not hoarded: above 300 blue the playbook ALWAYS builds a weapon
  (chaff first when we hold none, else an EMP); above 250 it keeps the
  older 50% EMP roll. (400 blue is the night-side "flush" mark — see the
  surplus-harvester blue seeking below.)

* **Night phase** — every alive harvester gets a chain:

    * Surface harvesters chain into the nearest unclaimed RED.
    * Orbital harvesters drop onto the best unclaimed RED's
      adjacent cell, then walk-and-harvest.
    * SURPLUS blue seeking (v1.x): when the seat runs 2+ harvesters and
      blue is below the 400 weapon cap, the last healthy unit prefers
      BLUE over low-tier RED — it still grabs any unclaimed mass/pure
      seam, but with none left it farms BLUE to fund EMP/chaff instead of
      chasing a cheap trace/vein blob (2 harvesters → 1 RED + 1 seeking;
      3 → 2 RED + 1 seeking).
    * Surplus harvesters with no visible RED left to claim push
      into fog: drop onto a fog-cluster centroid (or a quarter-
      point on day-1) so their 2-cell vision disk reveals new
      ground for the next turn. Each fog probe lands far enough
      from existing coverage that the move actually expands the
      visible frontier.

  All chains end with a pickup so dawn destruction doesn't claim
  the unit (RULEBOOK §3.11.2). Damaged surface harvesters short-
  circuit to a single ``pickup`` so the next orbit can repair them.

  v1.x — CHAFF egress jam (RULEBOOK §5): when the seat holds chaff, one
  flare is scheduled (by queue position) at a random hour in an egress
  window (5-7 / 11-13) to cancel a rival harvester's pickup and strand
  it for the dawn sweep.

* **Probes** — up to two probe drops per night, in priority order:

    1. **Redsign jackpot** (RULEBOOK §4.11) — if a public redsign marks a
       pure-RED (255) seam we haven't revealed yet, one probe is aimed at
       the (jittered) sign centre to light the seam up so a harvester can
       chain the 3× vein next night.
    2. **RED bracketing** — plant a vision disk on the frontier of
       already-visible RED to scan for sibling veins.
    3. **Blue-sign top-up** (RULEBOOK §4.10) — only when BLUE is low and
       none is visible: one probe toward a blue-sign centre to seed
       tomorrow's low-blue harvest fallback.
    4. **Fog clusters** — the largest unexplored regions (anchored at
       their centroid, NOT the nearest-visible-edge, so each drop
       genuinely pushes the frontier outward), then a quadrant fallback.
"""

from __future__ import annotations

import random as _random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


MAX_HARVESTER_STEPS = 5
"""Per-harvester step cap. Mirrors the simulator's per-unit cap so
the heuristic never emits a chain the simulator would have to
truncate anyway. Drop + 5 steps + pickup = 7 policy actions per
harvester, which fits 3 harvesters under the fleet-wide MAX_MOVES
= 21 with zero headroom — see ``_plan_probe_drops`` for how we
budget the remaining slots."""

MAX_PROBES_PER_TURN = 2
"""Soft cap on EXPLORATION probe drops (fog scouting + RED bracketing)
the heuristic emits per night. Hot-drop securing probes are budgeted
separately (one per secured harvester landing) and are not bound by
this cap. Tuned to leave room in the policy queue for a 3-harvester
chain."""

_PROBE_TARGET_STOCK = 4
"""Working probe magazine the Orbit playbook tops up toward each turn
(v0.9.18). Sized to cover a typical night of hot-drops (RULEBOOK §3.9.7
— one securing probe per uncovered vein) plus an exploration probe.
Carry-over stock means we only build the shortfall.

v1.13 — the build used to hold back a credit reserve so the catapult
ship step could always afford a bid. Shipping is free and automatic
now, so probes may spend the turn's whole remainder."""

# ── Default per-unit Orbit-phase costs ────────────────────────────
#
# These mirror the constants in :mod:`sea_of_colours.game.session`
# (and are re-emitted by ``orbit_block.ship_prices`` in the agent
# view, which is the source of truth). They exist here only as
# fallbacks for tests / dev sandboxes that hand the agent a
# stripped-down view without the ship_prices block.
_DEFAULT_REPAIR_COST = 500
_DEFAULT_PROBE_BUILD_COST = 250
_DEFAULT_HARVESTER_BUILD_COST = 1500
_DEFAULT_HARVESTER_CAP = 3

# v0.9.9 — RED_HARVEST economy tuning ──────────────────────────────
#
# These mirror the canonical thresholds in the rest of the engine but
# are kept here as the agent's own knobs so a sandbox view that omits
# the matching blocks still behaves sanely.
_VEIN_MIN_PURITY = 51
"""Lowest purity that still counts as VEIN (RULEBOOK §3.1 tier bands:
trace 0-50, vein 51-150, mass 151-254, pure 255).

v1.13 — this used to be the ship/refine threshold in the Orbit
playbook. Everything ships now, so it only informs where the night
planner is willing to send a harvester."""

_BLUE_EMP_THRESHOLD = 250
"""When the seat's rolled-up BLUE purity is ABOVE this, RED_HARVEST
rolls a 50% chance to build an EMP in orbit (blue is the weapons
currency — surplus blue is better spent on interdiction than
hoarded)."""

_LOW_BLUE_THRESHOLD = 250
"""When the seat's rolled-up BLUE purity is AT or BELOW this, the
night planner folds visible BLUE tiles into the harvest target pool
so harvesters top the weapons economy back up."""

_DEFAULT_EMP_BLUE_COST = 200
_DEFAULT_EMP_CREDIT_COST = 0

_DEFAULT_EMP_RADIUS = 2
_DEFAULT_EMP_MISSILES = 3

_DEFAULT_CHAFF_BLUE_COST = 300  # v1.36 — was 255
_DEFAULT_CHAFF_CREDIT_COST = 0

_DEFAULT_WEAPONISED_BLUE_CAP = 600
"""Fallback for ``meta.rules.weapon_blue_cap`` (RULEBOOK §4.9.8) — the
most blue-worth of ordnance a seat may hold. Read off the view when it
is there, exactly like the weapon prices above, so a retune does not
need an agent edit."""

_BLUE_WEAPON_TARGET = 400
"""Rolled-up BLUE the seat farms toward before it's "flush" on weapons
currency. Below this, a SURPLUS harvester (the seat has 2+ and there's no
unclaimed high-tier RED left for it) diverts to BLUE to fund EMP/chaff
builds; at or above it, every harvester reverts to normal RED-first work."""

_BLUE_ALWAYS_BUILD_THRESHOLD = 300
"""BLUE above which the Orbit playbook ALWAYS builds a weapon: chaff first
when we hold none (for the hour 5-7 / 11-13 egress jam, RULEBOOK §5) else an
EMP. Between this and ``_BLUE_EMP_THRESHOLD`` the build stays a 50% EMP roll."""

_CHAFF_EGRESS_WINDOWS = ((5, 7), (11, 13))
"""Praxis-hour windows RED_HARVEST aims a chaff flare at — the tail of each
shift when rival harvesters are lifting off (pickup). A flare cancels every
OTHER seat's action for its hour (RULEBOOK §5), so a well-timed jam strands a
loaded harvester for the dawn destruction. One hour is drawn at random from a
randomly-chosen window each night so the timing isn't predictable (and to
spread the launcher's own ``CHAFF_DURATION_HOURS-1`` self-jam around)."""

# Fallback RED tier-quality multipliers (RULEBOOK §3.1). The live
# values ride on the view as ``orbit.ship_prices.quality_mult``; this
# table only kicks in for stripped-down test views. A ``pure`` parcel
# is worth 4× a ``trace`` one of the same raw purity — so target
# selection must rank on tier-weighted SCORE, not raw purity, or the
# harvesters chase fat-but-cheap TRACE blobs over lean PURE veins.
_RED_TIER_MULT = {"trace": 0.75, "vein": 1.0, "mass": 1.5, "pure": 3.0}


def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _step_toward(
    src: Tuple[int, int],
    dst: Tuple[int, int],
    *,
    width: int,
    height: int,
    blocked: Optional[set] = None,
) -> Optional[Tuple[int, int]]:
    """Greedy single-step that closes the Manhattan gap.

    Returns ``None`` when no legal axis-aligned move shrinks the
    distance (e.g. already adjacent on a diagonal — game rules allow
    only ±1 cardinal steps).
    """
    blocked = blocked or set()
    sx, sy = src
    dx, dy = dst
    candidates: List[Tuple[int, int]] = []
    if dx > sx:
        candidates.append((sx + 1, sy))
    elif dx < sx:
        candidates.append((sx - 1, sy))
    if dy > sy:
        candidates.append((sx, sy + 1))
    elif dy < sy:
        candidates.append((sx, sy - 1))
    for nx, ny in candidates:
        if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in blocked:
            return (nx, ny)
    return None


def _my_harvesters(view: Dict[str, Any]) -> List[Dict[str, Any]]:
    """All harvesters owned by the seat, in stable id order.

    Returns the entity rows from ``view.entities.mine`` filtered to
    harvesters. Order is by ``id`` so the planner makes the same
    assignments night-over-night for the same fleet composition.
    """
    entities = (view.get("entities") or {}).get("mine") or []
    rows = [ent for ent in entities if ent.get("type") == "harvester"]
    rows.sort(key=lambda r: str(r.get("id") or ""))
    return rows


def _harvester_state(view: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """First harvester owned by the seat (back-compat helper).

    Kept for callers (and tests) that still want the "single
    harvester" shape. New code should walk ``_my_harvesters`` so it
    handles the full fleet.
    """
    rows = _my_harvesters(view)
    return rows[0] if rows else None


def _orblift_state(view: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entities = (view.get("entities") or {}).get("mine") or []
    for ent in entities:
        if ent.get("type") == "orblift":
            return ent
    return None


def _adjacent_drop_targets(
    target: Tuple[int, int],
    *,
    width: int,
    height: int,
    seat: str = "p1",
    seat_rotation: int = 0,
) -> List[Tuple[int, int]]:
    """Adjacent cells around ``target`` that are inside the map.

    Order matters: the caller drops on the first candidate that
    passes :func:`_is_valid_drop`. To break symmetry on N-seat
    RED_HARVEST runs (v0.9.6), each seat walks the offset list at
    a different rotation index, so when N seats see the same RED
    tile they naturally fan out to different drop sides instead
    of all colliding on the same one. ``seat_rotation`` is the
    explicit knob (0..3); the legacy ``seat == "p2"`` rule is
    preserved as a fallback when no rotation is supplied.
    """
    tx, ty = target
    offsets = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    if seat_rotation:
        r = int(seat_rotation) % len(offsets)
        offsets = offsets[r:] + offsets[:r]
    elif seat == "p2":
        # Back-compat with the v0.8 hardcoded reverse for 2-seat
        # games that never pass a rotation index.
        offsets = list(reversed(offsets))
    out: List[Tuple[int, int]] = []
    for dx, dy in offsets:
        nx, ny = tx + dx, ty + dy
        if 0 <= nx < width and 0 <= ny < height:
            out.append((nx, ny))
    return out


def _has_world_block(view: Dict[str, Any]) -> bool:
    """True when the view carries a real ``world`` block.

    Used to tell apart "this is a stripped-down legacy test view —
    skip the live/echo gate" from "this is a real agent view but
    the player has no surface vision yet — drops are forbidden
    until a probe is launched". The two cases look identical at
    the ``visible_set`` level (both produce an empty set) but
    require opposite behaviour in :func:`_is_valid_drop`.

    v0.9.18 — recognise BOTH world serialisations (RULEBOOK §appendix
    ``SOC_AGENT_WORLD_VIEW``): the legacy ``list`` shape (``live[]`` /
    ``echo[]``) AND the ``grid`` shape (2D ``grid[y][x]``). The old
    code only knew the list shape, so under ``grid`` mode it reported
    "no world block" and :func:`_is_valid_drop` fell back to its
    permissive legacy path — dropping harvesters into fog that
    ``live_only`` then rejected.
    """
    world = view.get("world")
    if not isinstance(world, dict):
        return False
    return "live" in world or "echo" in world or "grid" in world


def _live_only_mode(view: Dict[str, Any]) -> bool:
    """True when the canonical drop rule is ``live_only`` (RULEBOOK §3.9.7).

    Read from ``meta.rules.drop_mode`` (surfaced by the agent view).
    In live-only mode a harvester may ONLY land where the seat has a
    LIVE sensor beacon right now (an active probe disk or a friendly
    harvester's plus); stale own-echo / memory no longer validates a
    drop. The engine enforces this in :meth:`GameSession.try_drop_unit`,
    so the heuristic must mirror it or it plans drops the engine
    refuses (a wasted night).
    """
    rules = (view.get("meta") or {}).get("rules") or {}
    return str(rules.get("drop_mode") or "").strip().lower() == "live_only"


def _world_grid(view: Dict[str, Any]) -> Optional[List[List[Any]]]:
    """The 2D ``grid[y][x]`` array when the view uses ``grid`` world mode."""
    world = view.get("world")
    if not isinstance(world, dict):
        return None
    grid = world.get("grid")
    if isinstance(grid, list) and grid and isinstance(grid[0], list):
        return grid
    return None


def _live_cells(view: Dict[str, Any]) -> Set[Tuple[int, int]]:
    """Set of cells the seat has LIVE vision on RIGHT NOW (echo excluded).

    The live-only drop gate keys on this set. Reads ``world.live`` in
    list mode; in grid mode it walks ``grid[y][x]`` and keeps cells
    that are NOT flagged ``echo`` (a live cell has no ``echo`` key).
    """
    out: Set[Tuple[int, int]] = set()
    grid = _world_grid(view)
    if grid is not None:
        for y, row in enumerate(grid):
            for x, cell in enumerate(row):
                if isinstance(cell, dict) and not cell.get("echo"):
                    out.add((x, y))
        return out
    world = view.get("world")
    if not isinstance(world, dict):
        return out
    for row in world.get("live") or []:
        try:
            out.add((int(row["x"]), int(row["y"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _drop_targetable_cells(view: Dict[str, Any]) -> Set[Tuple[int, int]]:
    """Cells a harvester may legally LAND on under the active drop rule.

    ``live_only`` → LIVE cells only (mirrors the engine). Otherwise the
    permissive live+echo set (legacy ``live_or_echo`` rule).
    """
    if _live_only_mode(view):
        return _live_cells(view)
    return _visible_or_echo_cells(view)


def _visible_or_echo_cells(view: Dict[str, Any]) -> Set[Tuple[int, int]]:
    """Set of cells the seat currently has live OR echo vision on.

    RULEBOOK §3.10 — harvesters can only land on cells in this set
    (plus any in ``memory_tiles``, but the agent view doesn't
    expose that directly; live+echo is a safe under-approximation).
    Built from ``view.world.live`` + ``view.world.echo``. Returns
    an empty set if the world block exists but has no surface
    coverage yet (the typical day-1 state — no harvester or probe
    is on the surface so ``tiles_visible_now`` is empty too).

    v0.9.7 — ``echo`` rows with ``via='probe_launch'`` (RULEBOOK
    §3.15) are deliberately EXCLUDED. Those rows carry the enemy
    probe occupant only; the underlying tile stays fog and the
    engine rejects harvester drops onto them. Including them here
    would make the agent plan drops that the engine then refuses,
    burning the orbital turn for nothing.
    """
    out: Set[Tuple[int, int]] = set()
    # v0.9.18 — grid world mode: every non-None grid cell is either
    # live or echo (probe-launch-only markers are dropped to None by
    # the grid renderer), so they're all valid live_or_echo drop sites.
    grid = _world_grid(view)
    if grid is not None:
        for y, row in enumerate(grid):
            for x, cell in enumerate(row):
                if isinstance(cell, dict):
                    out.add((x, y))
        return out
    world = view.get("world")
    if not isinstance(world, dict):
        return out
    for row in world.get("live") or []:
        try:
            out.add((int(row["x"]), int(row["y"])))
        except (KeyError, TypeError, ValueError):
            continue
    for row in world.get("echo") or []:
        if isinstance(row, dict) and row.get("via") == "probe_launch":
            continue
        try:
            out.add((int(row["x"]), int(row["y"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _is_valid_drop(
    view: Dict[str, Any],
    pos: Tuple[int, int],
    visible_set: Optional[Set[Tuple[int, int]]] = None,
) -> bool:
    """A cell is a legal harvester drop when it's NOT RED and is in live/echo.

    The "not RED" check stops the heuristic from dropping ONTO the
    target RED tile (drops auto-harvest there which wastes the
    drop). The live/echo check enforces RULEBOOK §3.10 — fog drops
    are rejected by the engine and waste the whole turn's chain.

    Falls back to "any cell is valid" ONLY when the view has no
    ``world`` block at all (legacy / stripped-down test views).
    When the world block IS present but live+echo are empty
    (the day-1 fresh-game state, before any probe has flown),
    the function returns False for EVERY cell — there is no
    legal landing pad anywhere on the surface yet, so the
    heuristic should idle the harvester and let its probe-drop
    actions seed echo coverage for next turn.
    """
    for r in view.get("red_tiles") or []:
        if (int(r["x"]), int(r["y"])) == pos:
            return False
    if not _has_world_block(view):
        # Legacy test view — keep the old permissive behaviour so
        # the long tail of agent tests that don't bother to stub a
        # world block still see drops succeed.
        return True
    # v0.9.18 — honour the active drop rule. In ``live_only`` mode the
    # engine refuses echo / memory landings, so the planner must gate
    # on LIVE coverage only or it burns the night on a rejected drop
    # (the "bots struggle with disappearing probes" symptom).
    vs = visible_set if visible_set is not None else _drop_targetable_cells(view)
    return pos in vs


# Back-compat alias for tests / old call sites that referenced the
# pre-v0.9.5 name.
def _is_empty_drop(view: Dict[str, Any], pos: Tuple[int, int]) -> bool:
    return _is_valid_drop(view, pos)


def _is_red_cell(view: Dict[str, Any], pos: Tuple[int, int]) -> bool:
    """True when ``pos`` is a known RED tile (live or echo).

    Used by the hot-drop planner to keep the harvester's LANDING cell
    off the target RED itself — the unit lands adjacent and walks onto
    the RED so the auto-harvest fires on a deliberate step.
    """
    for r in view.get("red_tiles") or []:
        try:
            if (int(r["x"]), int(r["y"])) == pos:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def _seat_day_rng(view: Dict[str, Any], seat: str, salt: str = "") -> _random.Random:
    """Return a seeded :class:`random.Random` for the seat × day × session.

    v0.9.8 — every random choice the heuristic makes (probe drop
    rotation, harvester drop site, fog-scout axis lead, etc.) is
    seeded from ``(session_id, seat, day, salt)``. That gives us:

    - Determinism within a single (session, seat, day, turn): the
      same view replans to the same actions, so tests stay stable
      and a transient retry doesn't suddenly change the move set.
    - Decorrelation across seats: two RED_HARVEST seats running
      against the same world snapshot draw from different streams,
      so they don't pile probes / harvesters on the same cell.
    - Variety across games: the session_id salt means every fresh
      game gets a different sequence of starting positions, instead
      of every game opening with the same four-corner pattern.
    """
    meta = view.get("meta") or {}
    hud = view.get("hud") or {}
    session_id = str(meta.get("session_id") or "")
    day = int(hud.get("day") or meta.get("day") or 0)
    seed_str = f"{session_id}|{seat}|{day}|{salt}"
    return _random.Random(seed_str)


def _fog_scout_walk(
    *,
    view: Dict[str, Any],
    start: Tuple[int, int],
    width: int,
    height: int,
    harvester_id: str,
    max_steps: int,
    visible_set: Set[Tuple[int, int]],
) -> List[Dict[str, Any]]:
    """Walk a fog-scouting harvester outward from ``start`` for vision.

    v0.9.6 — the fog-push fallback used to do ``drop + pickup`` which
    revealed a single 2-cell disk for one tick before the lift, and in
    HIDDEN mode that vision evaporated as soon as the harvester left
    the surface. This helper instead chains 4–5 steps in the direction
    that points away from the centroid of the seat's current vision —
    that's the "deepest fog" direction relative to where the drop
    landed.

    v0.9.8 — RULEBOOK §3.10 / :meth:`GameSession.try_step_unit` only
    accept CARDINAL steps (Manhattan distance exactly 1). Pre-v0.9.8
    this helper emitted diagonal steps (``+1,+1`` per tick) which the
    engine rejected as ``not adjacent``, burning every queue slot as
    a ``waste`` frame and never actually moving the harvester. We
    now emit a staircase: each step changes EXACTLY one axis by ±1,
    alternating between x-stepping and y-stepping. A per-seat RNG
    decides which axis to lead with so multiple bots planning the
    same fog push don't trace the same path.

    Step targets are NOT validated against the agent's view of
    ``visible_set`` — fog is a perfectly legal step destination from
    the engine's perspective, and gating on visible_set here would
    defeat the whole point of scouting.
    """
    if max_steps <= 0:
        return []
    # Pick a direction vector that pushes away from existing vision.
    # ``visible_set`` is the seat's live+echo cells — its centroid is
    # the "centre of mass" of what we already know. The outward
    # gradient = ``start - centroid``. If we have no vision at all
    # (defensive — caller already guards against this) fall back to
    # heading toward the nearest map corner the start isn't already in.
    if visible_set:
        sx = sum(p[0] for p in visible_set) / len(visible_set)
        sy = sum(p[1] for p in visible_set) / len(visible_set)
        dx = start[0] - sx
        dy = start[1] - sy
    else:
        # Push toward the further-away corner of the map (4 corners).
        corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
        corners.sort(key=lambda c: -_manhattan(c, start))
        dx = corners[0][0] - start[0]
        dy = corners[0][1] - start[1]
    step_dx = 0 if abs(dx) < 1e-6 else (1 if dx > 0 else -1)
    step_dy = 0 if abs(dy) < 1e-6 else (1 if dy > 0 else -1)
    if step_dx == 0 and step_dy == 0:
        # Degenerate — fall back to "down-right" which is always a
        # legal direction on a non-zero grid.
        step_dx, step_dy = 1, 1

    # Decide which axis we step on first. When one axis has a clearly
    # stronger pull, lead with it (the harvester will cover more
    # ground in the dominant direction before zig-zagging). Otherwise
    # let the seat-day RNG break ties so two bots with the same
    # ``start`` and the same visible_set still diverge.
    seat = _seat_of(view)
    rng = _seat_day_rng(view, seat, salt=f"scout|{harvester_id}|{start}")
    abs_dx = abs(dx)
    abs_dy = abs(dy)
    if step_dx != 0 and step_dy != 0:
        if abs_dx > abs_dy * 1.5:
            next_axis = "x"
        elif abs_dy > abs_dx * 1.5:
            next_axis = "y"
        else:
            next_axis = rng.choice(("x", "y"))
    elif step_dx != 0:
        next_axis = "x"
    else:
        next_axis = "y"

    moves: List[Dict[str, Any]] = []
    here = start
    visited: Set[Tuple[int, int]] = {here}
    for _ in range(max_steps):
        # Pick the axis we want to step on this tick. Falls back to
        # the other axis if the preferred one is degenerate (single-
        # axis pull) so we don't emit a no-op step.
        if next_axis == "x" and step_dx != 0:
            nxt = (here[0] + step_dx, here[1])
            this_axis = "x"
        elif step_dy != 0:
            nxt = (here[0], here[1] + step_dy)
            this_axis = "y"
        elif step_dx != 0:
            nxt = (here[0] + step_dx, here[1])
            this_axis = "x"
        else:
            break

        # Bounds check — flip the axis direction if we'd leave the
        # map so the staircase continues parallel to the wall.
        if not (0 <= nxt[0] < width and 0 <= nxt[1] < height):
            if this_axis == "x":
                step_dx = -step_dx
                nxt = (here[0] + step_dx, here[1])
            else:
                step_dy = -step_dy
                nxt = (here[0], here[1] + step_dy)

        if nxt in visited or not (0 <= nxt[0] < width and 0 <= nxt[1] < height):
            break
        # Sanity check — the engine rejects anything that isn't a
        # cardinal step. If we somehow constructed a non-adjacent
        # next cell (shouldn't happen but cheap to assert) we stop
        # rather than queue a guaranteed-invalid action.
        if abs(nxt[0] - here[0]) + abs(nxt[1] - here[1]) != 1:
            break
        moves.append({"a": "step", "unit": harvester_id, "to": [nxt[0], nxt[1]]})
        visited.add(nxt)
        here = nxt
        # Alternate axes for the next tick when both axes still pull.
        if step_dx != 0 and step_dy != 0:
            next_axis = "y" if this_axis == "x" else "x"
    return moves


def _walk_and_harvest(
    *,
    start: Tuple[int, int],
    red_tiles: List[Dict[str, Any]],
    harvester_id: str,
    width: int,
    height: int,
    max_steps: int,
    claimed: Set[Tuple[int, int]],
    avoid_cells: Optional[Set[Tuple[int, int]]] = None,
) -> List[Dict[str, Any]]:
    """Greedy multi-red walk: closest-first, consume RED targets in chain.

    Pops each consumed RED tile out of ``red_tiles`` AND adds its
    coordinates to ``claimed`` so a subsequent harvester planned in
    the same turn can't try to harvest the same square.

    ``avoid_cells`` are hard no-step cells: the harvester harvests
    every tile it steps onto (engine ``_harvest_at`` fires per step),
    so routing the walk around these cells keeps the unit from
    collateral-harvesting GREEN (toxic legacy) or wandering into a
    friendly EMP cloud. Steps that can only be made through an
    avoided cell are simply not taken — the chain stops short rather
    than banking a parcel we don't want.
    """
    avoid_cells = avoid_cells or set()
    moves: List[Dict[str, Any]] = []
    here = start
    steps_left = max_steps
    visited: set = {here}
    while steps_left > 0 and red_tiles:
        # v1.x — drop any tile another unit already banked this turn. The
        # caller may hand us a FILTERED pool (e.g. high-tier-only for a
        # surplus harvester) that isn't the shared list, so we can't rely on
        # pops alone for cross-harvester dedup — honour ``claimed`` directly.
        red_tiles[:] = [
            r for r in red_tiles
            if (int(r["x"]), int(r["y"])) not in claimed
        ]
        if not red_tiles:
            break
        red_tiles.sort(
            key=lambda r: (
                _manhattan(here, (int(r["x"]), int(r["y"]))),
                -int(r.get("value", r.get("purity", 0))),
            )
        )
        target = (int(red_tiles[0]["x"]), int(red_tiles[0]["y"]))
        if target == here:
            red_tiles.pop(0)
            claimed.add(target)
            continue
        nxt = _step_toward(
            here, target, width=width, height=height,
            blocked=visited | avoid_cells,
        )
        if nxt is None:
            break
        moves.append({"a": "step", "unit": harvester_id, "to": list(nxt)})
        visited.add(nxt)
        here = nxt
        steps_left -= 1
        if here == target:
            red_tiles.pop(0)
            claimed.add(target)
    return moves


def _pick_fog_anchor(
    view: Dict[str, Any],
    *,
    avoid: Sequence[Tuple[int, int]],
    min_separation: int = 6,
) -> Optional[Tuple[int, int]]:
    """Pick a frontier cell for the harvester to push into the fog from.

    RULEBOOK §3.10 — harvesters can only land on tiles the seat
    currently has live OR echo vision on. So "push beyond the
    visible area" means dropping at the **edge** of the visible
    region (``cluster.nearest_visible_edge``), not at the deep-
    fog centroid. The harvester's 2-cell vision disk then reveals
    fresh ground beyond the frontier for the next turn.

    Walks the cluster list in priority order; for each cluster
    that has a ``nearest_visible_edge``, returns that edge UNLESS
    a previous harvester this turn already claimed it (then
    falls through to the next cluster). Quarter-point fallback
    fires when no cluster gives a unique edge.

    v0.9.8 — the quarter-point fallback used to pick a fixed cell
    list in fixed order, so two RED_HARVEST seats whose probe maps
    overlapped (common in 3- / 4-seat games on day 2 before any RED
    is visible) would both land on ``(width//4, height//4)`` and the
    engine would crash one of the two harvesters as a stacked drop.
    We now shuffle that fallback list with a (session, seat, day)
    seeded RNG so seats fan out organically across the candidate
    pool. Cluster passes 1 + 2 keep their priority-order behaviour
    so the visible-edge optimisation still applies whenever a
    cluster anchor is available.
    """
    width = int(view.get("grid", {}).get("width", 0))
    height = int(view.get("grid", {}).get("height", 0))
    if width == 0 or height == 0:
        return None

    def _too_close(pt: Tuple[int, int]) -> bool:
        for q in avoid:
            if _manhattan(pt, q) < min_separation:
                return True
        return False

    avoid_set = set(tuple(p) for p in avoid)
    clusters = list(view.get("fog_clusters") or [])
    # Pass 1: visible-edge anchor that respects min_separation.
    for cluster in clusters:
        edge = cluster.get("nearest_visible_edge")
        if not edge:
            continue
        ex, ey = int(edge[0]), int(edge[1])
        if not (0 <= ex < width and 0 <= ey < height):
            continue
        if not _too_close((ex, ey)):
            return (ex, ey)
    # Pass 2: any visible-edge anchor THAT ISN'T ALREADY CLAIMED.
    # This stops 3 harvesters from stacking on the same edge when
    # only one fog cluster has a visible edge (the common late-
    # season case).
    for cluster in clusters:
        edge = cluster.get("nearest_visible_edge")
        if not edge:
            continue
        ex, ey = int(edge[0]), int(edge[1])
        if not (0 <= ex < width and 0 <= ey < height):
            continue
        if (ex, ey) in avoid_set:
            continue
        return (ex, ey)
    # Pass 3: quarter-point rotation as a last-resort drop site.
    # These are reachable on day-1 (every tile is freshly visible
    # to the harvester's own orbital vision) so the drop succeeds
    # even with no cluster data.
    candidates = [
        (max(1, width // 4), max(1, height // 4)),
        (min(width - 2, width * 3 // 4), min(height - 2, height * 3 // 4)),
        (max(1, width // 4), min(height - 2, height * 3 // 4)),
        (min(width - 2, width * 3 // 4), max(1, height // 4)),
        (width // 2, height // 2),
    ]
    # v0.9.8 — seat × day shuffle so two seats that fall through to
    # this fallback don't both grab ``candidates[0]``. The shuffle
    # is deterministic within a turn (idempotent replan); see
    # ``_seat_day_rng`` for the seed shape.
    seat = _seat_of(view)
    rng = _seat_day_rng(view, seat, salt="harvester-anchor")
    rng.shuffle(candidates)
    for cand in candidates:
        if cand in avoid_set:
            continue
        if not _too_close(cand):
            return cand
    return None


def _plan_one_harvester(
    view: Dict[str, Any],
    harvester: Dict[str, Any],
    red_tiles: List[Dict[str, Any]],
    claimed: Set[Tuple[int, int]],
    avoid_drops: List[Tuple[int, int]],
    *,
    max_steps: int = MAX_HARVESTER_STEPS,
    blue_tiles: Optional[List[Dict[str, Any]]] = None,
    avoid_cells: Optional[Set[Tuple[int, int]]] = None,
    probe_budget: Optional[List[int]] = None,
    blue_preferred: bool = False,
) -> Tuple[List[Dict[str, Any]], str]:
    """Plan the night chain for ONE harvester.

    Caller passes in ``red_tiles`` (mutable — consumed targets are
    popped) and ``claimed`` (also mutated). ``avoid_drops`` carries
    the drop coords already used by other harvesters in this turn so
    the fog-push fallback spreads units out across the map.
    ``avoid_cells`` is a hard no-go set (e.g. the friendly-fire
    footprint of this night's EMP salvo) — the planner never drops
    a harvester onto, nor anchors a target inside, those cells.

    Returns ``(moves, descriptor)`` where ``descriptor`` is a short
    string fragment ("p1's harvester_p1_1: drove to RED@[12,8]") for
    the rationale line.
    """
    width = int(view["grid"]["width"])
    height = int(view["grid"]["height"])
    harvester_id = harvester.get("id") or "harvester_p1"
    pos = harvester.get("pos")
    damaged = bool(harvester.get("damaged"))
    seat = _seat_of(view)
    # v0.9.18 — the set a harvester may LAND on depends on the drop
    # rule: live-only restricts to current LIVE beacons; otherwise
    # live+echo. ``_is_valid_drop`` uses this same set.
    visible_set = _drop_targetable_cells(view)
    avoid_cells = avoid_cells or set()
    mult_map = _tier_mult_map(view)
    moves: List[Dict[str, Any]] = []

    # Damaged harvester on the surface: pickup and let the next
    # orbit phase repair it. Damaged in orbit: skip entirely so the
    # repair action takes effect first.
    if damaged:
        if pos is not None:
            moves.append({"a": "pickup", "unit": harvester_id})
            return moves, f"{harvester_id}: damaged, pulling back to orbit"
        return moves, f"{harvester_id}: damaged in orbit, awaiting repair"

    # Surface harvester: walk into RED chain.
    if pos is not None:
        start = (int(pos[0]), int(pos[1]))
        # v1.x — a BLUE-PREFERRED (surplus) harvester chases only HIGH-tier
        # RED (mass/pure); when none is reachable it seeks BLUE to build the
        # weapons economy, and takes low-tier RED only as a last resort.
        # Everyone else keeps the RED-first (all tiers) → BLUE fallback order.
        primary_red = (
            [r for r in red_tiles if _is_high_tier(r)]
            if blue_preferred else red_tiles
        )
        chain = _walk_and_harvest(
            start=start,
            red_tiles=primary_red,
            harvester_id=harvester_id,
            width=width,
            height=height,
            max_steps=max_steps,
            claimed=claimed,
            avoid_cells=avoid_cells,
        )
        if chain:
            chain.append({"a": "pickup", "unit": harvester_id})
            tgt = chain[-2].get("to") if len(chain) >= 2 else None
            hi = " (high-RED)" if blue_preferred else ""
            return chain, f"{harvester_id}@{list(pos)}→RED{tgt}{hi}"
        # No reachable (high-)RED — fall back to the nearest reachable BLUE
        # so the weapons economy gets topped up (RULEBOOK §4.6). For a normal
        # harvester this only fires on a low-blue night; for a surplus one it
        # is the whole point (funding EMP/chaff toward _BLUE_WEAPON_TARGET).
        if blue_tiles:
            bchain = _walk_and_harvest(
                start=start,
                red_tiles=blue_tiles,
                harvester_id=harvester_id,
                width=width,
                height=height,
                max_steps=max_steps,
                claimed=claimed,
                avoid_cells=avoid_cells,
            )
            if bchain:
                bchain.append({"a": "pickup", "unit": harvester_id})
                tgt = bchain[-2].get("to") if len(bchain) >= 2 else None
                tag = "blue seek" if blue_preferred else "low-blue top-up"
                return bchain, f"{harvester_id}@{list(pos)}→BLUE{tgt} ({tag})"
        # Surplus harvester with no high-RED and no reachable BLUE — take any
        # RED (incl. low tier) before idling rather than waste the unit.
        if blue_preferred:
            lchain = _walk_and_harvest(
                start=start,
                red_tiles=red_tiles,
                harvester_id=harvester_id,
                width=width,
                height=height,
                max_steps=max_steps,
                claimed=claimed,
                avoid_cells=avoid_cells,
            )
            if lchain:
                lchain.append({"a": "pickup", "unit": harvester_id})
                tgt = lchain[-2].get("to") if len(lchain) >= 2 else None
                return lchain, f"{harvester_id}@{list(pos)}→RED{tgt} (low-tier fallback)"
        # No reachable RED — leave the harvester where it is. The
        # next surface tick we'll try again. (We deliberately do
        # NOT pickup-only here because a healthy on-surface
        # harvester still expands LoS just by existing.)
        return [], f"{harvester_id}@{list(pos)} idle (no RED reachable)"

    # Orbital harvester: drop adjacent to the JUICIEST reachable RED
    # CLUSTER, not just the single fattest pixel. Each candidate is
    # scored by the tier-weighted value of every unclaimed RED within
    # a chain's reach (``max_steps``), discounted by distance, so the
    # harvester lands where it can bank the most total score in one
    # outing — chasing a lone TRACE blob over a tight PURE/MASS vein
    # was the "fails to go after the juicy parcels" complaint.
    unclaimed = [
        r for r in red_tiles
        if (int(r["x"]), int(r["y"])) not in claimed
        and (int(r["x"]), int(r["y"])) not in avoid_cells
    ]
    # v1.x — a BLUE-PREFERRED (surplus) harvester only drops for HIGH-tier
    # RED (mass/pure). With no unclaimed high seam it skips the RED drop
    # entirely and falls through to the BLUE-drop block below, building the
    # weapons economy instead of chasing a low-tier blob.
    if blue_preferred:
        unclaimed = [r for r in unclaimed if _is_high_tier(r)]
    if unclaimed:
        cx, cy = width // 2, height // 2

        def _cluster_score(anchor: Dict[str, Any]) -> float:
            ax, ay = int(anchor["x"]), int(anchor["y"])
            total = 0.0
            for r in unclaimed:
                d = _manhattan((int(r["x"]), int(r["y"])), (ax, ay))
                if d <= max_steps:
                    total += _parcel_score(r, mult_map) / (1 + d)
            return total

        unclaimed.sort(
            key=lambda r: (
                -_cluster_score(r),
                -_parcel_score(r, mult_map),
                _manhattan((int(r["x"]), int(r["y"])), (cx, cy)),
            )
        )
        target = (int(unclaimed[0]["x"]), int(unclaimed[0]["y"]))
        candidates = _adjacent_drop_targets(
            target, width=width, height=height, seat=seat,
        )
        drop_at = next(
            (
                c for c in candidates
                if _is_valid_drop(view, c, visible_set)
                and c not in avoid_drops
                and c not in avoid_cells
            ),
            None,
        )
        if drop_at is not None:
            avoid_drops.append(drop_at)
            moves.append({"a": "drop", "unit": harvester_id, "at": list(drop_at)})
            walk = _walk_and_harvest(
                start=drop_at,
                red_tiles=red_tiles,
                harvester_id=harvester_id,
                width=width,
                height=height,
                max_steps=max_steps,
                claimed=claimed,
                avoid_cells=avoid_cells,
            )
            moves.extend(walk)
            moves.append({"a": "pickup", "unit": harvester_id})
            return moves, f"{harvester_id} drop@{list(drop_at)}→RED@{list(target)}"

        # v0.9.18 — HOT-DROP. No LIVE landing pad next to the best RED
        # target (its probe coverage lapsed to echo, or it's a freshly
        # remembered vein). Under ``live_only`` the engine refuses an
        # echo landing, so a plain drop here would waste the night —
        # the "bots struggle with disappearing probes" symptom. Instead
        # SECURE the drop: launch a probe ONTO the target RED this hour
        # (its radius disk lights the adjacent cells) and drop the
        # harvester into that now-live disk the NEXT hour. The engine
        # re-snapshots live coverage each hour, so a probe deployed at
        # hour N validates a drop at hour N+1 (proven mechanically).
        # Costs one probe from stock; guaranteed save for an EMP or a
        # rival landing on the same cell first. Only fires in live-only
        # mode (echo drops are legal otherwise) and only with budget.
        if (
            _live_only_mode(view)
            and probe_budget is not None
            and probe_budget[0] > 0
            and target not in avoid_cells
        ):
            hot_at = next(
                (
                    c for c in candidates
                    if c not in avoid_drops
                    and c not in avoid_cells
                    and not _is_red_cell(view, c)
                ),
                None,
            )
            if hot_at is not None:
                probe_budget[0] -= 1
                avoid_drops.append(hot_at)
                moves.append({"a": "probe", "at": [target[0], target[1]]})
                moves.append(
                    {"a": "drop", "unit": harvester_id, "at": list(hot_at)}
                )
                walk = _walk_and_harvest(
                    start=hot_at,
                    red_tiles=red_tiles,
                    harvester_id=harvester_id,
                    width=width,
                    height=height,
                    max_steps=max_steps,
                    claimed=claimed,
                    avoid_cells=avoid_cells,
                )
                moves.extend(walk)
                moves.append({"a": "pickup", "unit": harvester_id})
                return moves, (
                    f"{harvester_id} HOT-DROP: secure probe@{list(target)} "
                    f"+ drop@{list(hot_at)}→RED@{list(target)}"
                )

    # No unclaimed visible RED — on a low-blue night, try to drop
    # next to the best unclaimed BLUE before falling through to a
    # vision-only fog push. RED was exhausted above, so this never
    # costs the seat a RED run.
    if blue_tiles:
        unclaimed_blue = [
            b for b in blue_tiles
            if (int(b["x"]), int(b["y"])) not in claimed
        ]
        unclaimed_blue = [
            b for b in unclaimed_blue
            if (int(b["x"]), int(b["y"])) not in avoid_cells
        ]
        if unclaimed_blue:
            cx, cy = width // 2, height // 2
            unclaimed_blue.sort(
                key=lambda b: (
                    -int(b.get("value", b.get("purity", 0))),
                    _manhattan((int(b["x"]), int(b["y"])), (cx, cy)),
                )
            )
            btarget = (int(unclaimed_blue[0]["x"]), int(unclaimed_blue[0]["y"]))
            bcandidates = _adjacent_drop_targets(
                btarget, width=width, height=height, seat=seat,
            )
            bdrop = next(
                (
                    c for c in bcandidates
                    if _is_valid_drop(view, c, visible_set)
                    and c not in avoid_drops
                    and c not in avoid_cells
                ),
                None,
            )
            if bdrop is not None:
                avoid_drops.append(bdrop)
                moves.append({"a": "drop", "unit": harvester_id, "at": list(bdrop)})
                bwalk = _walk_and_harvest(
                    start=bdrop,
                    red_tiles=blue_tiles,
                    harvester_id=harvester_id,
                    width=width,
                    height=height,
                    max_steps=max_steps,
                    claimed=claimed,
                    avoid_cells=avoid_cells,
                )
                moves.extend(bwalk)
                moves.append({"a": "pickup", "unit": harvester_id})
                return moves, (
                    f"{harvester_id} drop@{list(bdrop)}→BLUE@{list(btarget)} "
                    "(low-blue top-up)"
                )

    # No unclaimed visible RED — push the harvester into fog so its
    # 2-cell vision disk reveals new ground for next turn.
    # v0.9.5 — on day 1 (fresh game, no probe yet) the seat has
    # ZERO live/echo coverage. Trying to drop a harvester there
    # would always fail RULEBOOK §3.10 ("can only land on live or
    # echo tiles"). Short-circuit to a clean "idle in orbit, let
    # the probe drops below build us some echo for tomorrow" path
    # so we don't waste any of the night's policy budget on a
    # doomed drop+pickup pair.
    if _has_world_block(view) and not visible_set:
        return [], (
            f"{harvester_id}: idling in orbit "
            "(no live/echo coverage yet — probes flying first)"
        )
    fog = _pick_fog_anchor(view, avoid=avoid_drops + list(claimed) + list(avoid_cells))
    if fog is None or not _is_valid_drop(view, fog, visible_set) or fog in avoid_cells:
        # Fog anchor didn't land on a live/echo cell — fall back to
        # ANY live/echo cell that isn't yet claimed, preferring ones
        # furthest from existing drops so we maximise new vision.
        if visible_set:
            far_first = sorted(
                visible_set,
                key=lambda c: -min(
                    (_manhattan(c, d) for d in (avoid_drops + list(claimed))),
                    default=10**6,
                ),
            )
            for cand in far_first:
                if (
                    _is_valid_drop(view, cand, visible_set)
                    and cand not in avoid_drops
                    and cand not in avoid_cells
                ):
                    fog = cand
                    break
            else:
                return [], f"{harvester_id}: no RED, no visible drop site — idling"
        else:
            return [], f"{harvester_id}: no RED, no fog target — idling"
    avoid_drops.append(fog)
    moves.append({"a": "drop", "unit": harvester_id, "at": list(fog)})
    # v0.9.6 — walk N cells outward from the drop so the harvester's
    # surface LoS actually rolls the fog frontier back. The old
    # behaviour (drop + immediate pickup) gave a single 2-cell vision
    # disk for ONE tick before the lift, which in HIDDEN mode doesn't
    # persist as echo (the cell is "live" only while the harvester is
    # on it). By walking through the fog the harvester accumulates a
    # ribbon of newly-revealed cells along its path, and any RED
    # discovered en route will be in the next day's ``red_tiles`` so
    # ``_walk_and_harvest`` can chase it. Steps are NOT gated by fog
    # on the engine side (see :meth:`GameSession.try_step_unit`), so
    # we can safely walk into uncharted ground — the simulator reveals
    # each cell as the harvester enters.
    scout_steps = _fog_scout_walk(
        view=view,
        start=fog,
        width=width,
        height=height,
        harvester_id=harvester_id,
        max_steps=max_steps,
        visible_set=visible_set,
    )
    moves.extend(scout_steps)
    moves.append({"a": "pickup", "unit": harvester_id})
    return moves, (
        f"{harvester_id} scouting@{list(fog)} → {len(scout_steps)} fog-step(s)"
    )


def _seat_of(view: Dict[str, Any]) -> str:
    """Best-effort extraction of the viewer's seat id from the agent view.

    Falls back to ``"p1"`` when the view shape is too stripped to
    name the seat — that mirrors what the engine assumes on a
    legacy / hand-rolled view (and the symmetry-break below is a
    no-op in that case, which is the right default).
    """
    meta = view.get("meta") or {}
    hud = view.get("hud") or {}
    return str(meta.get("player") or hud.get("player") or "p1")


def _seat_rotation(view: Dict[str, Any], seat: str) -> Tuple[int, int]:
    """Return ``(seat_index, seat_count)`` for the current view.

    v0.9.6 — N-seat helper that walks the session's seat list
    (carried on the view as ``view["players"]`` or
    ``view["meta"]["players"]``) and finds where the current seat
    sits. Falls back to ``(0, 1)`` when the view doesn't carry a
    seat list — that triggers the legacy p1/p2 path in the
    callers, so 2-seat tests keep working unchanged.
    """
    players_raw = view.get("players")
    if not isinstance(players_raw, (list, tuple)):
        meta = view.get("meta") or {}
        players_raw = meta.get("players")
    if not isinstance(players_raw, (list, tuple)) or not players_raw:
        return (0, 1)
    try:
        idx = list(players_raw).index(seat)
    except ValueError:
        idx = 0
    return (idx, len(players_raw))


def _sign_centres(
    regions: Any, width: int, height: int,
) -> List[Tuple[int, int]]:
    """Clamped integer centres of blue-sign / redsign regions.

    Both signs carry a deliberately **jittered** ``center`` that points
    *roughly* at the seam without pinpointing the exact square (RULEBOOK
    §4.10 blue-sign / §4.11 redsign). RED_HARVEST aims a probe there and
    lets the radius-4 vision disk sweep the true tile in. Returns ``[]``
    for a missing or malformed region list.
    """
    out: List[Tuple[int, int]] = []
    if not isinstance(regions, (list, tuple)):
        return out
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        ctr = reg.get("center")
        if not isinstance(ctr, (list, tuple)) or len(ctr) < 2:
            continue
        try:
            cx = max(0, min(width - 1, int(round(float(ctr[0])))))
            cy = max(0, min(height - 1, int(round(float(ctr[1])))))
        except (TypeError, ValueError):
            continue
        out.append((cx, cy))
    return out


def _plan_probe_drops(
    view: Dict[str, Any],
    *,
    max_probes: int = MAX_PROBES_PER_TURN,
    min_separation: int = 8,
    avoid_drops: Optional[Sequence[Tuple[int, int]]] = None,
) -> List[Dict[str, Any]]:
    """Drop probes on the largest fog clusters, **deep** into the fog.

    Aims at each cluster's centroid (not its visible-edge boundary)
    so a single probe drop maximises new ground revealed. Successive
    probes in the same turn must clear ``min_separation`` cells from
    already-placed probes / harvester drops.

    v0.9.5 — when both seats run the same heuristic against the
    same map, they'd pick the same cluster centroids and the probes
    would collide on deployment (RULEBOOK §3.11.1). To break the
    symmetry, p2 walks the cluster list in REVERSE and offsets its
    centroid by (+1, +1) cell. The shift is large enough to escape
    a same-cell collision but small enough that the probe still
    lands inside the cluster and reveals roughly the same ground.

    Day-1 fallback (no fog clusters returned by the view yet): drop
    at the NW and SE quarter points so the player walks into Day 2
    with vision in two distant regions.
    """
    width = int(view["grid"]["width"])
    height = int(view["grid"]["height"])
    seat = _seat_of(view)
    moves: List[Dict[str, Any]] = []
    used: List[Tuple[int, int]] = list(avoid_drops or [])
    clusters = list(view.get("fog_clusters") or [])
    # v0.9.8 — seat × day × session RNG. Drives both (a) the random
    # tie-break inside the cluster-shift jitter and (b) the
    # shuffle of the quadrant-fallback table, so two bots running
    # the same heuristic on the same world snapshot stop converging
    # on identical cells. See ``_seat_day_rng`` for the seed shape.
    rng = _seat_day_rng(view, seat, salt="probes")
    # v0.9.6 — N-seat symmetry-break. Each seat in the session gets
    # a unique rotation index (0..3); the rotation is used to
    # (a) walk the cluster list at a different starting offset and
    # (b) shift each centroid by a small per-seat amount so probes
    # don't pile on the same cell in a 3- or 4-seat run. Falls back
    # to the v0.9.5 p1/p2 "reverse + (+1,+1)" rule for legacy 2-seat
    # views that don't carry an explicit seat list.
    seat_idx, seat_count = _seat_rotation(view, seat)
    if seat_count > 1 and seat_idx > 0:
        rot = (seat_idx * len(clusters)) // seat_count if clusters else 0
        clusters = clusters[rot:] + clusters[:rot]
        # Diagonal-shift table that fans 4 seats to 4 corners.
        shifts = [(0, 0), (1, 1), (-1, 1), (1, -1)]
        seat_shift = shifts[seat_idx % len(shifts)]
    elif seat == "p2":
        # Legacy 2-seat shape — the heuristic-tests still pin this.
        clusters = list(reversed(clusters))
        seat_shift = (1, 1)
    else:
        seat_shift = (0, 0)

    existing_probes: List[Tuple[int, int]] = []
    for ent in (view.get("entities") or {}).get("mine") or []:
        if ent.get("type") != "probe":
            continue
        pos = ent.get("pos")
        if pos:
            existing_probes.append((int(pos[0]), int(pos[1])))

    def _too_close(pt: Tuple[int, int]) -> bool:
        for q in used + existing_probes:
            if _manhattan(pt, q) < min_separation:
                return True
        return False

    # v0.9.18 — NOTE: stale-RED recovery is handled UP-FRONT by the
    # hot-drop planner in ``_plan_one_harvester`` (it pairs a securing
    # probe with the dependent drop in the same night). This probe
    # planner is left to pure VISION work — bracketing fresh RED and
    # expanding the fog frontier — and runs on whatever probe budget the
    # hot-drops didn't claim (capped by the caller via ``max_probes``).

    # v0.9.6 — PASS 0: bracket visible RED. Once the seat has spotted
    # any RED, the next probe should NOT chase the largest abstract
    # fog cluster on the other side of the map — it should plant a
    # vision disk at the FRONTIER of the discovered RED so adjacent
    # fog gets scanned for sibling RED tiles. RED on a Sea-of-Colours
    # map tends to come in veins/blobs, not lone pixels, so this is a
    # high-yield play. We pick the cell adjacent to a visible-RED tile
    # that maximises (a) reveals NEW fog cells in its 2-radius disk,
    # and (b) satisfies ``min_separation`` from existing probes.
    red_tiles_view: List[Tuple[int, int]] = []
    for r in view.get("red_tiles") or []:
        try:
            rx, ry = int(r["x"]), int(r["y"])
            if 0 <= rx < width and 0 <= ry < height:
                red_tiles_view.append((rx, ry))
        except (KeyError, TypeError, ValueError):
            continue
    visible_cells = _visible_or_echo_cells(view)
    fog_cells: Set[Tuple[int, int]] = set()
    if visible_cells:
        for y in range(height):
            for x in range(width):
                if (x, y) not in visible_cells:
                    fog_cells.add((x, y))

    def _fog_yield(pt: Tuple[int, int]) -> int:
        """Estimate of how many fog cells a probe at ``pt`` reveals.

        Probe LoS is a **Euclidean** disk of radius ``probe_vision_radius()``
        (canonical 4 = ~49 cells; ``dx²+dy²≤r²``), matching the engine's
        vision model (RULEBOOK §3.9.7). Counting just the fog cells inside
        that disk gives a per-candidate "new vision" score the planner
        ranks on.
        """
        from sea_of_colours.game.tuning import probe_vision_radius

        r = probe_vision_radius()
        r2 = r * r
        n = 0
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy > r2:
                    continue
                qx, qy = pt[0] + dx, pt[1] + dy
                if (qx, qy) in fog_cells:
                    n += 1
        return n

    # v1.x — PASS -1: RACE TO A REDSIGN JACKPOT (RULEBOOK §4.11). A public
    # redsign marks a pure-RED (255) seam — a 3× tier-multiplier prize that
    # can swing a season. Spend at most ONE probe/night lighting up a
    # discovered seam we haven't revealed yet, so a harvester can chain onto
    # the pure vein next night (the tier-weighted target scorer already ranks
    # a visible pure tile top). The sign is jittered off the true square, so
    # we aim at the region centre and let the probe's radius-4 disk sweep it
    # in. Skipped once the centre is out of fog (already revealed / harvested
    # — redsign persists forever, RULEBOOK §4.11, so this guard stops a stale
    # sign from burning a probe every night). Runs AHEAD of RED bracketing so
    # the jackpot always gets the first probe; the cap of one leaves budget
    # for extending known veins.
    if fog_cells:
        for cand in _sign_centres(view.get("redsign"), width, height):
            if len(moves) >= max_probes:
                break
            if cand in used or _too_close(cand) or cand not in fog_cells:
                continue
            if _fog_yield(cand) == 0:
                continue
            used.append(cand)
            moves.append({"a": "probe", "at": [cand[0], cand[1]]})
            break  # one jackpot probe per night — keep budget for RED veins

    if red_tiles_view and fog_cells:
        # For each visible RED, gather its 8-neighbours and rank.
        red_bracket_candidates: List[Tuple[int, int]] = []
        for rx, ry in red_tiles_view:
            for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                    if dx == 0 and dy == 0:
                        continue
                    cx_, cy_ = rx + dx, ry + dy
                    if not (0 <= cx_ < width and 0 <= cy_ < height):
                        continue
                    cand = (cx_, cy_)
                    if _too_close(cand) or cand in used:
                        continue
                    red_bracket_candidates.append(cand)
        # Sort by fog-yield DESC; ties broken by distance to seat-shift
        # quadrant so 4-seat games still fan out a bit when several
        # candidates score equally.
        cx, cy = width // 2 + seat_shift[0] * width // 4, height // 2 + seat_shift[1] * height // 4
        red_bracket_candidates.sort(
            key=lambda c: (-_fog_yield(c), _manhattan(c, (cx, cy)))
        )
        for cand in red_bracket_candidates:
            if len(moves) >= max_probes:
                break
            if cand in used or _too_close(cand):
                continue
            if _fog_yield(cand) == 0:
                break  # remaining candidates reveal no new ground
            used.append(cand)
            moves.append({"a": "probe", "at": [cand[0], cand[1]]})

    # v1.x — PASS 0.5: BLUE-SIGN top-up scouting (RULEBOOK §4.10). BLUE is the
    # weapons currency (EMP/refine fuel); when the seat's rolled-up BLUE is low
    # AND no BLUE tile is visible to harvest yet, aim one probe at a blue-sign
    # centre to reveal a pocket for tomorrow's low-blue harvest fallback
    # (``plan_moves`` folds visible BLUE into the target pool below the
    # threshold). Runs AFTER RED bracketing so a healthy scoring run is never
    # diverted; gated so a full weapons economy never spends a probe on blue.
    blue_total = _blue_purity_total(view)
    visible_blue = [
        b for b in (view.get("blue_tiles") or []) if b.get("x") is not None
    ]
    if fog_cells and blue_total <= _LOW_BLUE_THRESHOLD and not visible_blue:
        for cand in _sign_centres(view.get("blue_sign"), width, height):
            if len(moves) >= max_probes:
                break
            if cand in used or _too_close(cand) or cand not in fog_cells:
                continue
            if _fog_yield(cand) == 0:
                continue
            used.append(cand)
            moves.append({"a": "probe", "at": [cand[0], cand[1]]})
            break  # one blue-sign probe per night

    for cluster in clusters:
        if len(moves) >= max_probes:
            break
        centroid = cluster.get("centroid")
        anchor = (
            centroid
            if centroid
            else cluster.get("nearest_visible_edge")
        )
        if not anchor:
            continue
        ax = max(0, min(width - 1, int(anchor[0]) + seat_shift[0]))
        ay = max(0, min(height - 1, int(anchor[1]) + seat_shift[1]))
        if _too_close((ax, ay)):
            edge = cluster.get("nearest_visible_edge")
            if edge:
                ex = max(0, min(width - 1, int(edge[0]) + seat_shift[0]))
                ey = max(0, min(height - 1, int(edge[1]) + seat_shift[1]))
                if not _too_close((ex, ey)):
                    ax, ay = ex, ey
                else:
                    continue
            else:
                continue
        used.append((ax, ay))
        moves.append({"a": "probe", "at": [ax, ay]})
    # v0.9.6 — TOP-UP, do NOT early-return. The cluster loop above
    # may legitimately emit 0 or 1 probes on day-1 HIDDEN where the
    # view yields a single mega-cluster covering most of the map.
    # The user-visible symptom of the old "if moves: return moves"
    # gate was "bots do nothing on day 1" — only one probe goes up
    # and the harvester can't drop (no live/echo coverage yet) so
    # the seat looks idle. Fall through to the quadrant fallback
    # so the seat always burns its 2 starting probes on night-1.
    # ``_too_close`` and the ``used`` dedup keep the top-up from
    # stacking probes when the cluster loop already saturated.
    #
    # Quadrant table — each seat takes a UNIQUE pair so 1/2/3/4
    # seats fan out instead of stacking. The 2-seat layout used 4
    # corners (NW/NE/SE/SW) and gave each seat opposite corners,
    # which works fine. But naively extending the SAME 4 corners
    # to 4 seats means seats 0 and 2 share cells (just reversed
    # order) and the engine's probe-collision rule (§3.11.1)
    # destroys both probes on the duplicate cell. So we expand the
    # palette to 4 corners + 4 mid-edge points (8 unique cells)
    # and assign each seat one corner + one mid-edge so 4 × 2 = 8
    # cells all land distinct. Falls back to the v0.8 p1/p2 split
    # when no rotation is supplied (legacy callers).
    nw = (max(1, width // 4), max(1, height // 4))
    ne = (min(width - 2, width * 3 // 4), max(1, height // 4))
    se = (min(width - 2, width * 3 // 4), min(height - 2, height * 3 // 4))
    sw = (max(1, width // 4), min(height - 2, height * 3 // 4))
    n_mid = (max(1, width // 2), max(1, height // 4))
    e_mid = (min(width - 2, width * 3 // 4), max(1, height // 2))
    s_mid = (max(1, width // 2), min(height - 2, height * 3 // 4))
    w_mid = (max(1, width // 4), max(1, height // 2))
    if seat_count > 1:
        quadrant_rotation = [
            [nw, s_mid],
            [ne, w_mid],
            [se, n_mid],
            [sw, e_mid],
        ]
        fallback_points = list(quadrant_rotation[seat_idx % len(quadrant_rotation)])
    elif seat == "p2":
        fallback_points = [ne, sw]
    else:
        fallback_points = [nw, se]
    # v0.9.8 — shuffle this seat's pair so the FIRST probe (the one
    # that flies if budget is tight or min_separation knocks the
    # second out) lands at a different quadrant from game to game.
    # Without this, every fresh 4-seat session opened with seat 0
    # dropping at NW (and seat 1 at NE, etc.) — boring AND made it
    # easy for the human to memorise the bot opening. The shuffle
    # is deterministic per (session, seat, day) so the planner
    # remains idempotent within a turn.
    rng.shuffle(fallback_points)
    # v0.9.8 — small per-cell jitter (±1) so two seats whose fallback
    # tables happen to share a cell (the mid-edge points can collide
    # at low map widths) still draw a unique landing spot. The
    # ``min_separation`` / ``_too_close`` guards downstream catch the
    # case where jitter happens to roll into an already-used cell.
    def _jitter(pt: Tuple[int, int]) -> Tuple[int, int]:
        jx = rng.randint(-1, 1)
        jy = rng.randint(-1, 1)
        return (
            max(1, min(width - 2, pt[0] + jx)),
            max(1, min(height - 2, pt[1] + jy)),
        )

    for raw in fallback_points:
        if len(moves) >= max_probes:
            break
        ax, ay = _jitter(raw)
        if (ax, ay) in used:
            ax, ay = raw  # jitter landed on a taken cell — try the canonical anchor
        if (ax, ay) in used:
            continue
        # Honour min_separation here too: the quarter-point fallback
        # can otherwise land right next to an existing probe and
        # waste the drop (covered by the "spread-out" test).
        if _too_close((ax, ay)):
            continue
        used.append((ax, ay))
        moves.append({"a": "probe", "at": [ax, ay]})
    return moves


# ── Economy helpers (v0.9.9) ──────────────────────────────────────


def _blue_purity_total(view: Dict[str, Any]) -> int:
    """Best-effort rolled-up BLUE purity the seat is holding.

    Reads ``orbit.blue_purity_total`` (the engine's canonical
    readout) and falls back to summing BLUE ``hoard_parcels`` when
    that field is absent (stripped-down test views).
    """
    orbit = view.get("orbit") or {}
    total = orbit.get("blue_purity_total")
    if total is not None:
        try:
            return int(total)
        except (TypeError, ValueError):
            pass
    blue = 0
    for p in orbit.get("hoard_parcels") or []:
        if str(p.get("colour", "")).upper() == "BLUE":
            try:
                blue += int(p.get("purity", 0) or 0)
            except (TypeError, ValueError):
                continue
    return blue


def _enemy_harvester_targets(view: Dict[str, Any]) -> List[Tuple[int, int]]:
    """Coords of enemy harvesters the seat can lawfully see this turn.

    Pulls from ``competitor_intel`` — both the fresh
    ``new_this_day`` harvester trails and the older
    ``persistent_echoes`` sightings (RULEBOOK §3.15). Probes are
    deliberately excluded; an EMP is worth far more dropped on a
    loaded harvester than a scout probe. De-duplicated, fresh
    sightings first so the caller targets the most current position.
    """
    intel = view.get("competitor_intel") or {}
    out: List[Tuple[int, int]] = []
    seen: Set[Tuple[int, int]] = set()

    def _add(rows: Any, harvester_only: bool) -> None:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "")
            if harvester_only and "harvester" not in kind:
                continue
            at = row.get("at")
            if not isinstance(at, (list, tuple)) or len(at) < 2:
                continue
            try:
                pt = (int(at[0]), int(at[1]))
            except (TypeError, ValueError):
                continue
            if pt in seen:
                continue
            seen.add(pt)
            out.append(pt)

    _add(intel.get("new_this_day"), harvester_only=True)
    _add(intel.get("persistent_echoes"), harvester_only=True)
    return out


# ── RED tier-weighted scoring (v0.9.10) ───────────────────────────


def _tier_mult_map(view: Dict[str, Any]) -> Dict[str, float]:
    """Live RED tier-quality multipliers, with a constant fallback."""
    prices = (view.get("orbit") or {}).get("ship_prices") or {}
    qm = prices.get("quality_mult")
    if isinstance(qm, dict) and qm:
        out: Dict[str, float] = {}
        for k, v in qm.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        if out:
            return out
    return dict(_RED_TIER_MULT)


def _tier_name_for(row: Dict[str, Any]) -> str:
    """Tier band for a tile row — uses its ``tier`` field, else purity."""
    tier = row.get("tier")
    if isinstance(tier, str) and tier:
        return tier
    p = int(row.get("purity", row.get("value", 0)) or 0)
    if p <= 50:
        return "trace"
    if p <= 150:
        return "vein"
    if p <= 254:
        return "mass"
    return "pure"


def _parcel_score(row: Dict[str, Any], mult_map: Dict[str, float]) -> float:
    """Tier-weighted score a tile contributes if banked (value × mult)."""
    value = int(row.get("value", row.get("purity", 0)) or 0)
    return value * float(mult_map.get(_tier_name_for(row), 1.0))


def _is_high_tier(row: Dict[str, Any]) -> bool:
    """True for a MASS or PURE RED tile — the tiers a surplus harvester
    still diverts for (RULEBOOK §3.1). Everything at VEIN or below is
    "low tier": a blue-preferred (surplus) harvester skips it in favour of
    building the weapons economy on BLUE."""
    return _tier_name_for(row) in ("mass", "pure")


# ── EMP beacon targeting (v0.9.10) ────────────────────────────────


def _latest_enemy_beacon(view: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Coords of the opponent's most-recent probe-launch BEACON.

    Probe launches are public (RULEBOOK §3.15) and surface in
    ``competitor_intel`` as ``enemy_probe_launch`` rows. We pick the
    one with the highest ``day_seen`` (freshest); ``new_this_day``
    beats ``persistent_echoes`` on a tie. Returns ``None`` when the
    seat has seen no enemy beacon.
    """
    intel = view.get("competitor_intel") or {}
    best: Optional[Tuple[int, int]] = None
    best_day = -1
    for bucket, base in (
        (intel.get("new_this_day"), 1_000_000),  # fresh rows win ties
        (intel.get("persistent_echoes"), 0),
    ):
        for row in bucket or []:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "")
            if "probe" not in kind:  # enemy_probe_launch / enemy_probe
                continue
            at = row.get("at")
            if not isinstance(at, (list, tuple)) or len(at) < 2:
                continue
            try:
                pt = (int(at[0]), int(at[1]))
            except (TypeError, ValueError):
                continue
            try:
                day = int(row.get("day_seen", row.get("last_seen_day", 0)) or 0)
            except (TypeError, ValueError):
                day = 0
            rank = base + day
            if rank > best_day:
                best_day = rank
                best = pt
    return best


def _emp_spread_targets(
    aim: Tuple[int, int],
    *,
    count: int,
    radius: int,
    width: int,
    height: int,
) -> List[List[int]]:
    """Fan ``count`` EMP missile targets around ``aim`` to cover wide.

    Each missile spawns a Manhattan radius-``radius`` cloud. Spacing
    the targets ``radius + 1`` cells apart tiles the clouds into one
    broad, gap-free blanket rather than stacking three clouds on the
    same 13-cell patch. The offset ring fans the missiles out along
    both axes (centre, then E/W, then N/S) so the blanket grows in
    two dimensions before it runs out of missiles.
    """
    step = max(1, int(radius) + 1)
    ax, ay = aim
    ring = [
        (0, 0),
        (step, 0), (-step, 0),
        (0, step), (0, -step),
        (step, step), (-step, -step),
    ]
    out: List[List[int]] = []
    seen: Set[Tuple[int, int]] = set()
    for dx, dy in ring:
        if len(out) >= count:
            break
        cx = max(0, min(width - 1, ax + dx))
        cy = max(0, min(height - 1, ay + dy))
        if (cx, cy) in seen:
            continue
        seen.add((cx, cy))
        out.append([cx, cy])
    return out


def _emp_cloud_cells(
    targets: Sequence[Sequence[int]],
    *,
    radius: int,
    width: int,
    height: int,
) -> Set[Tuple[int, int]]:
    """Union of the Manhattan radius-``radius`` clouds at each target."""
    cells: Set[Tuple[int, int]] = set()
    r = int(radius)
    for t in targets:
        try:
            tx, ty = int(t[0]), int(t[1])
        except (TypeError, ValueError, IndexError):
            continue
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if abs(dx) + abs(dy) > r:
                    continue
                qx, qy = tx + dx, ty + dy
                if 0 <= qx < width and 0 <= qy < height:
                    cells.add((qx, qy))
    return cells


# ── Orbit-phase playbook (v0.9.6) ─────────────────────────────────


def plan_orbit_actions(
    view: Dict[str, Any],
    *,
    weapons_enabled: bool = True,
) -> Tuple[List[Dict[str, Any]], str]:
    """Plan an Orbit-phase submission for the seat (RULEBOOK §4).

    ``weapons_enabled=False`` (the hackathon "no weapons" tutorial
    opponent, RED_HARVEST_LITE) skips the weapons priority entirely;
    every other priority is unchanged.

    Returns ``(actions, rationale)`` where ``actions`` is a list of
    wire-format Orbit action dicts ready to hand to
    :func:`sea_of_colours.snowpark.engine.submit_orbit_actions`.

    v1.13 — the playbook is now pure spending, in priority order, with
    **no slot cap**: credits and blue are the only limit.

        1. Repair every damaged harvester — a dead rig earns nothing.
        2. Build a harvester if under the fleet cap and affordable.
        3. Build weapons from surplus BLUE (skipped when disabled).
        4. Top the probe magazine up toward a working stock.

    Three priorities went away with the mechanics behind them: shipping
    RED (now automatic), flushing GREEN (now automatic) and refining
    TRACE upward (removed). Nothing replaces them, which is the point —
    the whole "ship it or refine it?" branch of the playbook collapsed
    into "mine better parcels in the first place".

    Ordering note: probes now come **last** instead of competing with
    the ship step for credits. There is no bid to hold credits back for
    any more, so probes soak up whatever the fleet didn't need.
    """
    orbit = view.get("orbit") or {}
    credits = int(orbit.get("credits", 0))
    cap_used = int(orbit.get("harvester_cap_used", 0))
    cap_max = int(orbit.get("harvester_cap_max", _DEFAULT_HARVESTER_CAP))
    prices = orbit.get("ship_prices") or {}
    repair_cost = int(prices.get("repair", _DEFAULT_REPAIR_COST))
    probe_cost = int(prices.get("probe_build", _DEFAULT_PROBE_BUILD_COST))
    harvester_cost = int(
        prices.get("harvester_build", _DEFAULT_HARVESTER_BUILD_COST),
    )

    # BLUE is the weapons currency; a surplus should pressure rivals
    # rather than sit idle. v1.13 — refining used to compete for the same
    # blue, so surpluses are more common than they were.
    blue_total = _blue_purity_total(view)
    weapon_stock = orbit.get("weapon_stock") or {}
    emp_stock = int(weapon_stock.get("emp", 0) or 0)
    weapon_prices = orbit.get("weapon_prices") or {}
    emp_price = weapon_prices.get("emp") or {}
    emp_blue_cost = int(emp_price.get("blue", _DEFAULT_EMP_BLUE_COST))
    emp_credit_cost = int(emp_price.get("credits", _DEFAULT_EMP_CREDIT_COST))
    chaff_stock = int(weapon_stock.get("chaff", 0) or 0)
    chaff_price = weapon_prices.get("chaff") or {}
    chaff_blue_cost = int(chaff_price.get("blue", _DEFAULT_CHAFF_BLUE_COST))
    chaff_credit_cost = int(chaff_price.get("credits", _DEFAULT_CHAFF_CREDIT_COST))

    actions: List[Dict[str, Any]] = []
    descriptors: List[str] = []
    remaining = credits

    # v1.13 — on the terminal settlement orbit the season ends the moment
    # this resolves, so anything bought here is never used. Bank it and
    # let the automatic settlement do the work.
    #
    # v1.30 — LEGACY PATH, KEPT ON PURPOSE. A new season never reaches
    # here: the simulator now settles the terminal orbit itself rather
    # than asking, precisely BECAUSE this branch's answer ("nothing worth
    # buying") was the only answer anyone ever had. It still fires for a
    # season persisted mid-final-orbit by a pre-v1.30 build, which resumes
    # through the normal submit path. Delete it only once no such save can
    # exist.
    if bool(orbit.get("final_orbit")):
        return [], (
            "final settlement orbit: RED ships and GREEN clears "
            "automatically — nothing worth buying"
        )

    # Priority 1: repair every damaged harvester.
    damaged = [h for h in _my_harvesters(view) if bool(h.get("damaged"))]
    for harv in damaged:
        if remaining < repair_cost:
            descriptors.append(
                f"deferred repair on {harv.get('id')} (need {repair_cost}c, "
                f"have {remaining}c)",
            )
            continue
        actions.append({"a": "repair", "unit": str(harv.get("id"))})
        remaining -= repair_cost
        descriptors.append(f"repaired {harv.get('id')} ({repair_cost}c)")

    # Priority 2: build a new harvester if under cap and affordable.
    if cap_used >= cap_max:
        descriptors.append(
            f"skipped harvester build (fleet at cap {cap_used}/{cap_max})",
        )
    elif remaining >= harvester_cost:
        actions.append({"a": "build_harvester"})
        remaining -= harvester_cost
        descriptors.append(f"built harvester ({harvester_cost}c)")
    else:
        descriptors.append(
            f"deferred harvester build (need {harvester_cost}c, "
            f"have {remaining}c)",
        )

    # Priority 3: WEAPONS ECONOMY. Tiered on rolled-up BLUE:
    #   • blue > 300 → ALWAYS build. Fill the two-weapon kit: CHAFF first
    #     when we hold none (the hour 5-7 / 11-13 egress jam), else an EMP,
    #     else whichever is still affordable.
    #   • blue > 250 → 50% roll for an EMP (beacon denial).
    # EMP stays capped at a small stockpile so we don't hoard salvos we
    # never fire — which is exactly the failure V12 ships with, and the
    # reason this cap is worth keeping rather than tuning up.
    # v1.34 — the arsenal ceiling (RULEBOOK §4.9.8). Threaded through the
    # affordability helpers so every branch below inherits it, rather
    # than bolted onto each one. At most one weapon is queued per orbit,
    # so the held figure does not need to move mid-plan.
    weapon_blue_cap = int(
        ((view.get("meta") or {}).get("rules") or {}).get(
            "weapon_blue_cap", _DEFAULT_WEAPONISED_BLUE_CAP
        )
    )
    held_weapon_blue = emp_stock * emp_blue_cost + chaff_stock * chaff_blue_cost

    def _room_for(blue_cost: int) -> bool:
        return held_weapon_blue + blue_cost <= weapon_blue_cap

    def _afford_emp() -> bool:
        return (
            blue_total >= emp_blue_cost
            and remaining >= emp_credit_cost
            and _room_for(emp_blue_cost)
        )

    def _afford_chaff() -> bool:
        return (
            blue_total >= chaff_blue_cost
            and remaining >= chaff_credit_cost
            and _room_for(chaff_blue_cost)
        )

    if weapons_enabled and not _room_for(min(emp_blue_cost, chaff_blue_cost)):
        # Say the cap out loud rather than letting it read as "cannot
        # afford" — an agent at the ceiling with a full wallet is a
        # different situation, and the descriptor is what a fork reads
        # when it wonders why its build never fired.
        descriptors.append(
            f"weapon build skipped (holding {held_weapon_blue} of the "
            f"{weapon_blue_cap} blue arsenal cap)"
        )
    elif weapons_enabled and blue_total > _BLUE_ALWAYS_BUILD_THRESHOLD:
        if chaff_stock < 1 and _afford_chaff():
            actions.append({"a": "build_chaff", "count": 1})
            remaining -= chaff_credit_cost
            descriptors.append(
                f"built CHAFF for egress jam (blue {blue_total} > "
                f"{_BLUE_ALWAYS_BUILD_THRESHOLD})"
            )
        elif emp_stock < 2 and _afford_emp():
            actions.append({"a": "build_emp", "count": 1})
            remaining -= emp_credit_cost
            descriptors.append(
                f"built EMP (blue {blue_total} > {_BLUE_ALWAYS_BUILD_THRESHOLD})"
            )
        elif _afford_chaff():
            actions.append({"a": "build_chaff", "count": 1})
            remaining -= chaff_credit_cost
            descriptors.append("built CHAFF (blue surplus top-up)")
        elif _afford_emp():
            actions.append({"a": "build_emp", "count": 1})
            remaining -= emp_credit_cost
            descriptors.append("built EMP (blue surplus top-up)")
        else:
            descriptors.append(
                f"weapon build wanted (blue {blue_total}) but unaffordable "
                f"(have {remaining}c)"
            )
    elif (
        weapons_enabled
        and blue_total > _BLUE_EMP_THRESHOLD
        and emp_stock < 2
        and _afford_emp()
    ):
        emp_rng = _seat_day_rng(view, _seat_of(view), salt="emp-build")
        if emp_rng.random() < 0.5:
            actions.append({"a": "build_emp", "count": 1})
            remaining -= emp_credit_cost
            descriptors.append(
                f"built EMP (blue {blue_total} > {_BLUE_EMP_THRESHOLD}, "
                f"50% roll hit)"
            )
        else:
            descriptors.append(
                "skipped EMP build (blue surplus but 50% roll missed)"
            )

    # Priority 4: keep the probe magazine topped up. Hot-drops
    # (RULEBOOK §3.9.7 live-only) spend a probe to SECURE each harvester
    # landing on a vein without live coverage, on top of the 2 exploration
    # probes a night. A flat "build 2" magazine ran dry and left
    # harvesters unable to land, so top up toward a working magazine in
    # one batched build, bounded by credits and current stock.
    current_probe_stock = int(orbit.get("probe_stock", 0) or 0)
    want = max(0, _PROBE_TARGET_STOCK - current_probe_stock)
    affordable = remaining // probe_cost if probe_cost > 0 else 0
    build_n = min(want, affordable)
    if build_n >= 1:
        actions.append({"a": "build_probe", "count": int(build_n)})
        remaining -= build_n * probe_cost
        descriptors.append(
            f"built {build_n} probe(s) ({build_n * probe_cost}c) — "
            f"stock {current_probe_stock}→{current_probe_stock + build_n}"
        )
    elif want <= 0:
        descriptors.append(
            f"probe magazine full (stock {current_probe_stock}≥"
            f"{_PROBE_TARGET_STOCK})"
        )
    else:
        descriptors.append(
            f"deferred probe build (need {probe_cost}c, have {remaining}c)",
        )

    rationale = (
        f"orbit day plan ({credits}c available): "
        + "; ".join(descriptors)
        + f". Carryover {remaining}c."
    )
    return actions, rationale


# ── Night-phase planner ───────────────────────────────────────────


def plan_moves(
    view: Dict[str, Any],
    *,
    weapons_enabled: bool = True,
) -> Tuple[List[Dict[str, Any]], str]:
    """Return ``(moves, rationale)`` for the structured agent view.

    v0.9.5 — plans a chain for EVERY healthy harvester the seat
    owns, not just the first one. Targets are claimed across
    harvesters so two units never try to harvest the same RED
    cell. Surplus harvesters without a visible RED target push
    into fog so their LoS disks reveal new ground.

    ``weapons_enabled=False`` (RED_HARVEST_LITE, the hackathon
    tutorial opponent) skips the EMP salvo, the blue-seeking-to-fund-
    weapons diversion, and the chaff egress jam — harvesters just
    harvest RED/BLUE on their own merits and probes just scout.

    Pure function — no I/O. Lets the caller decide whether to
    actually submit the queue or just preview it.
    """
    harvesters = _my_harvesters(view)
    moves: List[Dict[str, Any]] = []
    descriptors: List[str] = []

    # v0.9.10 — spend a built EMP on the opponent's LATEST probe
    # BEACON, fanning the salvo wide to blanket the area the enemy
    # just lit up (denying their fresh vision). ``weapon_stock`` rides
    # on the orbit block (present on the night view too). The launch
    # fires up to ``missiles_per_launch`` simultaneous clouds; we
    # spread the target cells so the radius-2 clouds tile a broad
    # blanket instead of stacking. Falls back to the nearest enemy
    # harvester when no beacon is on the board. The clouds are
    # friendly-fire (RULEBOOK §5.0), so we record their footprint in
    # ``emp_zone`` and keep our own harvester/probe drops out of it
    # for the rest of the night.
    grid = view.get("grid") or {}
    g_w = int(grid.get("width") or view.get("grid", {}).get("width") or 0)
    g_h = int(grid.get("height") or view.get("grid", {}).get("height") or 0)
    orbit = view.get("orbit") or {}
    emp_stock = int((orbit.get("weapon_stock") or {}).get("emp", 0) or 0)
    emp_specs = (orbit.get("weapon_specs") or {}).get("emp") or {}
    emp_radius = int(emp_specs.get("radius", _DEFAULT_EMP_RADIUS))
    emp_missiles = int(
        emp_specs.get("missiles_per_launch", _DEFAULT_EMP_MISSILES)
    )
    emp_zone: Set[Tuple[int, int]] = set()
    if weapons_enabled and emp_stock > 0 and g_w > 0 and g_h > 0:
        aim = _latest_enemy_beacon(view)
        aim_kind = "beacon"
        if aim is None:
            enemy_targets = _enemy_harvester_targets(view)
            if enemy_targets:
                aim = enemy_targets[0]
                aim_kind = "harvester"
        if aim is not None:
            targets = _emp_spread_targets(
                aim,
                count=max(1, emp_missiles),
                radius=emp_radius,
                width=g_w,
                height=g_h,
            )
            moves.append({"a": "emp_launch", "at": targets})
            emp_zone = _emp_cloud_cells(
                targets, radius=emp_radius, width=g_w, height=g_h
            )
            descriptors.append(
                f"EMP salvo: {len(targets)} missile(s) fanned over enemy "
                f"{aim_kind}@{list(aim)} — {len(emp_zone)}-cell no-drop zone."
            )

    # v0.9.9 — blue economy top-up. When the rolled-up BLUE purity is
    # low, surface BLUE tiles as a FALLBACK harvest target. RED always
    # wins: a harvester only reaches for BLUE when it has no reachable
    # RED left to claim (otherwise it would idle / fog-scout anyway),
    # so RED runs are never undercut.
    # v1.x — SURPLUS-harvester blue seeking. Even when blue is above the
    # top-up threshold, if the seat runs 2+ harvesters and blue hasn't
    # reached the weapon-funding cap (``_BLUE_WEAPON_TARGET``), offer BLUE
    # tiles so the designated surplus unit (see ``blue_seeker_idx`` below)
    # can farm weapons currency toward EMP/chaff builds.
    blue_total = _blue_purity_total(view)
    blue_seek = (
        weapons_enabled
        and len(harvesters) >= 2
        and blue_total < _BLUE_WEAPON_TARGET
    )
    blue_tiles: List[Dict[str, Any]] = []
    if blue_total <= _LOW_BLUE_THRESHOLD or blue_seek:
        blue_tiles = list(view.get("blue_tiles") or [])

    # v0.9.11 — GREEN is pure liability (toxic-legacy penalty at season
    # end, RULEBOOK §4.5) and the only cheap way out is the contested
    # solar catapult, so the best green is green we never harvest. The
    # engine harvests EVERY tile a harvester steps onto (``_harvest_at``
    # fires per drop AND per step), so we route harvesters around known
    # GREEN cells: they're added to the per-harvester no-step/no-drop
    # set alongside the EMP cloud. Best-effort — a green tile boxed
    # between the harvester and a RED target just means the chain stops
    # short rather than banking the green ("can't be helped sometimes").
    green_cells: Set[Tuple[int, int]] = {
        (int(g["x"]), int(g["y"]))
        for g in (view.get("green_tiles") or [])
        if g.get("x") is not None and g.get("y") is not None
    }
    harvester_avoid = emp_zone | green_cells

    # v0.9.18 — shared probe budget for this night. Hot-drops (securing
    # probes paired with a harvester landing) draw FIRST since a secured
    # harvest beats a speculative scout; whatever is left funds the
    # fog/RED-bracket exploration probes below. Threaded as a 1-element
    # list so the per-harvester planner can decrement it in place.
    # Stripped/legacy views that don't stamp ``orbit.probe_stock`` get a
    # permissive budget so the long tail of agent tests (and any caller
    # that omits the stock) keep planning the usual probe drops.
    _orbit_blk = view.get("orbit")
    if isinstance(_orbit_blk, dict) and "probe_stock" in _orbit_blk:
        probe_budget = [int(_orbit_blk.get("probe_stock", 0) or 0)]
    else:
        probe_budget = [MAX_PROBES_PER_TURN + len(harvesters) + 1]

    if not harvesters:
        descriptors.append("No harvester deployed; probe-only night.")
    else:
        # Each harvester pops from this shared list as it claims
        # targets so two units don't fight over the same cell. We
        # COPY the list because :func:`_walk_and_harvest` mutates it.
        shared_red = list(view.get("red_tiles") or [])
        if blue_tiles:
            why = (
                "blue seek for weapons" if blue_seek
                else f"low blue ({blue_total}≤{_LOW_BLUE_THRESHOLD})"
            )
            descriptors.append(
                f"BLUE available ({why}): {len(blue_tiles)} tile(s) as "
                f"fallback targets."
            )
        claimed: Set[Tuple[int, int]] = set()
        avoid_drops: List[Tuple[int, int]] = []

        # Surface harvesters first (they hold committed positions, so they
        # get first crack at nearby RED), then orbital ones. v1.x — the LAST
        # healthy unit in this order is the "surplus" harvester: with 2+
        # harvesters and blue below the weapon cap it prefers BLUE over
        # low-tier RED (2 harvesters → 1 RED + 1 blue; 3 → 2 RED + 1 blue).
        surface_harvs = [h for h in harvesters if h.get("pos") is not None]
        orbital_harvs = [h for h in harvesters if h.get("pos") is None]
        plan_order = surface_harvs + orbital_harvs
        healthy_idxs = [
            i for i, h in enumerate(plan_order) if not h.get("damaged")
        ]
        blue_seeker_idx = (
            healthy_idxs[-1] if (blue_seek and healthy_idxs) else -1
        )
        for i, harv in enumerate(plan_order):
            chain, desc = _plan_one_harvester(
                view, harv, shared_red, claimed, avoid_drops,
                blue_tiles=blue_tiles, avoid_cells=harvester_avoid,
                probe_budget=probe_budget,
                blue_preferred=(i == blue_seeker_idx),
            )
            moves.extend(chain)
            descriptors.append(desc)

    # Probe drops fill the remaining policy budget. We DON'T feed
    # the harvester drop list into ``avoid_drops`` — harvester +
    # probe on the same cell aren't a collision concern (the probe
    # would crush issue only matters when a NEW harvester drops
    # onto an existing PROBE, which the engine handles), and
    # treating them as collisions starves the probe planner of
    # cluster anchors when the harvester is already pushing into
    # the same cluster (the v0.9.5 fog-edge anchor case).
    # v0.9.18 — exploration probes get whatever stock the hot-drops
    # didn't claim (``probe_budget`` was decremented in place above),
    # capped at the per-turn soft limit.
    probes = _plan_probe_drops(
        view,
        max_probes=max(0, min(MAX_PROBES_PER_TURN, probe_budget[0])),
        avoid_drops=sorted(emp_zone),
    )
    moves.extend(probes)
    if probes:
        clusters_used = [m["at"] for m in probes]
        descriptors.append(
            f"Dropped {len(probes)} probe(s) on fog clusters at {clusters_used}."
        )
    else:
        descriptors.append("No fog clusters worth probing.")

    # v1.x — CHAFF egress jam (RULEBOOK §5). If we hold chaff, fire one at a
    # random hour inside an egress window (5-7 / 11-13) — the tail of each
    # shift when rival harvesters are lifting off — so the jam cancels a
    # loaded harvester's pickup and strands it for the dawn destruction.
    # Night actions are scheduled by QUEUE POSITION (one applied move per
    # hour, RULEBOOK §3.10), so we splice the flare in at index target-1,
    # padding with WAITs when the queue is shorter than the target hour.
    # (Firing self-jams our own house for CHAFF_DURATION_HOURS-1 carry-over
    # hours, which is why the windows are chosen around enemy egress.)
    chaff_stock = int((orbit.get("weapon_stock") or {}).get("chaff", 0) or 0)
    if weapons_enabled and chaff_stock > 0:
        crng = _seat_day_rng(view, _seat_of(view), salt="chaff")
        window = crng.choice(_CHAFF_EGRESS_WINDOWS)
        target_hour = crng.randint(window[0], window[1])
        idx = max(0, target_hour - 1)
        while len(moves) < idx:
            moves.append({"a": "wait"})
        moves.insert(idx, {"a": "chaff_flare"})
        descriptors.append(
            f"CHAFF flare scheduled @ hour {target_hour} (egress jam window "
            f"{window[0]}-{window[1]})."
        )

    rationale = " ".join(descriptors)
    return moves, rationale


@dataclass
class HeuristicAgent:
    """RED_HARVEST — the deterministic, in-process Sea of Colours agent.

    Wraps :func:`plan_moves` and :func:`plan_orbit_actions` behind
    the same ``play(view)`` shape used by the Snowflake Cortex
    invoker so the runtime layer can swap the two transparently.

    Dispatch:

    * If the view's ``phase`` is ``"orbit"`` the agent returns a
      list of Orbit-action dicts under ``"orbit_actions"`` plus an
      empty ``"moves"`` queue. The runtime hands those to
      :func:`sea_of_colours.snowpark.engine.submit_orbit_actions`.
    * Otherwise it returns the night-action ``"moves"`` queue.

    ``weapons_enabled=False`` produces **RED_HARVEST_LITE**: the same
    deterministic playbook with every chaff/EMP decision point skipped
    (RULEBOOK-adjacent hackathon variant — see ``docs/``). It never
    builds or fires a weapon, making it the easy first opponent in the
    onboarding guide; the default ``RED_HARVEST`` (weapons_enabled=True)
    remains the competitive baseline.
    """

    name: str = "RED_HARVEST"
    weapons_enabled: bool = True

    def play(self, view: Dict[str, Any]) -> Dict[str, Any]:
        phase = str(view.get("phase") or view.get("meta", {}).get("phase") or "")
        if phase == "orbit":
            actions, rationale = plan_orbit_actions(
                view, weapons_enabled=self.weapons_enabled,
            )
            return {
                "moves": [],
                "orbit_actions": actions,
                "rationale": rationale,
                "tool_calls": [
                    {"name": "soc_get_view", "args": {"echo": "in-process"}},
                    {
                        "name": "soc_submit_orbit_actions",
                        "args": {"action_count": len(actions)},
                    },
                ],
            }
        moves, rationale = plan_moves(view, weapons_enabled=self.weapons_enabled)
        return {
            "moves": moves,
            "orbit_actions": [],
            "rationale": rationale,
            "tool_calls": [
                {"name": "soc_get_view", "args": {"echo": "in-process"}},
                {"name": "soc_submit_policy", "args": {"move_count": len(moves)}},
            ],
        }
