#!/usr/bin/env python3
"""Offline canonical-battery stats reader.

Walks a file-backed season store (``./seasons`` by default) and, for each
session, prints player identities (name/tag/color), final shipped scores,
and the harvester-loss / EMP / repair tallies pulled straight from the
persisted session state and engine log.

Authoritative sources (no log regex guessing where possible):
  * scores            -> json_state.cumulative_shipped_score
  * EMP launches/seat -> json_state.weapons_used[seat]['emp']
  * harvesters lost   -> json_state.destroyed_harvester_markers
  * free repairs      -> log scan (must stay 0 under canonical rules)

Usage::

    python scripts/season_stats.py [store_dir] [--prefix Canon7_]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _inner_state(session_dir):
    raw = _load(os.path.join(session_dir, "state.json")) or {}
    inner = raw.get("json_state", raw)
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:
            inner = {}
    return inner if isinstance(inner, dict) else {}


def _log_texts(session_dir, inner):
    rows = inner.get("log")
    if not rows:
        raw = _load(os.path.join(session_dir, "log.json"))
        rows = raw if isinstance(raw, list) else (raw or {}).get("rows", [])
    out = []
    for r in rows or []:
        if isinstance(r, dict):
            out.append(str(r.get("text", "")))
        elif isinstance(r, str):
            out.append(r)
    return out


def _seat_of(text):
    m = re.search(r"\b(p[1-4])\b", text)
    return m.group(1) if m else None


def analyse(store_dir, prefix):
    rows = []
    for d in sorted(glob.glob(os.path.join(store_dir, "*/"))):
        meta = (_load(os.path.join(d, "meta.json")) or {}).get("row", {})
        name = meta.get("season_name")
        if not name or (prefix and not name.startswith(prefix)):
            continue

        inner = _inner_state(d)
        profiles = inner.get("player_profiles") or {}
        scores = inner.get("cumulative_shipped_score") or {}
        weapons = inner.get("weapons_used") or {}
        markers = inner.get("destroyed_harvester_markers") or []
        season_stats = inner.get("season_stats") or {}
        entities = inner.get("entities") or {}

        # current harvester fleet: built vs damaged (no free repair => damage sticks)
        built_by_seat = {s: int((season_stats.get(s) or {}).get("harvesters_built", 0))
                         for s in ("p1", "p2", "p3", "p4")}
        damaged_by_seat = {}
        for ent in entities.values():
            if not isinstance(ent, dict):
                continue
            owner = ent.get("owner")
            if owner and ent.get("damaged"):
                damaged_by_seat[owner] = damaged_by_seat.get(owner, 0) + 1

        # destroyed harvesters, by owning seat where the marker records it
        destroyed_by_seat = {}
        total_destroyed = 0
        if isinstance(markers, list):
            total_destroyed = len(markers)
            for mk in markers:
                seat = None
                if isinstance(mk, dict):
                    seat = mk.get("owner") or mk.get("seat")
                    if not seat:
                        ent = str(mk.get("entity_id") or mk.get("id") or "")
                        seat = _seat_of(ent)
                if seat:
                    destroyed_by_seat[seat] = destroyed_by_seat.get(seat, 0) + 1
        elif isinstance(markers, dict):
            total_destroyed = sum(len(v) if isinstance(v, list) else 1 for v in markers.values())
            for seat, v in markers.items():
                destroyed_by_seat[seat] = len(v) if isinstance(v, list) else 1

        emp_launch = {s: int((weapons.get(s) or {}).get("emp", 0)) for s in ("p1", "p2", "p3", "p4")}

        texts = _log_texts(d, inner)
        free_repairs = emp_hits = collisions = strandings = 0
        for t in texts:
            tl = t.lower()
            if "auto-repair" in tl or "free repair" in tl or "repaired for free" in tl:
                free_repairs += 1
            if "emp" in tl and ("fried" in tl or "disabled" in tl or "caught" in tl
                                 or "knocked" in tl or "stunned" in tl):
                emp_hits += 1
            if "collision" in tl:
                collisions += 1
            if "strand" in tl or "destroyed at dawn" in tl or "lost at dawn" in tl:
                strandings += 1

        rows.append({
            "name": name, "seed": meta.get("seed"),
            "profiles": profiles, "scores": scores,
            "emp_launch": emp_launch, "emp_hits": emp_hits,
            "destroyed_by_seat": destroyed_by_seat, "total_destroyed": total_destroyed,
            "built_by_seat": built_by_seat, "damaged_by_seat": damaged_by_seat,
            "collisions": collisions, "strandings": strandings,
            "free_repairs": free_repairs,
        })
    # de-dup by season name, keeping the last (latest) occurrence
    by_name = {}
    for r in rows:
        by_name[r["name"]] = r
    return [by_name[k] for k in sorted(by_name)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("store_dir", nargs="?", default="seasons")
    ap.add_argument("--prefix", default="Canon7_")
    args = ap.parse_args()

    rows = analyse(args.store_dir, args.prefix)
    if not rows:
        print(f"No sessions matching prefix '{args.prefix}' in {args.store_dir}/")
        return

    agg = {"emp_launch": 0, "emp_hits": 0, "free_repairs": 0,
           "collisions": 0, "strandings": 0, "built": 0, "damaged": 0}
    print("=" * 84)
    print(f"  CANONICAL BATTERY — {len(rows)} season(s)  ·  7 nights  ·  no free repairs")
    print("=" * 84)
    for r in rows:
        print(f"\n  {r['name']}   (seed {r['seed']})")
        for seat in ("p1", "p2", "p3", "p4"):
            prof = r["profiles"].get(seat)
            if not prof and seat not in r["scores"]:
                continue
            prof = prof or {}
            nm = prof.get("display_name", "?")
            tag = prof.get("tag", "?")
            col = prof.get("color", "?")
            sc = r["scores"].get(seat, 0)
            print(f"    {seat}  {nm:<18} [{tag}]  {col:<9} "
                  f"score={sc:>8.1f}  fleet_built={r['built_by_seat'].get(seat,0)}  "
                  f"damaged_at_end={r['damaged_by_seat'].get(seat,0)}  "
                  f"EMP_fired={r['emp_launch'].get(seat,0)}")
        winner = max(("p1", "p2"), key=lambda s: r["scores"].get(s, 0))
        wname = (r["profiles"].get(winner) or {}).get("display_name", winner)
        print(f"    -> winner: {wname} ({winner})   collisions={r['collisions']}   "
              f"strandings={r['strandings']}   free_repairs={r['free_repairs']}")
        agg["emp_launch"] += sum(r["emp_launch"].values())
        agg["emp_hits"] += r["emp_hits"]
        agg["free_repairs"] += r["free_repairs"]
        agg["collisions"] += r["collisions"]
        agg["strandings"] += r["strandings"]
        agg["built"] += sum(r["built_by_seat"].values())
        agg["damaged"] += sum(r["damaged_by_seat"].values())

    print("\n" + "=" * 84)
    print("  BATTERY TOTALS")
    print("=" * 84)
    print(f"    harvesters built        : {agg['built']}")
    print(f"    harvesters damaged @end : {agg['damaged']}   (stranded/EMP'd, NOT repaired)")
    print(f"    drop collisions         : {agg['collisions']}")
    print(f"    dawn strandings         : {agg['strandings']}")
    print(f"    EMP warheads fired      : {agg['emp_launch']}")
    print(f"    EMP hit events (log)    : {agg['emp_hits']}")
    print(f"    FREE/AUTO repairs       : {agg['free_repairs']}   (MUST be 0 under canonical rules)")


if __name__ == "__main__":
    main()
