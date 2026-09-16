"""Agent-friendly grid view payload — used by SOC_GET_VIEW.

v0.7.0 reframe: the payload is now a structured JSON tactical
briefing — no more ASCII grid for the agent to character-count.
Sections (top-level keys):

* ``meta``      — season, day, phase, player, policy_actions_left/max.
* ``hud``       — score + hoard + shipped with ``free``, ``pct_full``,
  ``warning`` strings naming the vault tier ladder (§3.14) when ≥ 80%.
* ``last_night``— structured recap of yesterday: ``my_orders`` with
  ``outcome:"ok"|"illegal"`` + reason, ``my_assets_destroyed``,
  ``my_parcels_banked``.
* ``competitor_intel`` — fresh enemy activity (probe launches via §3.15
  orbital publicity; harvester trails witnessed by this player) and
  older ``persistent_echoes``.
* ``world``     — :meth:`GameSession.agent_dense_view` — partitioned
  ``live[]`` / ``echo[]`` arrays of logical cells (x, y, tile, purity,
  square_id, lineage, trails, entity), plus ``fog_count`` for the
  filter-out-fog contract.
* ``navigation``— ``best_red_visible`` / ``best_red_echo`` sorted by
  value; ``fog_clusters`` for unexplored regions.
* ``my_assets`` — every asset the player has owned this season
  (orbit / deployed / destroyed) with lifetime stats.

Back-compat top-level keys (``red_tiles``, ``green_tiles``,
``blue_tiles``, ``fog_clusters``, ``entities``, ``entity_detail``,
``recent_log``, ``grid``) are still emitted for any external consumer
that pre-dates v0.7.0.

Calling code (in :mod:`sea_of_colours.snowpark.engine`) is responsible
for fetching ``recent_log`` from the store — keeping the view builder
storage-agnostic.
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from sea_of_colours.game.policy import MAX_MOVES
from sea_of_colours.game.session import (
    GREEN_ENDGAME_PENALTY,
    GameSession,
    HARVESTER_BUILD_COST,
    HARVESTER_MAX_PER_PLAYER,
    HOARD_CAPACITY,
    PROBE_BUILD_COST,
    PlayerId,
    RED_QUALITY_MULTIPLIER,
    REPAIR_COST,
    SHIPPED_CAPACITY,
    _xy_key,
    cast_player,
)
from sea_of_colours.game.tuning import (
    live_only_drops as _live_only_drops,
    probe_lifetime_nights as _probe_lifetime_nights,
    probe_vision_radius as _probe_vision_radius,
)
from sea_of_colours.generator import Tile
from sea_of_colours.render import RED_LEVEL_NAMES, red_level


def _active_rules(sess: Any = None) -> Dict[str, Any]:
    """Snapshot of the ruleset in force, for the view meta block.

    Most knobs here are process-level tuning. ``weapons_enabled`` and
    ``signs_enabled`` (v1.32) are per-GAME, so they are read off the
    session when one is supplied and default to on when it is not — this
    function is also called from paths that have no session in hand.

    These are published rather than silently enforced on purpose. Agents
    are told what is legal; an agent that proposes an EMP every turn in a
    weapons-off game and has it eaten by the sanitiser has spent its whole
    move budget and looks broken (see ``GameSession.weapons_enabled``).
    """
    from sea_of_colours.game.weapons import (
        BLUE_COST_BY_KIND,
        WEAPONISED_BLUE_CAP,
    )

    # v1.36 — prices and cap come off the SESSION when there is one,
    # because they are stamped per game. Publishing the live module
    # constants against an archived season is what makes its arsenal
    # bar walk mid-night: the client prices fired weapons off this
    # table, so a chaff bought at 255 and priced at 300 empties a rack
    # that was never that full. The module values are the fallback for
    # the callers that genuinely have no session in hand.
    cap = WEAPONISED_BLUE_CAP
    costs = BLUE_COST_BY_KIND
    if sess is not None and hasattr(sess, "weapon_prices"):
        cap = sess.arsenal_cap()
        costs = sess.weapon_prices()

    return {
        "drop_mode": "live_only" if _live_only_drops() else "live_or_echo",
        "probe_radius": _probe_vision_radius(),
        "probe_lifetime_nights": _probe_lifetime_nights(),
        "weapons_enabled": bool(getattr(sess, "weapons_enabled", True)),
        "signs_enabled": bool(getattr(sess, "signs_enabled", True)),
        # v1.34 — published for the same reason weapons_enabled is: an
        # agent that proposes a fourth EMP and has it refused has spent
        # move budget on a rule it was never told (§4.9.8).
        "weapon_blue_cap": int(cap),
        # v1.35 — the price list behind that cap. The client needs it to
        # say what a rack is worth mid-night (a fired EMP moves the bar
        # the instant it launches), and a mirrored table is the only way
        # to do that without a second copy of the prices in JS drifting
        # the day someone retunes one.
        "weapon_blue_costs": dict(costs),
    }


# Per-parcel score cap (mirrors GameSession._parcel_purity clamp at 255).
# Exposed as ``value`` on every RED tile row so the agent reasons in
# score units directly, not in raw purity. Today ``value == purity``,
# but exposing both pre-buys us the abstraction for future scoring
# changes (decay, shipped-vault multipliers, quotas).
_PARCEL_VALUE_CAP = 255


def _red_tier_name(purity: int) -> str:
    """Discrete RED concentration tier name (see RULEBOOK §2.2)."""
    return RED_LEVEL_NAMES[red_level(int(purity)) - 1]


def _red_value(purity: int) -> int:
    """Score contribution if this RED square ends up in the vault today."""
    return max(0, min(_PARCEL_VALUE_CAP, int(purity)))


# Single-glyph ASCII map alphabet (chosen so colour-blind / LLM
# tokenisation isn't ambiguous):
ASCII_FOG = "."
ASCII_STALE = ","
ASCII_EMPTY = "_"
ASCII_GREEN = "g"
ASCII_RED = "R"
ASCII_BLUE = "b"
ASCII_HARVESTER_MINE = "X"
ASCII_HARVESTER_ENEMY = "x"
ASCII_PROBE_MINE = "o"
ASCII_PROBE_ENEMY = ":"
ASCII_ORBLIFT = "L"  # only used on the visible cell when an orblift visits


def _tile_to_ch(tile: Tile) -> str:
    if tile == Tile.RED:
        return ASCII_RED
    if tile == Tile.GREEN:
        return ASCII_GREEN
    if tile == Tile.BLUE:
        return ASCII_BLUE
    return ASCII_EMPTY


def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _nearest_harvester(
    sess: GameSession, player: PlayerId
) -> Optional[Tuple[int, int]]:
    best: Optional[Tuple[int, int]] = None
    for e in sess.entities.values():
        if (
            e.owner == player
            and e.entity_type == "harvester"
            and e.x is not None
        ):
            best = (int(e.x), int(e.y))
            break
    return best


def _fog_clusters(
    fog_cells: Set[Tuple[int, int]],
    visible: Set[Tuple[int, int]],
    width: int,
    height: int,
    *,
    max_clusters: int = 8,
) -> List[Dict[str, Any]]:
    """Connected-components on fog. Keep the largest ``max_clusters``."""
    unseen = set(fog_cells)
    clusters: List[Dict[str, Any]] = []
    while unseen:
        seed = next(iter(unseen))
        bag: List[Tuple[int, int]] = []
        frontier: List[Tuple[int, int]] = []
        q: deque[Tuple[int, int]] = deque([seed])
        unseen.discard(seed)
        while q:
            x, y = q.popleft()
            bag.append((x, y))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if (nx, ny) in unseen:
                    unseen.discard((nx, ny))
                    q.append((nx, ny))
                elif (nx, ny) in visible:
                    frontier.append((nx, ny))
        cx = sum(p[0] for p in bag) // max(1, len(bag))
        cy = sum(p[1] for p in bag) // max(1, len(bag))
        anchor: Optional[Tuple[int, int]] = None
        if frontier:
            anchor = min(
                frontier,
                key=lambda p: (p[0] - cx) * (p[0] - cx) + (p[1] - cy) * (p[1] - cy),
            )
        clusters.append({
            "centroid": [cx, cy],
            "size": len(bag),
            "nearest_visible_edge": (
                [anchor[0], anchor[1]] if anchor is not None else None
            ),
        })
    clusters.sort(key=lambda c: -c["size"])
    return clusters[:max_clusters]


def _ascii_map(
    sess: GameSession,
    player: PlayerId,
    visible: Set[Tuple[int, int]],
    echoes: Mapping[str, Mapping[str, Any]],
) -> str:
    """Render a single-character-per-cell ASCII overview."""
    rows: List[str] = []
    entity_at: Dict[Tuple[int, int], str] = {}
    for e in sess.entities.values():
        if e.x is None:
            continue
        if e.owner == player:
            if e.entity_type == "harvester":
                entity_at[(e.x, e.y)] = ASCII_HARVESTER_MINE
            elif e.entity_type == "probe":
                entity_at[(e.x, e.y)] = ASCII_PROBE_MINE
            elif e.entity_type == "orblift":
                entity_at[(e.x, e.y)] = ASCII_ORBLIFT
        else:
            if (e.x, e.y) in visible:
                entity_at[(e.x, e.y)] = (
                    ASCII_HARVESTER_ENEMY
                    if e.entity_type == "harvester"
                    else ASCII_PROBE_ENEMY
                )

    for y in range(sess.height):
        out: List[str] = []
        for x in range(sess.width):
            here = (x, y)
            if here in entity_at:
                out.append(entity_at[here])
                continue
            if here in visible:
                out.append(_tile_to_ch(sess.grid[y][x].tile))
                continue
            k = _xy_key(x, y)
            if k in echoes:
                paint = echoes[k].get("paint", {})
                ch = paint.get("ch", "?")
                if ch in (".", ","):
                    out.append(ASCII_STALE)
                else:
                    out.append(ASCII_STALE)
                continue
            out.append(ASCII_FOG)
        rows.append("".join(out))
    legend = (
        "legend: fog=. stale=, empty=_ green=g red=R blue=b "
        "mine_harvester=X mine_probe=o mine_orblift=L "
        "enemy_harvester=x enemy_probe=:"
    )
    return legend + "\n" + "\n".join(rows)


# ── World-view modes (orchestrator harness, Phase 1) ─────────────────
#
# The agent payload's ``world`` block can be emitted in one of two
# shapes, chosen by the ``SOC_AGENT_WORLD_VIEW`` environment variable.
# This is layer 3 of the four-layer chain (game mechanics → game
# orchestration → harness → agent), and the eval framework drives the
# A/B between them to find the best shape for the agent to consume.
#
#   "list" (default) — current behaviour. ``world`` = {width, height,
#     live[], echo[], fog_count}. Each row carries its own (x, y).
#     The agent reads a flat list and reasons about positions itself.
#
#   "grid"           — new. ``world`` = {width, height, grid[y][x],
#     fog_count}. ``grid`` is a 2D nested array; index by [y][x] gives
#     either ``None`` (fog) or a slim cell dict. The spatial structure
#     of the JSON itself encodes adjacency — no character-counting,
#     no per-cell neighbour blocks.
WORLD_VIEW_LIST = "list"
WORLD_VIEW_GRID = "grid"
WORLD_VIEW_DEFAULT = WORLD_VIEW_LIST


def _world_view_mode() -> str:
    """Read the active world-view mode from the environment.

    Falls back to :data:`WORLD_VIEW_DEFAULT` if the env var is unset
    or set to anything we don't recognise. Centralised here so the
    runtime, eval runner, and tests all agree on what the agent
    payload's ``world`` block will look like.
    """
    raw = os.environ.get("SOC_AGENT_WORLD_VIEW", WORLD_VIEW_DEFAULT)
    mode = (raw or "").strip().lower()
    if mode in (WORLD_VIEW_LIST, WORLD_VIEW_GRID):
        return mode
    return WORLD_VIEW_DEFAULT


def _grid_cell_from_live(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Slim live-cell dict for the 2D grid representation.

    Keeps the fields that drive movement / harvest decisions; drops
    metadata the agent can re-fetch from ``world.live`` (square_id,
    parent_square_id, trail history) in the rare case it needs them.
    Field names match :meth:`GameSession.agent_dense_view`'s ``live[]``
    shape so the agent's mental model stays consistent across modes.
    """
    cell: Dict[str, Any] = {"tile": row.get("tile")}
    tile = row.get("tile")
    if tile == "RED":
        # RED is the only colour with a meaningful purity gradient
        # (trace → vein → mass → pure). Surface both ``purity`` (raw)
        # and ``value`` (score-units, clamped to 255) so the agent
        # can sort directly by what it gets banked.
        cell["purity"] = int(row.get("purity", 0))
        cell["value"] = int(row.get("value", cell["purity"]))
    elif tile in ("GREEN", "BLUE"):
        # Surface purity for completeness; both score zero (GREEN) or
        # near-zero (BLUE is the lowest tier) so it's mainly diagnostic.
        if "purity" in row:
            cell["purity"] = int(row["purity"])
    # Synthetic green = previously-harvested RED. Banking it scores
    # zero — flag loud so the agent routes around without diving
    # into the lineage field.
    if tile == "GREEN" and row.get("lineage") == "synthetic":
        cell["synthetic"] = True
    # Entity occupants (harvester / probe / orblift). Slimmer than
    # the full live[] entity payload: just kind + owner is enough
    # for "can I step here / is this enemy / is this mine".
    ent = row.get("entity")
    if isinstance(ent, Mapping):
        cell["entity"] = {"kind": ent.get("kind"), "owner": ent.get("owner")}
        if "carrying" in ent:
            cell["entity"]["carrying"] = ent["carrying"]
    # Collision scar — 1-day persistence; relevant for "is this cell
    # safe to drop a harvester on".
    if row.get("collision"):
        cell["collision"] = True
    # v1.8 — EMP scar (public §5.1): an EMP cloud was active here last
    # night for ``hours``. Kept in grid mode so a spatial agent can route
    # around a recently-interdicted corridor. Same shape as world.*[].combat.
    if row.get("combat"):
        cell["combat"] = row["combat"]
    return cell


def _grid_cell_from_echo(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Slim echo-cell dict for the 2D grid representation.

    Same shape as :func:`_grid_cell_from_live` plus an ``echo: True``
    flag and (optionally) ``last_seen_day``. The agent treats these
    as "what I knew was there" — tile/purity may be stale.
    """
    cell = _grid_cell_from_live(row)
    cell["echo"] = True
    last_seen = row.get("last_seen_day")
    if isinstance(last_seen, int):
        cell["last_seen_day"] = last_seen
    return cell


def _render_world_grid(world_block: Mapping[str, Any]) -> Dict[str, Any]:
    """Project the dense agent_dense_view into a 2D nested array.

    Returns ``{width, height, grid, fog_count}`` where ``grid`` is a
    ``height × width`` list of lists. Each cell is either ``None``
    (fog — never seen) or a slim dict produced by
    :func:`_grid_cell_from_live` / :func:`_grid_cell_from_echo`.

    The 2D structure IS the spatial representation: ``grid[y][x]`` is
    the cell at (x, y), ``grid[y][x+1]`` is its east neighbour, and
    so on. This is the "JSON board" variant of the world view —
    paired with :data:`SOC_RED_REAPER_GRID` in Phase 1 of the
    orchestrator-config search.
    """
    width = int(world_block.get("width") or 0)
    height = int(world_block.get("height") or 0)
    grid: List[List[Optional[Dict[str, Any]]]] = [
        [None for _ in range(width)] for _ in range(height)
    ]
    for row in world_block.get("live") or []:
        x = int(row["x"])
        y = int(row["y"])
        if 0 <= x < width and 0 <= y < height:
            grid[y][x] = _grid_cell_from_live(row)
    for row in world_block.get("echo") or []:
        x = int(row["x"])
        y = int(row["y"])
        if 0 <= x < width and 0 <= y < height:
            # v0.9.7 — probe-launch markers are "probe-only" fog
            # rows (§3.15). They never carry tile / purity, so the
            # slim grid cell would have ``tile=None``; skip them
            # here and let the cell stay fog (``None``). The
            # opposing seat's frontend renders the probe glyph from
            # the dense-cell path separately.
            if row.get("via") == "probe_launch" and "tile" not in row:
                continue
            # Live wins over echo (we may have a cell visible NOW that
            # also has a stale echo entry from an earlier day).
            if grid[y][x] is None:
                grid[y][x] = _grid_cell_from_echo(row)
    return {
        "width": width,
        "height": height,
        "grid": grid,
        "fog_count": int(world_block.get("fog_count") or 0),
    }


def build_agent_view(
    sess: GameSession,
    player: PlayerId,
    recent_log: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the structured agent view payload for SOC_GET_VIEW.

    v0.9.6 — when ``sess.visibility_mode == "open"`` the seat's
    percept is treated as the whole grid (OBS-style omniscience).
    Echo intel and fog logic still run as data-prep so downstream
    fields like ``stale_red_targets`` keep their shape, but every
    cell counts as currently visible so the agent payload exposes
    the full board to the human operator running an open game.
    """
    pid = (
        cast_player(player, allowed=sess.players)
        if isinstance(player, str)
        else player
    )
    if str(sess.visibility_mode).lower() == "open":
        visible = {
            (xi, yi)
            for yi in range(sess.height)
            for xi in range(sess.width)
        }
    else:
        visible = sess.tiles_visible_now(pid)
    echoes = sess.probe_intel.get(pid, {})
    nearest = _nearest_harvester(sess, pid)

    red_tiles: List[Dict[str, Any]] = []
    green_tiles: List[Dict[str, Any]] = []
    blue_tiles: List[Dict[str, Any]] = []
    visible_counts = {"RED": 0, "GREEN": 0, "EMPTY": 0, "BLUE": 0}
    fog_cells: Set[Tuple[int, int]] = set()

    for y in range(sess.height):
        for x in range(sess.width):
            here = (x, y)
            if here not in visible:
                if _xy_key(x, y) not in echoes:
                    fog_cells.add(here)
                continue
            cell = sess.grid[y][x]
            tile_name = cell.tile.name
            visible_counts[tile_name] = visible_counts.get(tile_name, 0) + 1
            sid = (
                sess.ledger.lookup(x, y) if sess.ledger is not None else ""
            )
            if cell.tile == Tile.RED:
                p = int(cell.purity)
                row = {
                    "x": x, "y": y,
                    "purity": p,
                    "tier": _red_tier_name(p),
                    "value": _red_value(p),
                    "freshness": "fresh",
                    "square_id": sid,
                }
                if nearest is not None:
                    row["distance_to_harvester"] = _manhattan(here, nearest)
                red_tiles.append(row)
            elif cell.tile == Tile.GREEN:
                # GREEN can be either natural (from the polar band
                # generator §2.3) or synthetic (a previously-harvested
                # RED that's been poisoned, §3.12 A.5). The active
                # ledger row at this (x,y) carries ``lineage`` so the
                # agent can distinguish without inspecting world-build
                # tile_at_generation.
                lineage = "natural"
                if sess.ledger is not None:
                    rec = sess.ledger.record(x, y)
                    if rec:
                        lineage = str(rec.get("lineage") or "natural")
                row = {
                    "x": x, "y": y,
                    "purity": int(cell.purity),
                    "square_id": sid,
                    "lineage": lineage,
                }
                # Back-compat: callers from v0.5 read ``harvested``
                # as a boolean — synthetic green is the new explicit
                # signal but we still surface the flag for downstream
                # widgets that key on it.
                if lineage == "synthetic":
                    row["harvested"] = True
                green_tiles.append(row)
            elif cell.tile == Tile.BLUE:
                # BLUE tiles surfaced for v0.6.0 — the agent now
                # harvests them too (turning them into EMPTY), and
                # they're the lowest-tier vault asset under §3.14.
                # Same shape as ``red_tiles`` so the agent can sort
                # by ``value`` consistently across colours.
                p = int(cell.purity)
                row = {
                    "x": x, "y": y,
                    "purity": p,
                    "value": _red_value(p),  # parcel cap is colour-agnostic
                    "freshness": "fresh",
                    "square_id": sid,
                }
                if nearest is not None:
                    row["distance_to_harvester"] = _manhattan(here, nearest)
                blue_tiles.append(row)

    # Stale RED from probe / harvester ECHO intel.
    #
    # The visible-cell loop above only sees tiles in *current* LoS, so
    # the moment a harvester returns to orbit and we have no probes on
    # the relevant area, the agent goes blind to every RED tile it had
    # previously revealed. That's the bug that caused RED_HARVEST to
    # loop forever on a "no RED visible — centre drop + probe" fallback
    # even when the player's screen clearly shows RED in the echo
    # layer. Echo-sourced reds carry ``freshness: "stale"`` so the
    # agent can prefer fresh tiles when both are available, but it
    # will still act on stale reds when fresh ones are absent.
    visible_keys = {f"{x}:{y}" for (x, y) in visible}
    for ek, snap in echoes.items():
        if ek in visible_keys:
            continue  # live LoS already covers this one
        try:
            ex, ey = ek.split(":", 1)
            xi, yi = int(ex), int(ey)
        except ValueError:
            continue
        if int(snap.get("tile", -1)) != int(Tile.RED):
            continue
        p = int(snap.get("purity", 0))
        row = {
            "x": xi,
            "y": yi,
            "purity": p,
            "tier": _red_tier_name(p),
            "value": _red_value(p),
            "freshness": "stale",
            "square_id": (
                sess.ledger.lookup(xi, yi) if sess.ledger is not None else ""
            ),
        }
        if nearest is not None:
            row["distance_to_harvester"] = _manhattan((xi, yi), nearest)
        red_tiles.append(row)

    # Sort RED tiles for the agent: fresh first (stale only fills in
    # gaps), then closest, then highest purity. The agent reads the
    # head of the list for its harvest target so the ordering directly
    # shapes the policy.
    red_tiles.sort(
        key=lambda r: (
            0 if r.get("freshness") == "fresh" else 1,
            r.get("distance_to_harvester", 9999),
            -r["purity"],
        ),
    )

    clusters = _fog_clusters(
        fog_cells, visible, sess.width, sess.height,
    )

    mine: List[Dict[str, Any]] = []
    echo_entities: List[Dict[str, Any]] = []
    for e in sess.entities.values():
        if e.owner != pid:
            continue
        row = {
            "id": e.id,
            "type": e.entity_type,
            "pos": [e.x, e.y] if e.x is not None else None,
        }
        if e.entity_type == "harvester":
            row["carrying_red"] = bool(e.carrying_red)
            row["cargo_count"] = len(e.cargo_squares)
            row["lost_last_night"] = bool(e.lost_last_night)
            # v0.9.5 — surface damage state so the heuristic agent
            # (and any LLM agent reading the same view) can queue a
            # REPAIR orbit action without having to dig through
            # ``my_assets`` for the entity record.
            row["damaged"] = bool(getattr(e, "damaged", False))
        if e.entity_type == "orblift":
            row["holds_red"] = bool(e.orbital_cargo_red)
        if e.entity_type == "probe" and e.x is not None:
            _k2 = _probe_lifetime_nights()
            if _k2:
                _rec2 = sess.asset_records.get(e.id)
                _first2 = int(
                    (_rec2.first_deployed_day if _rec2 and _rec2.first_deployed_day is not None
                     else _rec2.created_on_day if _rec2 else 0) or 0
                )
                _used2 = max(0, int(sess.day) - _first2)
                row["nights_remaining"] = max(0, int(_k2) - _used2)
        mine.append(row)
    # Opponent echoes (from probe intel) — last-seen positions of any
    # entities we glimpsed.
    seen_keys: Set[str] = set()
    for k, snap in echoes.items():
        for occ in snap.get("occupants") or []:
            if occ.get("owner") and occ.get("owner") != pid:
                eid = occ.get("id") or f"echo_{k}"
                if eid in seen_keys:
                    continue
                seen_keys.add(eid)
                try:
                    xs, ys = k.split(":", 1)
                    pos = [int(xs), int(ys)]
                except ValueError:
                    pos = None
                echo_entities.append({
                    "id": eid + "?",
                    "type": occ.get("type"),
                    "last_seen_pos": pos,
                })

    entity_detail: Dict[str, Dict[str, Any]] = {}
    for rec in sess.asset_records.values():
        if rec.owner != pid:
            continue
        entity_detail[rec.asset_id] = {
            "asset_type": rec.asset_type,
            "created_on_day": rec.created_on_day,
            "first_deployed_day": rec.first_deployed_day,
            "destroyed_on_day": rec.destroyed_on_day,
            "total_red_harvested": rec.total_red_harvested,
            "total_days_on_surface": rec.total_days_on_surface,
            "last_seen": (
                [rec.last_seen_x, rec.last_seen_y]
                if rec.last_seen_x is not None else None
            ),
        }

    hoard_count = len(sess.hoard_squares[pid])
    shipped_count = len(sess.shipped_squares.get(pid, []))
    hoard_tier_breakdown = _vault_tier_breakdown(sess.hoard_squares[pid])
    shipped_tier_breakdown = _vault_tier_breakdown(
        sess.shipped_squares.get(pid, []),
    )

    # Score is the canonical RED-purity-weighted total (0..255 per parcel;
    # see :meth:`GameSession.score_for`). Both seats are surfaced so the
    # frontend can render a leaderboard without a second roundtrip.
    # v0.7.4 — read the day cap from the SESSION (per-season setting)
    # rather than the module default. Legacy payloads loaded via
    # ``from_dict`` hydrate back to ``SEASON_DAY_CAP`` automatically.
    from sea_of_colours.game.session import SEASON_DAY_CAP  # noqa: F401 (kept for legacy import-by-name callers)
    season_day_cap = int(getattr(sess, "season_day_cap", None) or SEASON_DAY_CAP)
    score_self = sess.score_for(pid)
    # v0.9.6 — N-seat scoreboard: aggregate every opponent so a
    # 3/4-player game has a single ``score_opp_max`` to compare
    # against (UI can drill into ``scores`` for the full ranking).
    other_scores = [
        sess.score_for(p)  # type: ignore[arg-type]
        for p in sess.players
        if p != pid
    ]
    score_opp = max(other_scores) if other_scores else 0
    _ = score_opp  # retained for symmetry / future diff use

    hoard_pct = (hoard_count / HOARD_CAPACITY) if HOARD_CAPACITY else 0.0
    hoard_block = {
        "count": hoard_count,
        "used": hoard_count,
        "capacity": HOARD_CAPACITY,
        "max": HOARD_CAPACITY,
        "free": max(0, HOARD_CAPACITY - hoard_count),
        "pct_full": round(hoard_pct, 3),
        # v0.9.6 — tier-weighted potential score of the RED currently HELD
        # in the hoard (worth 0 until the orbit catapult ships it). Surfaced
        # so the agent can tell "harvested-but-unshipped" (held, safe) apart
        # from "lost cargo" when vault_score hasn't moved after a harvest.
        "red_value": int(hoard_tier_breakdown["RED"]["value"]),
        # Per-tier composition so the agent can reason about
        # whether returning to vault will trigger the §3.14
        # cascade (e.g. "vault has 50 BLUE — incoming RED will
        # auto-displace lowest BLUE; safe to bank").
        "by_tier": hoard_tier_breakdown,
        "warning": _vault_warning(
            hoard_count, HOARD_CAPACITY, hoard_tier_breakdown,
        ),
    }
    # SHIPPED is uncapped (v0.9.x): a permanent public record, not a
    # bounded bay. Surface the cumulative count; capacity fields are
    # ``None`` so the HUD renders a count with no denominator.
    shipped_block = {
        "count": shipped_count,
        "used": shipped_count,
        "capacity": SHIPPED_CAPACITY,
        "max": SHIPPED_CAPACITY,
        "free": None,
        "pct_full": 0.0,
        "uncapped": True,
        "by_tier": shipped_tier_breakdown,
        "warning": None,
    }

    hud = {
        "day": sess.day,
        "phase": sess.phase.value,
        "player": pid,
        # Legacy framing kept for the JS frontend's pre-v0.7.0
        # tooltips. ``meta.policy_actions_max`` is the canonical
        # v0.7.0 surface that the cortex prompt should read.
        "max_moves": MAX_MOVES,
        "hoard": hoard_block,
        "shipped": shipped_block,
        # Win condition (§3.0): each parcel's origin RED purity (0..255)
        # contributes to the player's running score. ``score`` is the
        # self-side total; ``scores`` carries both seats so the watcher
        # frontend can show standings without per-seat refetches.
        "score": score_self,
        "scores": {p: sess.score_for(p) for p in sess.players},
        # Season metadata: human label + remaining-days countdown so the
        # UI can show "DAY 3/5 · Aurora_Falcon" without inferring from
        # the SQL standings view.
        "season_name": sess.season_name,
        "season_day_cap": season_day_cap,
        "is_season_complete": sess.is_season_complete(),
    }

    visible_cells_total = sum(visible_counts.values())
    stale_cells_total = len(echoes) - visible_cells_total
    fog_cells_total = sess.width * sess.height - visible_cells_total - max(0, stale_cells_total)

    # Split the (sorted) RED list into fresh-LoS and echo-only slices —
    # the cortex prompt reads them as ``navigation.best_red_visible``
    # and ``navigation.best_red_echo`` respectively. Same provenance,
    # cleaner mental model than a single mixed list with a
    # ``freshness`` flag.
    best_red_visible = [r for r in red_tiles if r.get("freshness") == "fresh"]
    best_red_echo = [r for r in red_tiles if r.get("freshness") != "fresh"]

    structured_log = list(recent_log or [])
    last_night = _last_night_recap(sess, pid, structured_log)
    competitor = _competitor_intel(sess, pid, structured_log, visible, echoes)
    # v1.38 (§3.15) — the store's log is one shared feed with no owner
    # column, so ``structured_log`` holds every seat's rows. The two
    # calls above are entitled to that: ``last_night`` filters it to this
    # seat and ``competitor_intel`` lifts only ``probe_launch``, which is
    # a public flare. What must NOT go out whole is the feed itself.
    seat_log = _log_for_seat(sess, pid, structured_log)
    station_intel = _station_intel(sess, pid)
    my_assets_block = _my_assets(sess, pid)
    world_block = sess.agent_dense_view(pid)
    # Orchestrator-config A/B switch: the env var picks how we serialise
    # the world for the agent. In "grid" mode, replace the {live, echo}
    # flat lists with a single 2D ``grid[y][x]`` nested array. See
    # :func:`_render_world_grid` for the cell shape and rationale.
    if _world_view_mode() == WORLD_VIEW_GRID:
        world_block = _render_world_grid(world_block)

    policy_used = _policy_actions_used(sess, pid)
    meta = {
        "season": sess.season_name,
        "session_id": sess.session_id,
        "day": int(sess.day),
        "phase": sess.phase.value,
        "player": pid,
        # v0.9.6 — N-seat context for the heuristic agent so it can
        # compute its seat-index rotation. ``players`` is the
        # session's seat list in canonical order; ``agents`` is the
        # per-seat agent assignment (human / red_harvest) so the
        # heuristic can branch on opponent type if it ever wants to.
        "players": list(sess.players),
        "agents": dict(sess.agents),
        "visibility_mode": str(sess.visibility_mode),
        # v1.32 — which teaching preset spawned this game, "" for a real
        # season. Presentation only: it picks the film reel the tutorial
        # modal opens on. What is LEGAL is in ``rules`` below, never here.
        "tutorial": str(getattr(sess, "tutorial", "") or ""),
        "policy_actions_used": policy_used,
        "policy_actions_max": MAX_MOVES,
        "policy_actions_left": max(0, MAX_MOVES - policy_used),
        "policy_budget_note": (
            f"{MAX_MOVES} policy actions per night across your fleet "
            "(e.g. 3 harvesters × (drop + 5 step + pickup)). Each "
            "drop / step / pickup / probe is one action. Per-harvester "
            "step limit is 5 (separate from this fleet-wide cap)."
        ),
        # v0.9.17 — active ruleset knobs surfaced so frontend & agents
        # can branch on the current canonical rules without reading env.
        "rules": _active_rules(sess),
    }
    navigation_block = {
        "best_red_visible": best_red_visible,
        "best_red_echo": best_red_echo,
        "best_green": green_tiles,
        "best_blue": blue_tiles,
        "fog_clusters": clusters,
    }

    # v0.8.0 — Orbit phase context. Surfaces the economy / inventory
    # state the agent (or human player) needs to bid the catapult
    # and decide whether to build / repair / refine. Always emitted
    # so the watcher UI doesn't have to branch on phase to find it.
    own_hoard = sess.hoard_squares.get(pid, [])
    green_owned = [p for p in own_hoard if int(p.get("tile_at_harvest", 0)) == 1]
    green_owned_purity_total = sum(
        max(0, min(255, int(p.get("purity_at_harvest", p.get("origin_purity", 0)) or 0)))
        for p in green_owned
    )
    last_catapult: Dict[str, Any] = {}
    last_catapult_summary: Dict[str, Any] = {}
    if sess.catapult_history:
        last_catapult = dict(sess.catapult_history[-1])
        # v0.9.1 — flatten the public ``inventory`` blocks into a
        # tight summary the agent prompt can render verbatim. Falls
        # back to the empty-inventory shape if the section is missing
        # (e.g. older sessions persisted before the stamp was added).
        def _inv(section_key: str) -> Dict[str, Any]:
            sec = last_catapult.get(section_key) or {}
            return sec.get("inventory") or {
                "count": 0, "total_purity": 0,
                "color_counts": {"red": 0, "green": 0, "blue": 0},
                "tier_counts": {"trace": 0, "vein": 0, "mass": 0, "pure": 0},
            }
        last_catapult_summary = {
            "day": int(last_catapult.get("day", 0)),
            # v0.9.6 — section keys are ``catapult`` + ``jettison``.
            # Legacy 2-seat sessions persisted before v0.9.6 carried
            # ``auction`` / ``tithe`` sections; ``_inv`` returns the
            # empty-inventory shape for missing keys so old replays
            # render as "nothing shipped" instead of crashing.
            "catapult": _inv("catapult"),
            "jettison": _inv("jettison"),
        }
    # v0.9.1 — surface the seat's RED tier counts so the Orbit panel
    # can decide which refine button to enable (and the agent prompt
    # can decide whether to queue a refine action at all).
    tier_counts = sess.red_tier_counts(pid)
    # v0.9.5 — same shape for BLUE so the orbit panel can render a
    # "BLUE TIERS" readout next to RED, and the agent knows how
    # much blue is sittin in each band before queueing a weapon
    # build. The frontend renders the user-facing tier names
    # (shallow / mid / sink / deep) from these keys.
    blue_tier_counts = sess.blue_tier_counts(pid)
    blue_purity_total = sess.blue_purity_available(pid)
    # v0.9.3 — current weapon-stockpile counts surfaced so the
    # orchestrator can reason about whether a planned launch will
    # actually fire ("I have 0 EMPs and the prompt told me to
    # launch one — must build first"). Defensive ``.get`` chain so
    # a hand-edited or legacy session that's missing one of the
    # sub-keys still serialises as a clean ``{emp, chaff}``.
    #
    # v1.36 — keyed off the game's own price table rather than a literal
    # pair, so a weapon added to (or withdrawn from) ``BLUE_COST_BY_KIND``
    # appears here without an edit. A seat with no stock for a kind still
    # gets an explicit zero: an absent key reads to an agent as "this
    # weapon does not exist", which is a different claim.
    weapon_stock_seat = sess.weapon_stock.get(pid, {}) or {}
    blue_prices = sess.weapon_prices()
    weapon_stock_block = {
        kind: int(weapon_stock_seat.get(kind, 0) or 0)
        for kind in blue_prices
    }
    # v1.36 — both halves of the price come off ``weapons.py`` tables.
    # The credit half used to be a literal here, and the literal is
    # exactly how SNAP shipped published at 0 credits while the engine
    # charged 250: a buy panel that reads this view quoted a price the
    # orbit resolver then refused.
    from sea_of_colours.game.weapons import CREDIT_COST_BY_KIND, SPEC_BY_KIND

    weapon_prices_block = {
        kind: {
            "blue": int(blue),
            "credits": int(CREDIT_COST_BY_KIND.get(kind, 0)),
        }
        for kind, blue in blue_prices.items()
    }
    # v0.9.x — live weapon mechanics so the frontend targeting UI and
    # tooltips read the real dials (EMP salvo size / radius, chaff
    # window) instead of hard-coding them. (v1.31 — the mine cluster
    # shape went with the caltrop.) Filtered against the game's own
    # price list so a weapon this season does not sell cannot advertise
    # its specs, which is what keeps a withdrawal a one-line change.
    weapon_specs_block = {
        kind: dict(SPEC_BY_KIND[kind])
        for kind in blue_prices
        if kind in SPEC_BY_KIND
    }
    # v0.9.6 — surface the seat's own hoard parcels so the orbit-phase
    # planner (heuristic or cortex) can reason about its top-N RED
    # picks at ship-time. The catapult auto-picks highest-purity RED
    # so the agent doesn't need to name parcel ids any more, but the
    # tooltip + scoreboard still want each row to carry its tier and
    # the score it would earn IF shipped today (raw_purity × multiplier).
    # Public to self only — hoard provenance is private (§3.15).
    hoard_parcels: List[Dict[str, Any]] = []
    for parcel in own_hoard:
        sq_id = parcel.get("square_id") or parcel.get("site_id")
        if not sq_id:
            continue
        tile_code = int(parcel.get("tile_at_harvest", 0) or 0)
        try:
            colour = Tile(tile_code).name
        except (ValueError, KeyError):
            colour = "RED"
        purity = max(
            0,
            min(
                255,
                int(
                    parcel.get("purity_at_harvest",
                               parcel.get("origin_purity",
                                          parcel.get("purity", 0))) or 0
                ),
            ),
        )
        tier = _red_tier_name(purity) if colour == "RED" else ""
        mult = float(RED_QUALITY_MULTIPLIER.get(tier, 1.0)) if tier else 0.0
        score = int(round(purity * mult)) if colour == "RED" else 0
        hoard_parcels.append({
            "square_id": str(sq_id),
            "colour": colour,
            "purity": purity,
            # v0.9.6 — tier-multiplier scoring readout (RULEBOOK §3.1).
            # ``tier`` is the discrete RED bucket the parcel falls in;
            # ``score`` is what ``score_for`` will add IF this parcel
            # ships today with no cannibalisation. Frontends render
            # ``score = purity × MULT[tier]`` in the vault tooltip and
            # the catapult composer's net-yield estimate.
            "tier": tier,
            "score": score,
        })

    final_orbit_flag = bool(getattr(sess, "final_orbit", False))

    orbit_block = {
        "phase_active": sess.phase.value == "orbit",
        # Terminal settlement orbit marker. v1.13 — this no longer changes
        # what a seat may do (the refinery run it used to unlock is gone);
        # it only tells a consumer the season ends after this settlement,
        # so buying now is throwing credits away.
        "final_orbit": final_orbit_flag,
        "credits": int(sess.credits.get(pid, 0)),
        "credits_all": dict(sess.credits),
        "probe_stock": int(sess.probe_stock.get(pid, 0)),
        # v0.9.3 — built-weapons inventory + per-weapon build prices
        # so the agent can plan an orbit submission without having to
        # remember the constants.
        "weapon_stock": weapon_stock_block,
        "weapon_prices": weapon_prices_block,
        "weapon_specs": weapon_specs_block,
        "harvester_cap_used": sess.harvesters_owned_alive(pid),
        "harvester_cap_max": HARVESTER_MAX_PER_PLAYER,
        "green_owned_count": len(green_owned),
        "green_owned_purity_total": int(green_owned_purity_total),
        # v1.13 — no per-orbit action cap. ``None`` rather than a big
        # number so a consumer can't render a bogus "0/20 slots" counter;
        # credits and blue are the only limit worth showing.
        "actions_max": None,
        "tier_counts": tier_counts,
        # v0.9.5 — BLUE economy readout. Mirrors tier_counts shape
        # so the panel can render both with the same template, plus
        # the rolled-up purity total (the actual currency the
        # weapons economy debits).
        "blue_tier_counts": blue_tier_counts,
        "blue_purity_total": int(blue_purity_total),
        # v0.9.5 — full asset roster so the panel can render a
        # "what do I own?" pane without the watcher having to
        # cross-reference my_assets. Each row already carries the
        # "damaged" flag so the repair affordance can show
        # "[+ repair (500c) · 2 damaged]" instead of a bare button.
        "assets": sess.unit_summary_for_owner(pid),
        # Full parcel list (own hoard) so the vault tooltip can render
        # per-parcel score = purity × mult, and a planner can see exactly
        # what will ship at the next settlement.
        "hoard_parcels": hoard_parcels,
        # v1.13 — purchase prices. The catapult row/transit/threshold
        # fields are gone with the draft that used them; RED now ships
        # whole, so ``quality_mult`` is the entire scoring story.
        "ship_prices": {
            "harvester_build": HARVESTER_BUILD_COST,
            "probe_build": PROBE_BUILD_COST,
            "repair": REPAIR_COST,
            "quality_mult": {k: float(v) for k, v in RED_QUALITY_MULTIPLIER.items()},
        },
        # v1.13 — settlement is automatic in both directions and needs no
        # action from the seat. Stated explicitly rather than left as an
        # absence, because "do I have to ship?" is the first thing both a
        # human and an agent prompt will ask.
        "settlement": {
            "auto": True,
            "red": "every RED parcel ships at settlement; score = purity × tier multiplier",
            "green": (
                f"every GREEN parcel is disposed of at settlement for "
                f"-{int(GREEN_ENDGAME_PENALTY)} each"
            ),
            "green_penalty": int(GREEN_ENDGAME_PENALTY),
        },
        "last_catapult_results": last_catapult,
    }

    return {
        # v0.7.0 structured sections (cortex harness reads these):
        "meta": meta,
        "hud": hud,
        "last_night": last_night,
        "competitor_intel": competitor,
        "station_intel": station_intel,
        "world": world_block,
        "navigation": navigation_block,
        "my_assets": my_assets_block,
        "orbit": orbit_block,
        # v0.9.1 — top-level public summary of yesterday's catapult.
        # Agents read THIS instead of digging into ``last_catapult_results``.
        "last_catapult_summary": last_catapult_summary,

        # Back-compat top-level keys (frontends / older tests):
        "season_name": sess.season_name,
        "session_id": sess.session_id,
        "grid_ascii": _ascii_map(sess, pid, visible, echoes),
        "grid": {
            "width": sess.width,
            "height": sess.height,
            "cell_counts": {
                "fog": max(0, fog_cells_total),
                "stale": max(0, stale_cells_total),
                "fresh": visible_cells_total,
            },
            "tile_counts_visible": visible_counts,
        },
        "red_tiles": red_tiles,
        "green_tiles": green_tiles,
        "blue_tiles": blue_tiles,
        # v0.9.x — BLUE-SIGN: static radiative-signature overlay
        # (RULEBOOK §4.6). Visible from orbit to EVERY house regardless
        # of fog, identical for all seats and across the whole season
        # (does not fade on depletion). The frontend paints it as a
        # fuzzy smear over the player map; agents read it to maneuver
        # toward blue without knowing the exact squares or purity.
        "blue_sign": [dict(r) for r in (sess.blue_sign or [])],
        # v1.x — REDSIGN: discovery-triggered public beacons over pure-RED
        # seams (RULEBOOK §4.11). Minted the first time ANY house sees a
        # pure-RED cell, then visible to EVERY house regardless of fog
        # until the seam is spent. Anonymous, fuzzy smear — agents read it
        # to race toward a jackpot seam someone else found.
        #
        # The ``live`` filter is the retirement (§4.11, prose corrected in
        # v1.33): a spent beacon leaves every seat view rather than going
        # grey in it, so an agent cannot chase a jackpot that is gone. It
        # also means ``spent_by`` — engine ground truth for who banked it —
        # never reaches a seat, which is what keeps retirement anonymous.
        "redsign": [
            _redsign_for_seat(r, pid)
            for r in (sess.redsign or [])
            if r.get("live", True)
        ],
        # v0.9.x — SHIPPED is an uncapped PUBLIC record of every parcel
        # every house sent to Earth (RULEBOOK §4.x). Full detail incl.
        # code/lineage is public; the frontend renders an all-seat,
        # owner-coloured ledger even during live play.
        "shipped_record": _build_shipped_record(sess),
        "fog_clusters": clusters,
        "entities": {"mine": mine, "echoes": echo_entities},
        "entity_detail": entity_detail,
        "recent_log": seat_log,
    }


_HOARD_WARN_PCT: float = 0.80
"""Pct-full threshold for emitting a vault warning on ``hud.hoard``."""


def _vault_warning(
    used: int, cap: int, by_tier: Mapping[str, Mapping[str, Any]],
) -> Optional[str]:
    """Return a human-readable warning if the vault is at or above
    :data:`_HOARD_WARN_PCT` of capacity, else ``None``. The string
    names the tier ladder (§3.14: GREEN > RED > BLUE) so the agent
    knows what gets jettisoned on overflow."""
    if cap <= 0:
        return None
    pct = used / cap
    if pct < _HOARD_WARN_PCT:
        return None
    blue_n = (by_tier.get("BLUE") or {}).get("count") or 0
    red_n = (by_tier.get("RED") or {}).get("count") or 0
    green_n = (by_tier.get("GREEN") or {}).get("count") or 0
    if blue_n > 0:
        next_jettison = "BLUE"
    elif red_n > 0:
        next_jettison = "RED"
    elif green_n > 0:
        next_jettison = "GREEN"
    else:
        next_jettison = "the lowest-tier incoming parcel"
    return (
        f"vault {int(round(pct * 100))}% full ({used}/{cap}) — "
        f"next overflow will jettison {next_jettison} first "
        "(tier ladder GREEN > RED > BLUE)"
    )


def _redsign_for_seat(
    region: Mapping[str, Any], player: PlayerId,
) -> Dict[str, Any]:
    """Project one redsign region for THIS seat's private view (v9).

    The public beacon is anonymous to rivals, so we strip the engine-internal
    ``discoverer`` / ``co_discoverers`` identity fields and expose only a
    boolean ``mine`` — enough for the redsign-poker doctrine to branch on
    "is this my redsign?" without leaking who found a rival's beacon. Legacy
    regions with no discoverer stamp yield ``mine=False`` (treated as
    contested / not-mine, the safer default).

    v9 (I12) — the engine-internal ``pure_cells`` (the seam's real pure
    squares, used only for beacon-liveness) is also stripped so the exact
    pures never leak into a seat view; the smear ``cells`` remain the only
    positional hint. Dead regions are filtered out before this call.
    """
    out = {k: v for k, v in dict(region).items()
           if k not in ("discoverer", "co_discoverers", "pure_cells")}
    disc = str(region.get("discoverer") or "")
    co = [str(o) for o in (region.get("co_discoverers") or [])]
    out["mine"] = bool(disc) and (disc == str(player) or str(player) in co)
    return out


def _rival_words(sess: GameSession, player: PlayerId) -> List[str]:
    """Every string in the log that would give a rival seat away.

    The seat slug catches most of it, including entity ids, because the
    engine names units after their owner (``probe_p2_10``). Display name
    and tag are here because a seat can be renamed and a renamed seat
    that stops being redacted is the worst kind of regression: silent,
    and only on the games people care enough about to name.
    """
    words: List[str] = []
    for seat in sess.players:
        if str(seat) == str(player):
            continue
        words.append(str(seat))
        profile = (sess.player_profiles or {}).get(str(seat)) or {}
        for key in ("display_name", "tag"):
            value = str(profile.get(key) or "").strip()
            if len(value) >= 3:  # a 1-2 char tag would match everything
                words.append(value)
    return words


def _log_for_seat(
    sess: GameSession,
    player: PlayerId,
    rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """The shared engine log, minus anything naming another seat.

    v1.38 (§3.15) — ``store.list_log`` takes no seat and log rows carry
    no owner, so the raw feed is every seat's night in one list: exact
    drop coordinates, and for a bot seat its whole rationale line. That
    is a fog breach the moment anything reads it. Nothing shipped does —
    no harness under ``orchestrator_2`` touches ``recent_log`` — but a
    fork is a directory anyone can write, and "the percept handed it to
    me" is not a cheat we want to have to adjudicate on the day.

    Redaction is by *mention*, not by attribution: a row is dropped if a
    rival's name appears anywhere in it. That is blunter than parsing the
    subject, and deliberately so — a row like "expired probe_p1_3,
    probe_p2_5" is partly this seat's news, but the half that is not is
    the half worth hiding, and this seat learns its own probe expired
    from ``my_assets`` anyway. Erring toward fog costs a line; erring the
    other way costs the rule.

    Rows naming nobody (settlement, phase changes) are kept — they are
    the clock, and every seat is entitled to the clock.
    """
    rivals = _rival_words(sess, player)
    if not rivals:
        return [dict(r) for r in rows]
    kept: List[Dict[str, Any]] = []
    for row in rows:
        text = str(row.get("text", row.get("TEXT", "")) or "")
        if any(word in text for word in rivals):
            continue
        kept.append(dict(row))
    return kept


def _last_night_recap(
    sess: GameSession,
    player: PlayerId,
    recent_log: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Project yesterday's log + ledger into a structured ``last_night``
    block: my orders (with ``outcome``/``reason`` for illegal items),
    assets destroyed last night, parcels banked last night."""
    day_now = int(sess.day)
    day_ended = day_now - 1 if day_now > 0 else 0
    my_orders: List[Dict[str, Any]] = []
    seen_idx = 0
    for row in recent_log or []:
        try:
            row_day = int(row.get("day", row.get("DAY", 0)) or 0)
        except (TypeError, ValueError):
            row_day = 0
        if row_day != day_ended:
            continue
        text = str(row.get("text", row.get("TEXT", "")) or "")
        level = str(row.get("level", row.get("LEVEL", "info")) or "info")
        if not text:
            continue
        # Crude attribution: rows that mention this player as the
        # subject. The simulator's free-text log uses several
        # conventions — "{player} deployed ...", "{player}: step ...",
        # and "{player}'s probe_..." — so we accept any non-alphanumeric
        # punctuation immediately after the player slug.
        def _is_my_row(t: str, p: str) -> bool:
            if not (t.startswith(p) or f" {p}" in t):
                return False
            # Look for the boundary character after the slug.
            idx = t.find(p)
            after = t[idx + len(p):idx + len(p) + 1]
            return after == "" or not after.isalnum()

        if not _is_my_row(text, player):
            continue
        outcome = "illegal" if level == "error" else "ok"
        entry: Dict[str, Any] = {
            "idx": seen_idx,
            "text": text,
            "outcome": outcome,
        }
        if outcome == "illegal":
            entry["reason"] = text
        my_orders.append(entry)
        seen_idx += 1

    destroyed: List[Dict[str, Any]] = []
    for rec in sess.asset_records.values():
        if rec.owner != player:
            continue
        if rec.destroyed_on_day != day_ended:
            continue
        destroyed.append({
            "id": rec.asset_id,
            "kind": rec.asset_type,
            "at": (
                [rec.last_seen_x, rec.last_seen_y]
                if rec.last_seen_x is not None else None
            ),
            "reason": rec.destroyed_by or "unspecified",
        })

    banked: List[Dict[str, Any]] = []
    for parcel in sess.harvest_log.get(player, []):
        try:
            d = int(parcel.get("harvested_on_planning_day", -1))
        except (TypeError, ValueError):
            d = -1
        if d != day_ended:
            continue
        banked.append({
            "harvester_id": parcel.get("harvester_id"),
            "from": [int(parcel.get("x", 0)), int(parcel.get("y", 0))],
            "tile": (
                Tile(int(parcel.get("tile_at_harvest", 0))).name
                if parcel.get("tile_at_harvest") is not None else None
            ),
            "purity": int(parcel.get("purity_at_harvest", 0)),
            "value": max(0, min(255, int(parcel.get("purity_at_harvest", 0)))),
            "square_id": parcel.get("square_id"),
            "lineage": parcel.get("lineage"),
        })

    # v1.8 — canonical COMBAT feed for yesterday, fog-gated per seat.
    # PUBLIC ephemeral effects (EMP salvos + clouds, chaff flares — RULEBOOK
    # §5.1: every seat sees the launch + cloud) go to everyone; the per-unit
    # impact rows (``emp_hit`` / ``chaff_jam``) are the VICTIM's private
    # detail. This is the surface an agent reads instead of trying to
    # reconstruct dissipated clouds: EMP carries cells + hours, chaff carries
    # hours (it has no location — it's a seat-wide jam).
    combat_events: List[Dict[str, Any]] = []
    raw_events = (getattr(sess, "combat_events_by_day", {}) or {}).get(
        str(day_ended), []
    )
    for ev in raw_events:
        etype = str(ev.get("type", ""))
        if etype in ("emp", "snap", "chaff"):
            combat_events.append(dict(ev))
        elif etype in ("emp_hit", "snap_hit", "chaff_jam"):
            if str(ev.get("victim", "")) == str(player):
                combat_events.append(dict(ev))

    # v8 — night-resolution COLLISIONS this seat was in last night. Sourced
    # from ``collision_marks`` (a PUBLIC map feature — the collision ring the
    # frontend renders — stamped with the cell + owner list, decaying after
    # 1 day, §3.6). A simultaneous-drop pile-up damages every involved
    # harvester and banks ZERO for it, but the engine logs that frame with
    # ``owner=None`` and a caption the free-text recap attribution drops — so
    # without this channel the seat sees "banked 0" with no cause and invents
    # one ("thin seam"). Fog-safe: we surface only collisions THIS seat was a
    # party to (self in ``owners``) and the cell everyone already sees.
    my_collisions: List[Dict[str, Any]] = []
    for key, mark in (getattr(sess, "collision_marks", {}) or {}).items():
        if not isinstance(mark, dict):
            continue
        try:
            if int(mark.get("day", -1)) != int(day_ended):
                continue
        except (TypeError, ValueError):
            continue
        owners = [str(o) for o in (mark.get("owners") or [])]
        if str(player) not in owners:
            continue
        try:
            kx, ky = str(key).split(":", 1)
            at = [int(kx), int(ky)]
        except (ValueError, AttributeError):
            at = None
        others = [o for o in owners if o != str(player)]
        my_collisions.append({
            "at": at,
            "others": others,
            "party_count": len(owners),
        })

    # v9 — ATTRIBUTED EVENT DIGEST channels (additive; consumed by tabula_v9,
    # ignored by the frozen v6/v7/v8 prompts). Each entry carries the attacker
    # seat AND the CONSEQUENCE so the agent can draw the causal line ("p2
    # superseded my probe -> I lost that disk -> the hot-drop had no sensor")
    # instead of confabulating a cause for a bad night. Sourced directly from
    # engine state and fog-safe: probe launches/collisions/supersedes are
    # PUBLIC (§3.15/§3.16), EMP/chaff attacker (`by`) already rides the combat
    # feed above, and EMP scars are PUBLIC (§5.1).
    incoming_attacks: List[Dict[str, Any]] = []
    my_denials: List[Dict[str, Any]] = []

    # (a) EMP / chaff impacts on ME — reuse the already fog-filtered feed.
    for ev in combat_events:
        etype = str(ev.get("type", ""))
        if etype == "emp_hit":
            incoming_attacks.append({
                "type": "emp_hit",
                "by": [str(b) for b in (ev.get("by") or [])],
                "unit": ev.get("unit"),
                "hours": list(ev.get("hours") or []),
                "consequence": (
                    "harvester DISABLED in those hours; a pickup scheduled "
                    "then is cancelled and risks a dawn crash"
                ),
            })
        elif etype == "chaff_jam":
            incoming_attacks.append({
                "type": "chaff_jam",
                "by": [str(b) for b in (ev.get("by") or [])],
                "units": [str(u) for u in (ev.get("units") or [])],
                "hours": list(ev.get("hours") or []),
                "consequence": (
                    "your action slots in those hours were CANCELLED "
                    "(nothing you queued that hour happened)"
                ),
            })

    # (b) Probe kills last night — scan the structured log for the PUBLIC
    # supersede / collision events, split into done-TO-me (incoming_attacks)
    # and done-BY-me (my_denials) via the owners map + attacker seat.
    for row in getattr(sess, "log", None) or []:
        try:
            if int(row.get("day", -1)) != int(day_ended):
                continue
        except (TypeError, ValueError):
            continue
        kind = str(row.get("kind", ""))
        if kind not in ("probe_superseded", "probe_collision"):
            continue
        data = row.get("data") or {}
        at = data.get("at")
        owners_by_id = data.get("owners") or {}
        destroyed_ids = list(data.get("destroyed_ids") or [])
        my_lost = [
            eid for eid in destroyed_ids
            if str(owners_by_id.get(eid, "")) == str(player)
        ]
        if kind == "probe_superseded":
            attacker = str(data.get("new_owner", ""))
            if my_lost and attacker != str(player):
                incoming_attacks.append({
                    "type": "probe_superseded",
                    "by": [attacker],
                    "at": at,
                    "lost_ids": my_lost,
                    "consequence": (
                        "your probe was destroyed + replaced; you LOST that "
                        "disk's vision from the next hour on (any hot-drop "
                        "relying on it now has no live sensor)"
                    ),
                })
            elif attacker == str(player):
                victims = sorted({
                    str(o) for o in owners_by_id.values()
                    if str(o) != str(player)
                })
                if victims:
                    my_denials.append({
                        "type": "probe_superseded",
                        "against": victims,
                        "at": at,
                        "consequence": (
                            "you BLINDED them: their probe is gone, denying "
                            "their drop from the next hour on"
                        ),
                    })
        else:  # probe_collision — mutual annihilation on a shared cell
            involved = {str(o) for o in owners_by_id.values()}
            if str(player) not in involved:
                continue
            others = sorted(o for o in involved if o != str(player))
            incoming_attacks.append({
                "type": "probe_collision",
                "by": others,
                "at": at,
                "lost_ids": my_lost,
                "consequence": (
                    "your probe mutually annihilated with a rival probe on "
                    "the same cell — you LOST that disk's vision (they lost "
                    "theirs too)"
                ),
            })

    # (c) EMP SCARS still live as of last night — a spatial hazard to route
    # around this coming night. Pruned by the engine after 1 day, so anything
    # present is current. Public (§5.1).
    emp_scars: List[Dict[str, Any]] = []
    for key, mk in (getattr(sess, "emp_marks", {}) or {}).items():
        if not isinstance(mk, dict):
            continue
        try:
            kx, ky = str(key).split(":", 1)
            cell = [int(kx), int(ky)]
        except (ValueError, AttributeError):
            continue
        emp_scars.append({
            "at": cell,
            "hours": list(mk.get("hours") or []),
            "by": [str(o) for o in (mk.get("owners") or [])],
            "day": int(mk.get("day", -1)) if mk.get("day") is not None else -1,
        })

    return {
        "day_ended": day_ended,
        "my_orders": my_orders,
        "my_assets_destroyed": destroyed,
        "my_parcels_banked": banked,
        "combat_events": combat_events,
        "my_collisions": my_collisions,
        "incoming_attacks": incoming_attacks,
        "my_denials": my_denials,
        "emp_scars": emp_scars,
    }


def _station_intel(sess: GameSession, player: PlayerId) -> Dict[str, Any]:
    """Per-seat orbital station readings (RULEBOOK §3.15.x).

    Fog-of-war intel every seat can pick up from orbit: ``self`` carries
    EXACT readings; ``opponents`` carry only fuzzy grade bands / ranges.
    Each entry also carries the latest night's observable orbital
    ``activity`` (launch/recovery counts + damage, NO coordinates). This
    is the same payload the frontend Pre-Orbital Recap renders, fed to
    agents through the orchestrator.
    """
    seats = list(getattr(sess, "players", ()) or ())
    activity_map = getattr(sess, "orbital_activity_by_day", {}) or {}
    latest_day = None
    if activity_map:
        try:
            latest_day = max(int(d) for d in activity_map.keys())
        except (TypeError, ValueError):
            latest_day = None

    def _activity_for(seat: str) -> Dict[str, int]:
        empty = GameSession._empty_activity_tally()
        if latest_day is None:
            return empty
        by_seat = activity_map.get(str(latest_day), {}) or {}
        return dict(by_seat.get(seat, empty))

    self_obs = sess._station_observation(player, fuzzy=False)
    self_obs["activity"] = _activity_for(player)

    opponents: List[Dict[str, Any]] = []
    for seat in seats:
        if seat == player:
            continue
        obs = sess._station_observation(seat, fuzzy=True)
        obs["activity"] = _activity_for(seat)
        opponents.append(obs)

    return {
        "self": self_obs,
        "opponents": opponents,
        "night_day": latest_day,
    }


def _competitor_intel(
    sess: GameSession,
    player: PlayerId,
    recent_log: Sequence[Mapping[str, Any]],
    visible: Set[Tuple[int, int]],
    echoes: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Surface enemy activity this player can lawfully observe.

    ``new_this_day`` collects ``probe_launch`` events from the
    opponent (always public per §3.15) and any enemy harvester trail
    rows stamped with ``day == day_now - 1`` whose cell is currently
    in this player's vision (live or echo) — without that gate we'd
    leak fog-of-war.

    ``persistent_echoes`` carries older enemy entity sightings the
    player still remembers via probe_intel — last-seen positions of
    enemy units that were spotted at some point.
    """
    day_now = int(sess.day)
    day_ended = day_now - 1 if day_now > 0 else 0
    new_today: List[Dict[str, Any]] = []

    for row in recent_log or []:
        kind = str(row.get("kind", row.get("KIND", "")) or "")
        if kind != "probe_launch":
            continue
        data_blob: Any = row.get("data", row.get("DATA"))
        if isinstance(data_blob, str):
            import json as _json
            try:
                data_blob = _json.loads(data_blob)
            except _json.JSONDecodeError:
                data_blob = {}
        if not isinstance(data_blob, Mapping):
            data_blob = {}
        owner = str(data_blob.get("owner") or "")
        if not owner or owner == player:
            continue
        try:
            row_day = int(row.get("day", row.get("DAY", 0)) or 0)
        except (TypeError, ValueError):
            row_day = 0
        if row_day != day_ended:
            continue
        at = data_blob.get("at") or []
        new_today.append({
            "kind": "enemy_probe_launch",
            "owner": owner,
            "probe_id": data_blob.get("probe_id"),
            "at": list(at) if isinstance(at, (list, tuple)) else None,
            "day_seen": row_day,
            "landed_on_tile": data_blob.get("landed_on_tile"),
            "landed_purity": data_blob.get("landed_purity"),
        })

    # §3.15 FIX (seed-69 E2). The log-window loop above only sees launches
    # still present in the last-N recent-log rows, so a probe launched
    # mid-way through a BUSY night (e.g. the one that mints a redsign) gets
    # pushed out of the window before the next planning read and vanishes
    # from ``new_this_day`` — even though probe launches are ALWAYS public
    # and retained until the probe's scheduled expiry. Source them instead
    # from the authoritative ``probe_intel`` markers, which are seeded for
    # every opponent at launch (:meth:`GameSession._pulse_probe_launch`).
    # Additive + deduped so the enriched log rows above win when present.
    launch_dedupe: Set[Tuple[int, int, str]] = {
        (int(r["at"][0]), int(r["at"][1]), str(r.get("owner") or ""))
        for r in new_today
        if r.get("kind") == "enemy_probe_launch"
        and isinstance(r.get("at"), (list, tuple)) and len(r["at"]) >= 2
    }
    for k, snap in (sess.probe_intel.get(player) or {}).items():
        if not isinstance(snap, Mapping):
            continue
        # Launch day + launcher: a pure probe_launch marker stamps them on
        # itself; a marker merged onto an OLDER terrain echo carries the
        # ``probe_launch_day`` / ``launched_by`` stamp added at merge time.
        if snap.get("via") == "probe_launch":
            l_owner = str(snap.get("launched_by") or "")
            l_day = snap.get("day_seen")
            l_pid = snap.get("probe_id")
        elif snap.get("probe_launch_day") is not None:
            l_owner = str(snap.get("launched_by") or "")
            l_day = snap.get("probe_launch_day")
            l_pid = next(
                (o.get("id") for o in (snap.get("occupants") or [])
                 if isinstance(o, Mapping) and o.get("type") == "probe"
                 and o.get("owner") and o.get("owner") != player),
                None,
            )
        else:
            continue
        if not l_owner or l_owner == player:
            continue
        try:
            l_day = int(l_day)
            sx, sy = k.split(":", 1)
            xi, yi = int(sx), int(sy)
        except (TypeError, ValueError):
            continue
        if l_day != day_ended:
            continue
        if (xi, yi, l_owner) in launch_dedupe:
            continue
        launch_dedupe.add((xi, yi, l_owner))
        new_today.append({
            "kind": "enemy_probe_launch",
            "owner": l_owner,
            "probe_id": l_pid,
            "at": [xi, yi],
            "day_seen": l_day,
        })

    seen_set: Set[Tuple[int, int, str]] = set()
    for owner_other in sess.players:
        if owner_other == player:
            continue
        bucket = sess.track_paths.get(owner_other, {})
        for k, entry in bucket.items():
            try:
                sx, sy = k.split(":", 1)
                xi, yi = int(sx), int(sy)
            except ValueError:
                continue
            d_stamp = entry.get("d") if isinstance(entry, Mapping) else None
            try:
                d_int = int(d_stamp) if d_stamp is not None else -1
            except (TypeError, ValueError):
                d_int = -1
            if d_int != day_ended:
                continue
            if (xi, yi) not in visible and _xy_key(xi, yi) not in echoes:
                continue
            key = (xi, yi, str(owner_other))
            if key in seen_set:
                continue
            seen_set.add(key)
            h_id = (
                entry.get("h") if isinstance(entry, Mapping) else None
            )
            new_today.append({
                "kind": "enemy_harvester_trail",
                "owner": owner_other,
                "harvester_id": h_id,
                "at": [xi, yi],
                "day_seen": d_int,
            })

    persistent: List[Dict[str, Any]] = []
    for ek, snap in echoes.items():
        occ = snap.get("occupants") if isinstance(snap, Mapping) else None
        if not isinstance(occ, list):
            continue
        try:
            sx, sy = ek.split(":", 1)
            xi, yi = int(sx), int(sy)
        except ValueError:
            continue
        for o in occ:
            if not isinstance(o, Mapping):
                continue
            o_owner = o.get("owner")
            if not o_owner or o_owner == player:
                continue
            persistent.append({
                "kind": (
                    "enemy_probe"
                    if o.get("type") == "probe"
                    else f"enemy_{o.get('type', 'unit')}"
                ),
                "owner": o_owner,
                "at": [xi, yi],
                "last_seen_day": (
                    int(snap["day_seen"])
                    if isinstance(snap.get("day_seen"), int)
                    else None
                ),
            })

    return {"new_this_day": new_today, "persistent_echoes": persistent}


def _my_assets(sess: GameSession, player: PlayerId) -> List[Dict[str, Any]]:
    """Full fleet roll including destroyed assets with lifetime stats."""
    out: List[Dict[str, Any]] = []
    for rec in sess.asset_records.values():
        if rec.owner != player:
            continue
        ent = sess.entities.get(rec.asset_id)
        if rec.destroyed_on_day is not None:
            state = "destroyed"
            at = (
                [rec.last_seen_x, rec.last_seen_y]
                if rec.last_seen_x is not None else None
            )
            carrying = 0
        elif ent is None:
            state = "missing"
            at = (
                [rec.last_seen_x, rec.last_seen_y]
                if rec.last_seen_x is not None else None
            )
            carrying = 0
        elif ent.entity_type == "harvester":
            if ent.x is None:
                state = "orbit"
                at = None
            else:
                state = "deployed"
                at = [int(ent.x), int(ent.y)]
            carrying = len(ent.cargo_squares)
        elif ent.entity_type == "probe":
            state = "deployed" if ent.x is not None else "orbit"
            at = (
                [int(ent.x), int(ent.y)]
                if ent.x is not None else None
            )
            carrying = 0
            # Probe lifetime countdown — how many more nights until this
            # probe expires (``None`` when lifetime is off / forever).
            _k = _probe_lifetime_nights()
            if _k and at is not None:
                _first = int(rec.first_deployed_day or rec.created_on_day or 0)
                _nights_used = max(0, int(sess.day) - _first)
                nights_remaining = max(0, int(_k) - _nights_used)
            else:
                nights_remaining = None
        elif ent.entity_type == "orblift":
            state = "orbit" if ent.x is None else "surface"
            at = (
                [int(ent.x), int(ent.y)]
                if ent.x is not None else None
            )
            carrying = int(getattr(ent, "orbital_cargo_red", 0) or 0)
        else:
            state = "unknown"
            at = None
            carrying = 0
        row_out: Dict[str, Any] = {
            "id": rec.asset_id,
            "kind": rec.asset_type,
            "owner": rec.owner,
            "state": state,
            "at": at,
            "carrying": carrying,
            "lifetime": {
                "created_on_day": rec.created_on_day,
                "first_deployed_day": rec.first_deployed_day,
                "days_on_surface": rec.total_days_on_surface,
                "red_harvested": rec.total_red_harvested,
                "destroyed_on_day": rec.destroyed_on_day,
                "destroyed_reason": rec.destroyed_by,
            },
        }
        if ent and ent.entity_type == "probe" and nights_remaining is not None:
            row_out["nights_remaining"] = nights_remaining
        out.append(row_out)
    out.sort(key=lambda r: (r["lifetime"].get("created_on_day") or 0, r["id"]))
    return out


def _policy_actions_used(sess: GameSession, player: PlayerId) -> int:
    """Number of policy actions already locked for this player this turn.

    Counts the *raw* queue length the player has submitted for the
    upcoming night (parser-pruned). The simulator will further skip
    invalid items at execution time, so this is an upper bound on the
    actions that will be applied. Used to surface
    ``meta.policy_actions_left = MAX_MOVES - used`` so the agent
    knows how many slots remain when assembling a policy across
    multiple Cortex turn-completions."""
    pending = sess.pending_policies.get(player) if hasattr(sess, "pending_policies") else None
    if pending is None:
        return 0
    return len(pending)


def _build_shipped_record(sess: GameSession) -> List[Dict[str, Any]]:
    """All-seat PUBLIC record of every parcel sent to Earth.

    v0.9.x — SHIPPED is an uncapped, public ledger (RULEBOOK §4.x).
    Shipping is a public act, so every detail (code/``square_id``,
    purity, transit tax, score, tier, and refine ``refined_from``
    lineage) is exposed to every viewer in all contexts — nothing is
    masked. Each entry is the stored shipped row plus an explicit
    ``owner`` so the frontend can colour the record by house.
    """
    record: List[Dict[str, Any]] = []
    for seat in sess.players:
        for row in sess.shipped_squares.get(seat, []):
            entry = dict(row)
            entry["owner"] = str(seat)
            record.append(entry)
    return record


def _vault_tier_breakdown(
    parcels: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Per-tile-colour summary of a hoard / shipped list.

    Used by the HUD to surface ``hud.hoard.by_tier`` so the agent can
    reason about §3.14 overflow before committing to a pickup. Shape::

        {
          "GREEN": {"count": 3, "purity_min": 255, "purity_max": 255},
          "RED":   {"count": 41, "purity_min": 12, "purity_max": 254},
          "BLUE":  {"count": 6, "purity_min": 4, "purity_max": 180},
        }

    Colours with zero parcels are still present (with ``count: 0``) so
    the agent's branching doesn't need to .get() with defaults.
    """
    buckets: Dict[str, Dict[str, Any]] = {
        "GREEN": {"count": 0, "purity_min": None, "purity_max": None, "value": 0},
        "RED":   {"count": 0, "purity_min": None, "purity_max": None, "value": 0},
        "BLUE":  {"count": 0, "purity_min": None, "purity_max": None, "value": 0},
    }
    name_for = {
        int(Tile.GREEN): "GREEN",
        int(Tile.RED): "RED",
        int(Tile.BLUE): "BLUE",
    }
    for p in parcels:
        tile_i = int(p.get("tile_at_harvest", p.get("origin_tile", 0)) or 0)
        key = name_for.get(tile_i)
        if key is None:
            continue
        bucket = buckets[key]
        pur_raw = (
            p.get("purity_at_harvest")
            if isinstance(p.get("purity_at_harvest"), (int, float))
            else p.get("origin_purity", 0)
        )
        try:
            pur = int(pur_raw) if pur_raw is not None else 0
        except (TypeError, ValueError):
            pur = 0
        pur = max(0, min(255, pur))
        bucket["count"] += 1
        if bucket["purity_min"] is None or pur < bucket["purity_min"]:
            bucket["purity_min"] = pur
        if bucket["purity_max"] is None or pur > bucket["purity_max"]:
            bucket["purity_max"] = pur
        # v0.9.6 — tier-weighted potential score IF this parcel ships today
        # (matches ``compute_player_score``: eff_purity × MULT[tier]). Only
        # RED scores positively, so ``value`` is meaningful for the RED
        # bucket; GREEN/BLUE keep 0 (green is an end-game penalty, blue funds
        # a separate economy). Lets the HUD show "held RED worth ~N pts" so
        # the agent doesn't read an unshipped hoard as lost cargo.
        if key == "RED":
            tier = _red_tier_name(pur)
            bucket["value"] += int(round(pur * RED_QUALITY_MULTIPLIER.get(tier, 1.0)))
    return buckets
