"""Orbit-phase settlement (RULEBOOK §4; simplified in v1.13).

Each game-day starts in :data:`Phase.ORBIT`. A seat queues purchases
(harvester / probe / repair) and weapons buys (EMP / mine / chaff) —
as many as credits and blue allow. Once every seat locks, this
resolver:

1. Awards Orbit credits (idempotent per-day).
2. Applies each seat's actions in **declaration order**. These only
   move credits and blue, so the balance debits are the whole story.
3. **Ships every RED parcel** (RULEBOOK §4.4). No bid, no slot draft,
   no transit fee: score = purity x tier-multiplier(purity).
4. **Disposes of every GREEN parcel** at a flat
   :data:`GREEN_ENDGAME_PENALTY` each (RULEBOOK §4.5).
5. Transitions ``phase`` to :data:`Phase.PLANNING` so the night
   submission window opens.

v1.13 removed the three mechanics this module mostly used to be: the
per-parcel credit-bid RED catapult (20 slots over 4 rows, each with a
purity transit charge), the round-robin GREEN flush paid for in forfeit
RED fuel, and refining. With them went the parcel-locking model they
needed and the 3-action cap. What is left is the pair of decisions
worth making — what to buy, and what to arm with — and settlement that
happens to you rather than settlement you bid for.

The resolver writes structured log rows (``[orbit] ...``) and appends
one ``catapult_history`` entry per call so the agent view can surface
``last_catapult_results`` next turn. That key keeps its name: replay
frames and agent prompts across the repo read it, and the shape is
still "what left my vault last settlement".
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Tuple, TYPE_CHECKING

from sea_of_colours.game.policy import (
    BuildChaffAction,
    BuildEmpAction,
    BuildHarvesterAction,
    BuildProbeAction,
    BuildSnapAction,
    OrbitAction,
    OrbitWasteAction,
    RepairAction,
)

if TYPE_CHECKING:
    from sea_of_colours.game.session import GameSession, PlayerId


# v0.9.6 — N-seat aware. The resolver derives iteration order from
# ``sess.players`` on every ``run`` so 3- and 4-seat sessions resolve
# the same way 2-seat ones do. The constant stays as a legacy default
# for any caller that bypasses ``run`` (unit tests poking helpers
# directly).
PLAYERS_ORDER: Tuple[str, ...] = ("p1", "p2")


def _parcel_key(parcel: Mapping[str, Any]) -> str:
    return str(parcel.get("square_id") or parcel.get("site_id") or "")


def _parcel_purity(parcel: Mapping[str, Any]) -> int:
    raw = (
        parcel.get("purity_at_harvest")
        if isinstance(parcel.get("purity_at_harvest"), (int, float))
        else parcel.get("origin_purity")
        if isinstance(parcel.get("origin_purity"), (int, float))
        else parcel.get("purity")
    )
    try:
        v = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        v = 0
    return max(0, min(255, v))


def _parcel_tile(parcel: Mapping[str, Any]) -> int:
    raw = parcel.get("tile_at_harvest")
    if raw is None:
        raw = parcel.get("origin_tile")
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _is_red(parcel: Mapping[str, Any]) -> bool:
    # RED = Tile.RED = 2. Avoid importing Tile to keep this module
    # cycle-light.
    return _parcel_tile(parcel) == 2


def _is_green(parcel: Mapping[str, Any]) -> bool:
    # GREEN = Tile.GREEN = 1.
    return _parcel_tile(parcel) == 1


def _tier_label(p: int) -> str:
    if p <= 50:
        return "trace"
    if p <= 150:
        return "vein"
    if p <= 254:
        return "mass"
    return "pure"


def _summarize_inventory(parcels: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """v0.9.1 — public summary of a catapult inventory.

    Used by the agent view + replay catapult modal so the public
    learns WHAT shipped (counts, tiers, total purity) without
    leaking parcel provenance / lineage / origin coords. Returns
    a stable shape even for empty inventories so the renderer can
    branch on ``count == 0`` to show the "empty" marker.
    """
    parcels = list(parcels or [])
    color_counts = {"red": 0, "green": 0, "blue": 0}
    tier_counts = {"trace": 0, "vein": 0, "mass": 0, "pure": 0}
    total_purity = 0
    for p in parcels:
        tile = _parcel_tile(p)
        purity = _parcel_purity(p)
        total_purity += purity
        if tile == 2:
            color_counts["red"] += 1
        elif tile == 1:
            color_counts["green"] += 1
        elif tile == 3:
            color_counts["blue"] += 1
        tier_counts[_tier_label(purity)] += 1
    return {
        "count": len(parcels),
        "total_purity": int(total_purity),
        "color_counts": color_counts,
        "tier_counts": tier_counts,
    }


class OrbitResolver:
    """Pure data-mutation engine — owns no state of its own."""

    def run(
        self,
        sess: "GameSession",
        queues: Mapping[str, Sequence[OrbitAction]],
    ) -> None:
        from sea_of_colours.game.session import (
            Phase,
            cast_player,
        )

        sess.log_info(f"[orbit] day {sess.day} — orbit settlement begins")

        sess.award_orbit_credits()
        sess.award_tutorial_blue_topup()

        # v0.9.6 — derive seat list from the session so N-seat games
        # are first-class. The tuple is captured once at the top so
        # every helper sees a stable iteration order.
        seats: Tuple[str, ...] = tuple(sess.players)

        # 1) Per-seat action application in DECLARED ORDER. v1.13 — the
        #    only actions left spend credits and blue, so the old
        #    parcel-locking model went with the mechanics that needed it.
        for p in seats:
            actions = list(queues.get(p, []) or [])
            self._apply_seat_actions(
                sess, cast_player(p, allowed=seats), actions,
            )

        # 2) RED ships automatically — every parcel, no bid, no transit
        #    fee (RULEBOOK §4.4).
        catapult_result = self._settle_red_auto(sess, seats)

        # 3) GREEN is disposed of automatically at a flat penalty each
        #    (RULEBOOK §4.5). Mandatory: green can never be displaced out
        #    of a vault, so leaving it would brick the seat.
        jett_result = self._settle_green_auto(sess, seats)

        # v0.9.1 — stamp public inventory summaries onto each section
        # (per-seat AND per-catapult totals) so the replay modal can
        # render "Catapult this day: 5 parcels Σ430p · trace 2 · vein 3"
        # without exposing private provenance keys.
        self._stamp_public_inventories(
            catapult_result, parcels_key="shipped_parcels",
        )
        self._stamp_public_inventories(
            jett_result, parcels_key="jettisoned_parcels",
        )

        sess.catapult_history.append({
            "day": int(sess.day),
            "catapult": catapult_result,
            "jettison": jett_result,
        })

        # v0.9.11 — stamp the post-settlement ("post"-orbital) station
        # readings for every seat, keyed by the settlement day so the
        # Post-Orbital Briefing report (and replay scrubber) can show
        # how each platform looked after shipping/jettison resolved.
        try:
            sess.station_obs_by_day.setdefault(str(int(sess.day)), {})[
                "post"
            ] = sess.station_observation_snapshot()
        except Exception:  # pragma: no cover — never block settlement
            pass

        # 4) Flip phase + clear locks. The final settlement orbit ends
        #    the season outright (RULEBOOK §4); every other orbit hands
        #    off to the night planning window.
        sess.pending_orbit_actions = {p: None for p in seats}
        if getattr(sess, "final_orbit", False):
            sess.pending_policies = {p: None for p in seats}
            sess.phase = Phase.SEASON_COMPLETE
            sess.log_info(
                f"[seasonComplete] {sess.season_name or sess.session_id} — "
                f"final settlement orbit resolved; season over."
            )
        else:
            sess.phase = Phase.PLANNING
            sess.log_info(
                f"[orbit] day {sess.day} — settlement complete; planning opens"
            )

    # ── Per-seat action application ──────────────────────────────────

    def _apply_seat_actions(
        self,
        sess: "GameSession",
        player: "PlayerId",
        actions: Sequence[OrbitAction],
    ) -> None:
        """Apply one seat's queued actions in DECLARED ORDER.

        v1.13 — this used to return ship/green commitments for the two
        global drafts and maintain an ``eligible``/``committed`` parcel
        lock set. Both are gone: the only actions left are purchases and
        weapons buys, which spend credits and blue and touch no parcels,
        so there is nothing two actions could double-spend that the
        balance debits don't already handle. Settlement of RED and GREEN
        is automatic and happens after every seat has spent.
        """
        for act in actions:
            if isinstance(act, OrbitWasteAction):
                sess.log_error(
                    f"[orbit] {player}: invalid action — {act.reason}"
                )
                continue

            if isinstance(act, BuildHarvesterAction):
                ok, msg = sess.apply_build_harvester(player)
            elif isinstance(act, BuildProbeAction):
                ok, msg = sess.apply_build_probe(
                    player, count=int(getattr(act, "count", 1) or 1),
                )
            # v0.9.3 — three new build actions for the interdiction
            # weapons (RULEBOOK §5.0). Each pays blue + credits NOW
            # and pushes the count into ``weapon_stock``; the night-
            # phase launch moves drain that stock instead of paying
            # at launch time.
            elif isinstance(act, BuildEmpAction):
                ok, msg = sess.apply_build_emp(
                    player, count=int(getattr(act, "count", 1) or 1),
                )
            elif isinstance(act, BuildChaffAction):
                ok, msg = sess.apply_build_chaff(
                    player, count=int(getattr(act, "count", 1) or 1),
                )
            elif isinstance(act, BuildSnapAction):
                ok, msg = sess.apply_build_snap(
                    player, count=int(getattr(act, "count", 1) or 1),
                )
            elif isinstance(act, RepairAction):
                ok, msg = sess.apply_repair(player, act.unit)
            else:
                sess.log_error(
                    f"[orbit] {player}: unhandled action type {act.tag}"
                )
                continue

            if ok:
                sess.log_info(f"[orbit] {msg}")
            else:
                sess.log_error(f"[orbit] {msg}")

    # ── Automatic settlement (v1.13, RULEBOOK §4.4) ──────────────────

    @staticmethod
    def _settle_red_auto(
        sess: "GameSession",
        seats: Tuple[str, ...],
    ) -> Dict[str, Any]:
        """Ship every RED parcel in every vault. No bid, no fee, no cap.

        v1.13 — replaces the 20-slot credit-bid draft. A parcel scores
        its full purity times the tier multiplier of that purity, so the
        RED economy is unchanged in everything except *how* a parcel
        reaches the scoreboard. Shipping is unconditional, which is what
        makes where you sent the harvester the only decision that
        mattered.
        """
        from sea_of_colours.game.session import RED_QUALITY_MULTIPLIER

        result: Dict[str, Any] = {
            "auto": True,
            "seats": {},
            # One entry per parcel actually shipped, in seat order. This
            # used to be a fixed 20-slot lattice because slots were the
            # scarce thing being bid over; with shipping unconditional
            # it is simply the manifest, and the UI sizes its grid to
            # however long it happens to be.
            "slot_assignments": [],
            "parcels_shipped": 0,
            "score_shipped": 0.0,
        }
        for seat in seats:
            hoard = list(sess.hoard_squares.get(seat, []))
            reds = [p for p in hoard if _is_red(p)]
            shipped: List[Dict[str, Any]] = []
            score = 0.0
            for parcel in reds:
                purity = max(0, _parcel_purity(parcel))
                tier = _tier_label(purity)
                mult = RED_QUALITY_MULTIPLIER.get(tier, 1.0)
                value = purity * mult
                row = dict(parcel)
                # Stamp the scoring inputs so compute_player_score never
                # has to re-derive them, and a replay can show the maths.
                row["effective_purity"] = purity
                row["score_tier"] = tier
                row["shipped_day"] = int(sess.day)
                shipped.append(row)
                score += value
                result["slot_assignments"].append({
                    "seat": seat,
                    "shipped": True,
                    "disposition": "shipped",
                    "effective_purity": purity,
                    "tier": tier,
                    "tier_multiplier": mult,
                    "score": round(value, 2),
                    "parcel": dict(parcel),
                })
            if shipped:
                sess.shipped_squares.setdefault(seat, []).extend(shipped)
                sess.hoard_squares[seat] = [
                    p for p in hoard if not _is_red(p)
                ]
                # Keep the O(1) HUD counter in step with shipped_squares.
                # It caches the score contribution of the SHIPPED bay, and
                # engine.py's endgame breakdown leans on
                # ``shipped + vault penalties == score_for``, so a missed
                # bump here shows up as a scoreboard frozen at zero.
                sess.cumulative_shipped_score[seat] = (
                    float(sess.cumulative_shipped_score.get(seat, 0.0) or 0.0)
                    + score
                )
                sess.log_info(
                    f"[orbit] {seat}: auto-shipped {len(shipped)} RED "
                    f"parcel(s) for {int(round(score))} score"
                )
            result["seats"][seat] = {
                "awarded": len(shipped),
                "shipped_parcels": shipped,
                "score_shipped": round(score, 2),
            }
            result["parcels_shipped"] += len(shipped)
            result["score_shipped"] += score
        result["score_shipped"] = round(result["score_shipped"], 2)
        return result

    @staticmethod
    def _settle_green_auto(
        sess: "GameSession",
        seats: Tuple[str, ...],
    ) -> Dict[str, Any]:
        """Dispose of every GREEN parcel and charge the flat penalty.

        v1.13 — replaces the 12-slot round-robin flush that was paid for
        in forfeit RED fuel. Disposal is now unconditional and costs a
        flat :data:`GREEN_ENDGAME_PENALTY` per parcel.

        Disposal is **not optional**, and that is load-bearing rather
        than a simplification: GREEN sits at the top of the §3.14 vault
        ladder and can never be displaced, so a house that could not
        flush it would silently brick its own vault a few nights in.

        Disposed parcels are appended to ``shipped_squares`` carrying
        their GREEN origin tile. ``compute_player_score`` recognises them
        and subtracts the penalty instead of adding purity — which keeps
        the live score and the bulk scoreboard (which reads the SHIPPED
        table directly, without hydrating a session) in agreement.
        """
        from sea_of_colours.game.session import GREEN_ENDGAME_PENALTY

        result: Dict[str, Any] = {
            "auto": True,
            "seats": {},
            "slot_assignments": [],
            "parcels_disposed": 0,
            "penalty_total": 0,
        }
        for seat in seats:
            hoard = list(sess.hoard_squares.get(seat, []))
            greens = [p for p in hoard if _is_green(p)]
            disposed: List[Dict[str, Any]] = []
            for parcel in greens:
                row = dict(parcel)
                row["effective_purity"] = 0
                row["score_tier"] = "green"
                row["shipped_day"] = int(sess.day)
                row["penalty"] = int(GREEN_ENDGAME_PENALTY)
                disposed.append(row)
                result["slot_assignments"].append({
                    "seat": seat,
                    "flushed": True,
                    "disposition": "disposed",
                    "penalty": int(GREEN_ENDGAME_PENALTY),
                    "parcel": dict(parcel),
                })
            penalty = int(GREEN_ENDGAME_PENALTY) * len(disposed)
            if disposed:
                sess.shipped_squares.setdefault(seat, []).extend(disposed)
                sess.hoard_squares[seat] = [
                    p for p in hoard if not _is_green(p)
                ]
                # Disposed green lands in SHIPPED as a debit, so the
                # cached counter has to fall with it or the HUD would
                # read high by 100 a parcel (see _settle_red_auto).
                sess.cumulative_shipped_score[seat] = (
                    float(sess.cumulative_shipped_score.get(seat, 0.0) or 0.0)
                    - penalty
                )
                sess.log_info(
                    f"[orbit] {seat}: auto-disposed {len(disposed)} GREEN "
                    f"parcel(s) for -{penalty} score"
                )
            result["seats"][seat] = {
                "awarded": len(disposed),
                "jettisoned_parcels": disposed,
                "penalty": penalty,
            }
            result["parcels_disposed"] += len(disposed)
            result["penalty_total"] += penalty
        return result

    @staticmethod
    def _stamp_public_inventories(
        section: Mapping[str, Any], parcels_key: str,
    ) -> None:
        """Inject a public ``inventory`` summary into a settlement
        section (in-place).

        Per-seat: each ``seats[<seat>]`` entry gains
        ``inventory`` derived from its ``parcels_key`` list (empty if
        the seat didn't ship / jettison anything). Section root also
        gains an aggregate ``inventory`` summing across both seats.
        """
        if not isinstance(section, dict):
            return
        all_parcels: List[Mapping[str, Any]] = []
        seats = section.get("seats")
        if isinstance(seats, dict):
            for seat, seat_block in seats.items():
                if not isinstance(seat_block, dict):
                    continue
                parcels = seat_block.get(parcels_key) or []
                seat_block["inventory"] = _summarize_inventory(parcels)
                all_parcels.extend(parcels)
        section["inventory"] = _summarize_inventory(all_parcels)

