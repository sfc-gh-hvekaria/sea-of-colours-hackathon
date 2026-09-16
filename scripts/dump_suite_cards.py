#!/usr/bin/env python3
"""Dump the FULL agent card for every frozen turn in the suite, into one labelled
archive directory.

Why this exists
---------------
``scripts/turn_suite.py`` scores each turn against shape predicates and keeps the
compiled moves, but it does NOT keep the card — the prompt the model actually
read plus the reasoning it wrote back. Predicates tell you *whether* a run
passed; only the card tells you *why*, and only a pair of cards from before and
after a change lets you look at a night and say "yes, that is better play".

The card is deterministic in the board: the same snapshot and the same harness
code always render the same prompt. So the honest way to compare two versions of
the harness is to dump the whole set at each one and diff turn by turn. That is
what this script is for::

    # before a change (stash your edits first, or check out the base commit)
    python scripts/dump_suite_cards.py --label pre_phaseA
    # after
    python scripts/dump_suite_cards.py --label post_phaseA

Each run writes ``reports/turn_suite/cards/<label>/<turn_id>.txt`` plus a
``MANIFEST.json`` recording the git commit, the working-tree state and the
snapshot each card came from — so an archive can never be mistaken for one taken
at a different revision.

Read-only: every card goes through ``harness.run(..., submit=False)`` against the
frozen snapshot, exactly as ``advise_v12.py`` does. Nothing is submitted and the
snapshots stay inert.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Mapping

_ROOT = Path(__file__).resolve().parents[1]
_SUITE = _ROOT / "reports" / "turn_suite" / "suite.json"
_CARDS = _ROOT / "reports" / "turn_suite" / "cards"
_LATENCY_MS = re.compile(r"\bms=\d+\b")


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "?"


def _turns() -> List[Dict[str, Any]]:
    return json.loads(_SUITE.read_text(encoding="utf-8"))["turns"]


def _dump_one(turn: Mapping[str, Any], out_dir: str) -> Dict[str, Any]:
    """One card. Runs in its own process so the set can be dumped in parallel."""
    os.environ.setdefault("SOC_BACKEND", "snowflake")
    tid = str(turn["id"])
    dest = Path(out_dir) / f"{tid}.txt"
    t0 = time.time()
    rc = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "advise_v12.py"),
         "--session", str(turn["snapshot"]), "--seat", str(turn["seat"]),
         "--full", "--prompt", "--out", str(dest)],
        cwd=_ROOT, capture_output=True, text=True,
    )
    ok = rc.returncode == 0 and dest.exists()
    if ok:
        # The card is otherwise byte-identical run to run — prompt, reasoning and
        # plan all reproduce exactly — so the ONLY thing that would show up in
        # every diff is the round-trip latency. Blank it and a diff between two
        # archives contains nothing but the effect of the change.
        dest.write_text(
            _LATENCY_MS.sub("ms=<normalised>", dest.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
    return {
        "id": tid,
        "snapshot": turn["snapshot"],
        "seat": turn["seat"],
        "day": turn.get("day"),
        "ok": ok,
        "bytes": dest.stat().st_size if dest.exists() else 0,
        "ms": int((time.time() - t0) * 1000),
        "error": "" if ok else (rc.stderr or rc.stdout)[-400:],
    }


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dump_suite_cards", description=__doc__)
    p.add_argument("--label", required=True,
                   help="archive name, e.g. pre_phaseA / post_phaseC")
    p.add_argument("--jobs", type=int, default=5,
                   help="cards to dump in parallel (each is one LLM call)")
    p.add_argument("--only", nargs="*", default=None,
                   help="turn ids to dump (default: all)")
    args = p.parse_args(argv)

    turns = [t for t in _turns()
             if args.only is None or str(t["id"]) in set(args.only)]
    out_dir = _CARDS / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"dumping {len(turns)} card(s) -> {out_dir}", flush=True)
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futs = {pool.submit(_dump_one, t, str(out_dir)): t for t in turns}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            flag = "ok " if r["ok"] else "FAIL"
            print(f"  {flag} {r['id']:<26} {r['bytes']:>7}b  {r['ms'] / 1000:.0f}s"
                  + ("" if r["ok"] else f"\n       {r['error']}"), flush=True)

    dirty = _git("status", "--porcelain",
                 "sea_of_colours/orchestrator_2/harnesses")
    manifest = {
        "label": args.label,
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "commit": _git("rev-parse", "HEAD"),
        "commit_subject": _git("log", "-1", "--format=%s"),
        # An archive taken on a dirty tree is still useful, but it is NOT the
        # commit it names, and a diff against it will mislead unless that is
        # recorded here.
        "harness_tree_clean": not bool(dirty),
        "harness_uncommitted": dirty.splitlines(),
        "cards": sorted(results, key=lambda r: r["id"]),
    }
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )
    n_ok = sum(1 for r in results if r["ok"])
    print(f"\n{n_ok}/{len(results)} card(s) written to {out_dir}")
    if dirty:
        print("NOTE: harness tree was DIRTY — this archive is not a clean "
              "snapshot of the named commit (recorded in MANIFEST.json).")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
