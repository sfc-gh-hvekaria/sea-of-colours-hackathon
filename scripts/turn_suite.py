"""Regression suite over frozen turns.

Each turn is a snapshot (permanent, inert) plus predicates over the COMPILED
moves. The suite clones every turn N times, plays it with the chosen harness,
and reports how often the agent produced the canonical shape.

    # score everything, six runs each
    SOC_BACKEND=snowflake python scripts/turn_suite.py --runs 6

    # one turn, against a new harness
    SOC_BACKEND=snowflake python scripts/turn_suite.py \
        --only own_seam_three_pures --harness tabula_v13 --runs 6

Predicates are deliberately coarse — they check the geometry the canonical
turns on (did it land on the pure, did it blind the finder), not the whole
plan. The model is stochastic, so read the pass RATE, not a single run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sea_of_colours.game.tuning import probe_vision_radius  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SUITE = _REPO_ROOT / "reports" / "turn_suite" / "suite.json"


def _state_of(store: Any, session_id: str) -> Dict[str, Any]:
    blob = store.load_session(session_id)["json_state"]
    for _ in range(3):  # some snapshots come back double-encoded
        if isinstance(blob, str):
            blob = json.loads(blob)
        else:
            break
    return blob


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("turn_suite")
    p.add_argument("--suite", default=str(_SUITE))
    p.add_argument("--only", nargs="*", default=[], help="turn ids to run")
    p.add_argument("--harness", default=None, help="override every turn's harness")
    p.add_argument("--runs", type=int, default=6)
    p.add_argument("--outdir", default="reports/turn_suite/results")
    p.add_argument("--include-unscored", action="store_true",
                   help="also run turns that have no canonical yet")
    p.add_argument("--jobs", type=int, default=1,
                   help="play this many turn-runs in parallel (separate processes)")
    return p


def _play_once(snap_id: str, seat: str, harness: Optional[str],
               day: Optional[int]) -> Dict[str, Any]:
    """Clone the frozen turn, play it once, return the COMPILED moves.

    Runs in its own process — snowpark connections are not fork-safe, so the
    pool must use spawn. Scoring stays in the parent, which already holds the
    board, so the worker never loads it twice.
    """
    import os
    import time as _time
    os.environ.setdefault("SOC_BACKEND", "snowflake")
    from sea_of_colours.snowpark import backend as soc_backend
    from sea_of_colours.snowpark import snapshot as soc_snapshot
    from sea_of_colours.orchestrator_2.runtime import run_agent_turn

    store = soc_backend.get_store()
    started = _time.time()
    rid = None
    try:
        rid = soc_snapshot.clone_for_run(store, snap_id)
        res = run_agent_turn(store, rid, seat, agent_label=harness) or {}
        d = int(day or store.load_session(rid)["day"])
        moves = (store.list_policies(rid, d) or {}).get(seat) or []
        # The ORDERS the agent issued, kept separate from the moves that
        # reached the board. When they disagree, the compiler authored the
        # night (OBS-27) and the two must be scored apart.
        extras = res.get("extras") or {}
        directive = extras.get("thinker_directive") or {}
        return {"clone": rid, "moves": moves, "error": None,
                "plan_ids": list(directive.get("plan") or []),
                "selected_option_ids": list(extras.get("selected_option_ids") or []),
                "interventions": list(extras.get("sanitizer_changes") or []),
                "packager_log": list(extras.get("packager_log") or []),
                "ms": int((_time.time() - started) * 1000)}
    except Exception as exc:
        return {"clone": rid, "moves": [], "error": str(exc),
                "plan_ids": [], "selected_option_ids": [],
                "interventions": [], "packager_log": [],
                "ms": int((_time.time() - started) * 1000)}
    finally:
        if rid:
            try:
                store.delete_session(rid)
            except Exception:
                pass


def _cells(moves: Sequence[Dict[str, Any]], *kinds: str) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for m in moves:
        if m.get("a") not in kinds:
            continue
        c = m.get("at") or m.get("to")
        if isinstance(c, (list, tuple)) and len(c) == 2:
            out.append((int(c[0]), int(c[1])))
    return out


# RED purity bands, mirroring GameSession's tier split (session.py ~l.680).
_TRACE, _VEIN, _MASS = 50, 150, 254
_GREEN_CHANNEL, _RED_CHANNEL = 1, 2


def _covers(probe: Tuple[int, int], cell: Tuple[int, int]) -> bool:
    """Is ``cell`` inside a probe's vision disk? (engine's euclidean radius)."""
    r = probe_vision_radius()
    return (probe[0] - cell[0]) ** 2 + (probe[1] - cell[1]) ** 2 <= r * r


class Board:
    """Engine truth for one frozen turn, so predicates can describe SHAPE.

    Fixed-cell predicates rot: they fail a correct-but-different play and pass a
    wrong one that happens to hit the coordinate. Worse, a canonical can name a
    cell the seat cannot see (see ``scripts/_audit_canonical_fog.py``). Reading
    the board lets a predicate say "land on a pure" instead of "land on (16,6)".
    """

    def __init__(self, state: Dict[str, Any], seat: str) -> None:
        self.state, self.seat = state, seat
        self.grid = state.get("grid") or []
        ents = (state.get("entities") or {}).values()
        self.own_harvesters = {
            e["id"] for e in ents
            if e.get("type") == "harvester" and e.get("owner") == seat
        }
        self.enemy_probe_cells = {
            (e["x"], e["y"]) for e in ents
            if e.get("type") == "probe" and e.get("owner") != seat
            and e.get("x") is not None
        }
        # Redsign smears, kept PER BEACON. Unioning them lets "commit to the
        # seam" be satisfied by two units on two different seams, which is the
        # opposite of concentration.
        self.smears: List[Dict[Tuple[int, int], float]] = []
        for sign in (state.get("redsign") or []):
            cells = {(int(e[0]), int(e[1])): float(e[2])
                     for e in (sign.get("cells") or []) if len(e) >= 3}
            if cells:
                self.smears.append(cells)
        self.smear: Dict[Tuple[int, int], float] = {}
        for s in self.smears:
            for c, w in s.items():
                self.smear[c] = max(self.smear.get(c, 0.0), w)
        stock = (state.get("weapon_stock") or {})
        self.chaff_in_play = any(
            int((v or {}).get("chaff", 0)) > 0 for v in stock.values()
        )
        self.n_players = len(state.get("players") or [])

    def _cell(self, x: int, y: int) -> Optional[Tuple[int, int]]:
        try:
            ch, val = self.grid[y][x]
            return int(ch), int(val)
        except Exception:
            return None

    def is_green(self, x: int, y: int) -> bool:
        c = self._cell(x, y)
        return bool(c and c[0] == _GREEN_CHANNEL)

    def tier(self, x: int, y: int) -> Optional[str]:
        c = self._cell(x, y)
        if not c or c[0] != _RED_CHANNEL:
            return None
        val = c[1]
        if val <= _TRACE:
            return "trace"
        if val <= _VEIN:
            return "vein"
        if val <= _MASS:
            return "mass"
        return "pure"


def _runs(moves: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split the move list into per-harvester outings, in execution order."""
    runs: List[Dict[str, Any]] = []
    open_runs: Dict[str, Dict[str, Any]] = {}
    for idx, m in enumerate(moves):
        act, unit = m.get("a"), m.get("unit")
        if act == "drop" and unit:
            open_runs[unit] = {"unit": unit, "start": idx, "end": None,
                               "cells": _cells([m], "drop")}
            runs.append(open_runs[unit])
        elif act == "step" and unit in open_runs:
            open_runs[unit]["cells"] += _cells([m], "step")
        elif act == "pickup" and unit in open_runs:
            open_runs[unit]["end"] = idx
            open_runs.pop(unit)
    return runs


def _score(moves: Sequence[Dict[str, Any]], expect: Dict[str, Any],
           board: Optional[Board] = None,
           run: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    drops = _cells(moves, "drop")
    steps = _cells(moves, "step")
    probes = _cells(moves, "probe")
    walked = set(drops) | set(steps)
    res: Dict[str, bool] = {}

    # ---- fixed-cell predicates (legacy; prefer the shape ones below) --------
    if "probe_at" in expect:
        want = {tuple(c) for c in expect["probe_at"]}
        res["probe_at"] = want <= set(probes)
    if "drop_at_any" in expect:
        want = {tuple(c) for c in expect["drop_at_any"]}
        res["drop_at_any"] = bool(want & set(drops))
    if "visit_all" in expect:
        want = {tuple(c) for c in expect["visit_all"]}
        res["visit_all"] = want <= walked
    if "forbid" in expect:
        bad = {tuple(c) for c in expect["forbid"]}
        res["forbid"] = not (bad & walked)
    if "max_probes" in expect:
        res["max_probes"] = len(probes) <= int(expect["max_probes"])

    if board is None:
        return res

    # ---- shape predicates (board-derived; no coordinates in the suite) ------
    runs = _runs(moves)
    route = [c for r in runs for c in r["cells"]]

    if expect.get("no_green"):
        res["no_green"] = not any(board.is_green(*c) for c in route)

    if expect.get("take_pure"):
        res["take_pure"] = any(board.tier(*c) == "pure" for c in route)

    if expect.get("pure_first"):
        # Tempo: if the night takes a pure at all, the FIRST outing must be the
        # one that takes it — a pure held from hour 2 cannot be raced away.
        pure_runs = [i for i, r in enumerate(runs)
                     if any(board.tier(*c) == "pure" for c in r["cells"])]
        res["pure_first"] = bool(pure_runs) and pure_runs[0] == 0

    if expect.get("no_wake_reentry"):
        # OBS-31 / OBS-42: a later wave must not re-enter ground an earlier one
        # already stripped — it auto-harvests our own synthetic green.
        seen: set = set()
        clean = True
        for r in runs:
            if seen & set(r["cells"]):
                clean = False
                break
            seen |= set(r["cells"])
        res["no_wake_reentry"] = clean

    if expect.get("deploy_all"):
        res["deploy_all"] = (
            {r["unit"] for r in runs} >= board.own_harvesters
        )

    if "seam_commitment" in expect:
        # OBS-44 floor: however bad the tempo, commit N units to ONE seam.
        # Counted per beacon — two units on two different seams is spread, not
        # commitment, and is exactly the failure this predicate exists to catch.
        want = int(expect["seam_commitment"])
        best = max(
            (sum(1 for r in runs if r["cells"] and r["cells"][0] in s)
             for s in board.smears),
            default=0,
        )
        res["seam_commitment"] = best >= want

    if expect.get("probes_after_harvesters"):
        # OBS-27: the packager injected probes BETWEEN outings, delaying the
        # second harvester by three hours on a tempo-critical night.
        #
        # OBS-50 — but a wave's OWN legality probe must precede its drop, and
        # counting that as interleaving failed the canonical play. On
        # `own_seam_late_3opp` the SECURE_MASS wave probes (14,6) to legalise
        # its own landing at (17,4): three runs played the canonical
        # SMASH_GRAB + SECURE_MASS and were scored as failures for it. Only a
        # probe the NEXT outing does not need counts as a delay.
        gaps = [(r["end"], nxt["start"], nxt)
                for r, nxt in zip(runs, runs[1:])
                if r["end"] is not None]
        interleaved = False
        for i, m in enumerate(moves):
            if m.get("a") != "probe":
                continue
            at = m.get("at")
            for lo, hi, nxt in gaps:
                if not (lo < i < hi):
                    continue
                if at is not None and nxt["cells"] and _covers(
                    tuple(at), tuple(nxt["cells"][0])
                ):
                    continue  # this probe is what makes the next drop legal
                interleaved = True
        res["probes_after_harvesters"] = not interleaved

    if expect.get("denial_probe"):
        res["denial_probe"] = bool(set(probes) & board.enemy_probe_cells)

    if "max_steps_past_pure" in expect:
        # The wager past H1 stakes the whole hold, so cap the TAIL — the steps
        # that bank nothing while re-risking everything already aboard.
        #
        # Measured from the LAST pure, not the first. Measuring from the first
        # counted a walk from one pure to the next as tail, which made
        # `own_seam_three_pures` unpassable BY ITS OWN CANONICAL: that canonical
        # is a four-cell route over three pures, and the cap is 1. Worse, it
        # scored 100% while the agent took the single pure and left ~1500 on the
        # board, so the predicate was actively rewarding the play the canonical
        # calls wrong. Crossing a pure is never padding.
        cap, ok = int(expect["max_steps_past_pure"]), True
        for r in runs:
            tiers = [board.tier(*c) for c in r["cells"]]
            if "pure" not in tiers:
                continue
            last_pure = len(tiers) - 1 - tiers[::-1].index("pure")
            if len(r["cells"]) - 1 - last_pure > cap:
                ok = False
        res["max_steps_past_pure"] = ok

    if "min_steps_past_pure" in expect:
        # The mirror case: on a board nobody can punish, a short grab leaves
        # money behind — the wager is nearly free, so it must be taken.
        floor = int(expect["min_steps_past_pure"])
        best = 0
        for r in runs:
            tiers = [board.tier(*c) for c in r["cells"]]
            if "pure" in tiers:
                best = max(best, len(r["cells"]) - 1 - tiers.index("pure"))
        res["min_steps_past_pure"] = best >= floor

    if expect.get("comb_gradient") and board.smear:
        # A blind comb should ride the smear's high-probability cells, not
        # wander it. Beat the smear's own mean weight.
        on_smear = [board.smear[c] for c in route if c in board.smear]
        mean_all = sum(board.smear.values()) / len(board.smear)
        res["comb_gradient"] = bool(on_smear) and (
            sum(on_smear) / len(on_smear) >= mean_all
        )

    if expect.get("compiler_clean") and run is not None:
        # OBS-27: the agent's orders must reach the board unaltered. A drop or
        # a substitution here means the night was authored downstream, so a
        # failing predicate above may not be the agent's fault at all.
        #
        # OBS-51 — this used to match the SUBSTRINGS "skip"/"drop" anywhere in
        # the packager log, which was written when the packager only spoke when
        # it rewrote something. Since Workstream C it mostly REPORTS, and its
        # advisories quote the move they are declining to touch: "drop [18,3] is
        # the SECOND landing ... KEPT" scored as an intervention on the strength
        # of the word "drop", in a sentence whose last word is KEPT. Match the
        # alteration vocabulary instead, and never a line that says KEPT.
        altered = [
            s for s in (run.get("packager_log") or [])
            if "KEPT" not in s and (
                s.startswith(("cut ", "skip ", "completion:"))
                or "could not compile" in s
                or "capped chain" in s
            )
        ]
        res["compiler_clean"] = not (run.get("interventions") or altered)

    if expect.get("no_repeat_pure") and not board.chaff_in_play:
        # Repeating the pure only insures against CHAFF (OBS-44). With no chaff
        # on the board a second pass buys nothing and pays green.
        pure_drops = [c for c in drops if board.tier(*c) == "pure"]
        res["no_repeat_pure"] = len(pure_drops) == len(set(pure_drops))

    return res


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    from sea_of_colours.snowpark import backend as soc_backend

    suite = json.loads(Path(args.suite).read_text())
    turns = suite.get("turns") or []
    if args.only:
        turns = [t for t in turns if t.get("id") in set(args.only)]
    if not args.include_unscored:
        turns = [t for t in turns if t.get("expect")]
    if not turns:
        print("no turns selected (unscored turns need --include-unscored)")
        return 1

    store = soc_backend.get_store()
    outdir = _REPO_ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    report: List[Dict[str, Any]] = []

    live = [t for t in turns if store.load_session(t["snapshot"])]
    for t in turns:
        if t not in live:
            print(f"{t['id']}: snapshot missing ({t['snapshot']}) — skipped")

    # Boards come from the FROZEN snapshots, not the clones: predicates describe
    # the position the agent was asked to play.
    boards = {t["id"]: Board(_state_of(store, t["snapshot"]), t["seat"]) for t in live}
    tasks = [(t, i) for t in live for i in range(1, args.runs + 1)]
    played: Dict[Tuple[str, int], Dict[str, Any]] = {}

    if args.jobs > 1 and len(tasks) > 1:
        import concurrent.futures as _cf
        import multiprocessing as _mp
        ctx = _mp.get_context("spawn")  # snowpark connections are NOT fork-safe
        print(f"playing {len(tasks)} turn-run(s) across {args.jobs} process(es)…")
        with _cf.ProcessPoolExecutor(max_workers=args.jobs, mp_context=ctx) as ex:
            futs = {
                ex.submit(_play_once, t["snapshot"], t["seat"],
                          args.harness or t.get("harness"), t.get("day")): (t["id"], i)
                for (t, i) in tasks
            }
            for done, fut in enumerate(_cf.as_completed(futs), 1):
                key = futs[fut]
                played[key] = fut.result()
                print(f"  [{done}/{len(tasks)}] {key[0]} run {key[1]}"
                      + (f" FAILED — {played[key]['error']}" if played[key]["error"] else ""))
    else:
        for t, i in tasks:
            played[(t["id"], i)] = _play_once(
                t["snapshot"], t["seat"], args.harness or t.get("harness"), t.get("day"))

    for t in live:
        tid = t["id"]
        harness = args.harness or t.get("harness")
        expect = t.get("expect") or {}
        board = boards[tid]
        print(f"\n=== {tid} · {t['snapshot']} · {t['seat']} · {harness} ===")
        runs: List[Dict[str, Any]] = []
        for i in range(1, args.runs + 1):
            got = played.get((tid, i)) or {}
            if got.get("error"):
                print(f"  run {i}: FAILED — {got['error']}")
                continue
            marks = _score(got.get("moves") or [], expect, board, got)
            ok = all(marks.values()) if marks else None
            runs.append({"run": i, "clone": got.get("clone"),
                         "moves": got.get("moves"), "marks": marks,
                         "pass": ok, "ms": got.get("ms"),
                         "plan_ids": got.get("plan_ids"),
                         "selected_option_ids": got.get("selected_option_ids"),
                         "interventions": got.get("interventions"),
                         "packager_log": got.get("packager_log")})
            flag = "PASS" if ok else ("—" if ok is None else "fail")
            detail = " ".join(f"{k}={'y' if v else 'n'}" for k, v in marks.items())
            print(f"  run {i}: {flag}  {detail}")
            ordered = got.get("selected_option_ids") or got.get("plan_ids") or []
            if ordered:
                print(f"          ORDERED: {', '.join(str(o) for o in ordered)}")
            for s in (got.get("interventions") or [])[:3]:
                print(f"          compiler: {s}")
        scored = [r for r in runs if r["pass"] is not None]
        rate = sum(1 for r in scored if r["pass"]) / len(scored) if scored else 0.0
        # Only real predicates — `expect` also carries prose (doctrine/note/
        # prefer) for the human reader, which must not be reported as 0%.
        checked = sorted({k for r in scored for k in r["marks"]})
        per_pred = {
            k: sum(1 for r in scored if r["marks"].get(k)) / max(len(scored), 1)
            for k in checked
        }
        print(f"  -> {rate:.0%} full pass ({len(scored)} run(s));  "
              + "  ".join(f"{k} {v:.0%}" for k, v in per_pred.items()))
        report.append({"id": tid, "snapshot": t["snapshot"], "harness": harness,
                       "runs": len(scored), "pass_rate": rate,
                       "per_predicate": per_pred, "detail": runs})

    (outdir / "latest.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("\n================ SUITE ================")
    for r in report:
        print("  %-26s %-14s %3.0f%%  (%d runs)"
              % (r["id"], r["harness"], 100 * r["pass_rate"], r["runs"]))
    print(f"\nwritten to {outdir / 'latest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
