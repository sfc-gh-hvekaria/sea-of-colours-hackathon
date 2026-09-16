"""Per-season balance metrics for the offline sweep harness.

Reads a *resolved* season out of any :class:`SocStore` and folds it into a
flat dict of scalar metrics (CSV-friendly). The headline group is
**interaction / denial** — the north-star is forcing players into contact
and making farming a contested, telegraphed act rather than two solo
corners.

All extraction is defensive (``getattr`` / ``.get``) so a partially-played
or oddly-shaped season degrades to zeros instead of throwing inside a
batch run.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sea_of_colours.snowpark.engine import _hydrate_session

# Proximity thresholds (Manhattan) at which two opposing surfaced
# harvesters are considered "in contact" — r2 ~ EMP blast reach, r4 ~
# loose skirmish range. Drives the contact-fraction metrics.
CONTACT_R_NEAR = 2
CONTACT_R_FAR = 4


def _seat_of(asset: Any) -> str:
    return str(getattr(asset, "owner", "") or "")


def _surfaced_harvesters(frame: Dict[str, Any]) -> Dict[str, List[tuple]]:
    """Map owner -> list of (x, y) for harvesters currently on the surface."""
    by_owner: Dict[str, List[tuple]] = {}
    for ent in frame.get("entities") or []:
        if ent.get("t") != "harvester":
            continue
        surf = ent.get("surface")
        if not (isinstance(surf, (list, tuple)) and len(surf) == 2):
            continue
        try:
            xy = (int(surf[0]), int(surf[1]))
        except (TypeError, ValueError):
            continue
        by_owner.setdefault(str(ent.get("owner") or ""), []).append(xy)
    return by_owner


def _min_cross_owner_gap(by_owner: Dict[str, List[tuple]]) -> Optional[int]:
    """Min Manhattan distance between any two opposing-owner harvesters."""
    owners = [o for o in by_owner if by_owner[o]]
    if len(owners) < 2:
        return None
    best: Optional[int] = None
    for i in range(len(owners)):
        for j in range(i + 1, len(owners)):
            for ax, ay in by_owner[owners[i]]:
                for bx, by in by_owner[owners[j]]:
                    d = abs(ax - bx) + abs(ay - by)
                    if best is None or d < best:
                        best = d
    return best


def compute_season_metrics(store: Any, session_id: str) -> Dict[str, Any]:
    """Fold a resolved season into a flat metrics dict."""
    sess = _hydrate_session(store, session_id)
    players = list(getattr(sess, "players", ("p1", "p2")))
    p1, p2 = (players + ["p1", "p2"])[:2]
    w = int(getattr(sess, "width", 0) or 0)
    h = int(getattr(sess, "height", 0) or 0)
    cells = max(1, w * h)

    # ── day count ─────────────────────────────────────────────────
    di = store.day_index(session_id)
    days = max((int(d.get("day", 0)) for d in di), default=int(getattr(sess, "day", 1)))

    # ── score / margin ────────────────────────────────────────────
    score = {p: float(sess.score_for(p)) for p in (p1, p2)}
    margin = score[p1] - score[p2]
    if margin > 0:
        winner = p1
    elif margin < 0:
        winner = p2
    else:
        winner = "tie"

    out: Dict[str, Any] = {
        "session_id": session_id,
        "season_name": getattr(sess, "season_name", ""),
        "days": days,
        "score_p1": score[p1],
        "score_p2": score[p2],
        "margin_abs": abs(margin),
        "winner": winner,
    }

    # ── harvest throughput + cargo state ──────────────────────────
    recs = list(getattr(sess, "asset_records", {}).values())
    harv = {p1: 0, p2: 0}
    for r in recs:
        if getattr(r, "asset_type", "") == "harvester":
            harv[_seat_of(r)] = harv.get(_seat_of(r), 0) + int(
                getattr(r, "total_red_harvested", 0) or 0
            )
    out["harvested_p1"] = harv.get(p1, 0)
    out["harvested_p2"] = harv.get(p2, 0)
    out["harvested_total"] = harv.get(p1, 0) + harv.get(p2, 0)
    out["harvested_per_night"] = round(out["harvested_total"] / max(1, days), 2)
    for p in (p1, p2):
        out[f"shipped_{p}"] = len(store.list_shipped(session_id, p))
        out[f"hoard_{p}"] = len(store.list_hoard(session_id, p))

    # ── probe economy ─────────────────────────────────────────────
    probes = [r for r in recs if getattr(r, "asset_type", "") == "probe"]
    destroyed = [r for r in probes if getattr(r, "destroyed_on_day", None) is not None]
    lifetimes: List[int] = []
    for r in probes:
        start = getattr(r, "first_deployed_day", None) or getattr(r, "created_on_day", 1)
        end = getattr(r, "destroyed_on_day", None) or days
        lifetimes.append(max(0, int(end) - int(start)))
    out["probes_built"] = len(probes)
    out["probes_built_p1"] = sum(1 for r in probes if _seat_of(r) == p1)
    out["probes_built_p2"] = sum(1 for r in probes if _seat_of(r) == p2)
    out["probes_destroyed"] = len(destroyed)
    out["probe_avg_lifetime"] = round(
        sum(lifetimes) / len(lifetimes), 2
    ) if lifetimes else 0.0

    # ── interaction / denial (north-star) ─────────────────────────
    log = [e for e in getattr(sess, "log", []) if isinstance(e, dict)]
    kinds: Dict[str, int] = {}
    for e in log:
        k = e.get("kind")
        if k:
            kinds[k] = kinds.get(k, 0) + 1
    out["probe_launches"] = kinds.get("probe_launch", 0)
    out["probe_collisions"] = kinds.get("probe_collision", 0)
    out["probe_supersedes"] = kinds.get("probe_superseded", 0)

    # Destroyed-asset reasons, bucketed (weapon-driven attrition).
    def _count_reason(substr: str) -> int:
        return sum(
            1 for r in recs
            if substr in str(getattr(r, "destroyed_by", "") or "")
        )

    out["kills_emp"] = _count_reason("emp")
    out["kills_mine"] = _count_reason("mine")
    out["kills_crush"] = _count_reason("crush")
    out["kills_collision"] = _count_reason("probe_collision")
    out["kills_supersede"] = _count_reason("probe_superseded")
    out["denial_events"] = (
        out["probe_collisions"] + out["probe_supersedes"]
        + out["kills_emp"] + out["kills_mine"]
    )

    # ── map illumination / "solved-ness" ──────────────────────────
    def _explored(owner: str) -> set:
        ks = set((getattr(sess, "probe_intel", {}) or {}).get(owner, {}).keys())
        ks |= set((getattr(sess, "memory_tiles", {}) or {}).get(owner, {}).keys())
        return ks

    e1, e2 = _explored(p1), _explored(p2)
    out["explored_frac_p1"] = round(len(e1) / cells, 3)
    out["explored_frac_p2"] = round(len(e2) / cells, 3)
    out["explored_frac_union"] = round(len(e1 | e2) / cells, 3)

    # ── harvester proximity (contact) over the replay ─────────────
    frames = store.list_replay_frames(session_id)
    gaps: List[int] = []
    for f in frames:
        g = _min_cross_owner_gap(_surfaced_harvesters(f))
        if g is not None:
            gaps.append(g)
    if gaps:
        out["min_harv_gap"] = min(gaps)
        out["mean_harv_gap"] = round(sum(gaps) / len(gaps), 2)
        out["contact_frac_r2"] = round(
            sum(1 for g in gaps if g <= CONTACT_R_NEAR) / len(gaps), 3
        )
        out["contact_frac_r4"] = round(
            sum(1 for g in gaps if g <= CONTACT_R_FAR) / len(gaps), 3
        )
        out["dual_surfaced_frames"] = len(gaps)
    else:
        out["min_harv_gap"] = ""
        out["mean_harv_gap"] = ""
        out["contact_frac_r2"] = 0.0
        out["contact_frac_r4"] = 0.0
        out["dual_surfaced_frames"] = 0

    return out


# Ordered metric columns for stable CSV output.
METRIC_COLUMNS: List[str] = [
    "config", "seed", "session_id", "season_name", "days",
    "score_p1", "score_p2", "margin_abs", "winner",
    "harvested_p1", "harvested_p2", "harvested_total", "harvested_per_night",
    "shipped_p1", "shipped_p2", "hoard_p1", "hoard_p2",
    "probes_built", "probes_built_p1", "probes_built_p2",
    "probes_destroyed", "probe_avg_lifetime",
    "probe_launches", "probe_collisions", "probe_supersedes",
    "kills_emp", "kills_mine", "kills_crush",
    "kills_collision", "kills_supersede", "denial_events",
    "explored_frac_p1", "explored_frac_p2", "explored_frac_union",
    "min_harv_gap", "mean_harv_gap",
    "contact_frac_r2", "contact_frac_r4", "dual_surfaced_frames",
]
