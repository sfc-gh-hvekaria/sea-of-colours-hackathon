#!/usr/bin/env python3
"""Re-ask a captured planning card N times and report what the agent picks.

A capture written by ``run_season_v11_capture.py`` stores the THINK and PLAN
prompts EXACTLY as they were sent, and those prompts already carry the whole
reconstructed memory for that moment: the STRATEGY JOURNAL, LAST NIGHT (intent,
what you saw, engine execution log), WORLD VIEW, OUT-OF-GRID and the OPTION
MENU. The chat invoker is stateless. So one night's decision can be replayed
without an engine, a session or a store — the card IS the state.

That makes two questions answerable cheaply:

  * Is a bad pick STABLE or was it one unlucky sample? Run ``-n 20`` and read
    the distribution.
  * Does a card change FIX it? Re-run with ``--patch`` (edits applied to the
    prompt text) and compare the two distributions.

What this canNOT do: show the consequences of a different pick, or reflect a
harness CODE change. Both need the board state, and the capture only keeps a
one-line board summary. Card text in, decision out.

Stages
------
``full``  (default) re-ask THINK, splice the fresh analysis into the PLAN card,
          re-ask PLAN. The honest end-to-end replay.
``plan``  re-ask PLAN alone against the ORIGINAL analysis. Isolates the commit
          step from reasoning variance.
``think`` re-ask THINK alone. Prose only, no decision.

Usage
-----
    # 20 samples of r3 night 7
    python scripts/replay_card.py 56f4acf9:7 -n 20

    # A/B a card edit
    python scripts/replay_card.py 56f4acf9:7 -n 20 --patch patches/no_tail_value.json

A patch file is a list of ``{"find": ..., "replace": ..., "where": ...}``
objects (``where`` is ``think``/``plan``/``both``, default ``both``). A ``find``
that is not present is a hard error — a silently inert patch would quietly
invalidate the comparison.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# The replay must not drift from production, so the model, token caps and
# response schema are read off the harness itself rather than re-declared.
from sea_of_colours.orchestrator_2.cortex_chat import CortexChatInvoker
from sea_of_colours.orchestrator_2.harnesses.tabula_v12._v7 import (
    directive as directive_mod,
)
from sea_of_colours.orchestrator_2.harnesses.tabula_v12 import harness as h12

_ANALYSIS_OPEN = "=== YOUR ANALYSIS (from your think pass — commit it now) ===\n<<<\n"
_ANALYSIS_CLOSE = "\n>>>"

_REPORTS = _ROOT / "reports"


# ──────────────────────────────────────────────────────────────────────────
# locating a card
# ──────────────────────────────────────────────────────────────────────────
def resolve_card(spec: str) -> Path:
    """``path/to/turn_x.json`` or ``<session-prefix>:<day>``."""
    direct = Path(spec)
    if direct.is_file():
        return direct
    if ":" not in spec:
        raise SystemExit(
            f"cannot resolve {spec!r}: give a file path or '<session>:<day>'"
        )
    sess, _, day = spec.partition(":")
    hits = sorted(_REPORTS.glob(f"*/{sess}*/turn_*day{day}_*planning.json"))
    if not hits:
        raise SystemExit(f"no capture matching session {sess!r} day {day}")
    if len({h.parent for h in hits}) > 1:
        opts = "\n  ".join(str(h) for h in hits)
        raise SystemExit(f"{sess!r} is ambiguous:\n  {opts}")
    return hits[0]


def load_card(path: Path) -> Dict[str, Any]:
    blob = json.loads(path.read_text(encoding="utf-8"))
    extras = blob.get("extras") or {}
    think = extras.get("thinker_prompt") or ""
    plan = extras.get("plan_prompt") or ""
    if not think or not plan:
        raise SystemExit(
            f"{path} has no captured prompts (only split-stage v11/v12 turns "
            "can be replayed)"
        )
    return {
        "path": path,
        "day": blob.get("day"),
        "session": path.parent.name,
        "think_prompt": think,
        "plan_prompt": plan,
        "reasoning": extras.get("thinker_reasoning") or "",
        "picked": list(extras.get("selected_option_ids") or []),
        "menu": list(extras.get("option_menu_ids") or []),
        "intent": extras.get("agent_intent") or "",
        "moves": extras.get("final_moves") or [],
        "packager_log": extras.get("packager_log") or [],
    }


# ──────────────────────────────────────────────────────────────────────────
# patching
# ──────────────────────────────────────────────────────────────────────────
def apply_patches(
    think: str, plan: str, patches: Sequence[Mapping[str, Any]],
) -> Tuple[str, str, List[str]]:
    log: List[str] = []
    for i, p in enumerate(patches, 1):
        find = str(p.get("find") or "")
        repl = str(p.get("replace") or "")
        where = str(p.get("where") or "both").lower()
        if not find:
            raise SystemExit(f"patch {i}: empty 'find'")
        touched = 0
        if where in ("think", "both"):
            n = think.count(find)
            think = think.replace(find, repl)
            touched += n
        if where in ("plan", "both"):
            n = plan.count(find)
            plan = plan.replace(find, repl)
            touched += n
        if touched == 0:
            raise SystemExit(
                f"patch {i}: {find[:70]!r} not found in {where} card — "
                "an inert patch would invalidate the comparison"
            )
        log.append(f"patch {i}: {touched} site(s) in {where}")
    return think, plan, log


def splice_analysis(plan_prompt: str, analysis: str) -> str:
    """Swap the frozen THINK prose in a PLAN card for a freshly generated one."""
    i = plan_prompt.find(_ANALYSIS_OPEN)
    if i < 0:
        raise SystemExit("PLAN card has no analysis block to splice")
    start = i + len(_ANALYSIS_OPEN)
    end = plan_prompt.find(_ANALYSIS_CLOSE, start)
    if end < 0:
        raise SystemExit("PLAN card analysis block is unterminated")
    return plan_prompt[:start] + analysis + plan_prompt[end:]


# ──────────────────────────────────────────────────────────────────────────
# one sample
# ──────────────────────────────────────────────────────────────────────────
def run_sample(
    idx: int, think_prompt: str, plan_prompt: str, frozen: str, stage: str,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"i": idx}
    t0 = time.time()
    analysis = frozen
    if stage in ("full", "think"):
        thinker = CortexChatInvoker(
            model=h12._THINKER_CHAT_MODEL,
            response_format=None,
            max_completion_tokens=h12._THINK_CHAT_MAX_TOKENS,
        )
        res = thinker.invoke(
            think_prompt, wallclock_cap_s=h12._THINK_CHAT_WALLCLOCK_S,
        )
        analysis = str(res.get("response") or "").strip()
        out["think_ms"] = int((time.time() - t0) * 1000)
        out["analysis"] = analysis
    if stage == "think":
        return out

    prompt = splice_analysis(plan_prompt, analysis) if stage == "full" else plan_prompt
    planner = CortexChatInvoker(
        model=h12._THINKER_CHAT_MODEL,
        response_format=h12.v10_chat_schema.DECISION_RESPONSE_FORMAT,
        max_completion_tokens=h12._PLAN_CHAT_MAX_TOKENS,
    )
    t1 = time.time()
    text = ""
    directive = None
    for attempt in range(2):
        text = str(
            planner.invoke(
                prompt, wallclock_cap_s=h12._PLAN_CHAT_WALLCLOCK_S,
            ).get("response") or ""
        )
        directive, _ = directive_mod.parse_directive_json(text)
        if directive is not None:
            break
        out["retried"] = True
    out["plan_ms"] = int((time.time() - t1) * 1000)
    # Raw plan IDs only — sanitize_directive() needs the board state, which a
    # capture does not keep. Unsanitized picks are the right unit anyway: they
    # are what the MODEL chose, before any downstream clamping.
    out["picks"] = list(getattr(directive, "plan", []) or []) if directive else []
    out["parsed"] = directive is not None
    intent, reflection = _intent_reflection(text)
    out["intent"] = intent
    out["reflection"] = reflection
    return out


def _intent_reflection(text: str) -> Tuple[str, str]:
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r'"intent"\s*:\s*"([^"]{0,400})"', text)
        return (m.group(1) if m else ""), ""
    if not isinstance(obj, dict):
        return "", ""
    return str(obj.get("intent") or ""), str(obj.get("reflection") or "")


# ──────────────────────────────────────────────────────────────────────────
# reporting
# ──────────────────────────────────────────────────────────────────────────
def report(card: Mapping[str, Any], samples: Sequence[Mapping[str, Any]],
           stage: str) -> None:
    n = len(samples)
    print()
    print("=" * 74)
    print(f"CARD    {card['session']}  day {card['day']}   ({stage} stage, n={n})")
    print(f"        {card['path']}")
    print(f"MENU    {', '.join(card['menu'])}")
    print(f"ORIGINAL PICK  {card['picked']}")
    print("=" * 74)

    if stage == "think":
        for s in samples:
            print(f"\n--- sample {s['i']} ({s.get('think_ms')}ms) ---")
            print((s.get("analysis") or "")[:1200])
        return

    ok = [s for s in samples if s.get("parsed")]
    if len(ok) < n:
        print(f"!! {n - len(ok)} sample(s) failed to parse a decision")

    per_option: Counter = Counter()
    combos: Counter = Counter()
    for s in ok:
        picks = s["picks"]
        per_option.update(picks)
        combos[" + ".join(picks) or "(empty)"] += 1

    print("\nPICK FREQUENCY (per option)")
    for oid in card["menu"]:
        c = per_option.get(oid, 0)
        if not c:
            continue
        bar = "#" * int(round(30 * c / max(1, len(ok))))
        star = " *" if oid in card["picked"] else "  "
        print(f"  {oid:<14}{star} {c:>3}/{len(ok)}  {bar}")
    stray = sorted(set(per_option) - set(card["menu"]))
    for oid in stray:
        print(f"  {oid:<14} !! {per_option[oid]:>3}/{len(ok)}  NOT ON MENU")

    print("\nEXACT COMBINATIONS")
    for combo, c in combos.most_common():
        print(f"  {c:>3}/{len(ok)}  {combo}")

    orig = " + ".join(card["picked"])
    print(f"\n  (* = in the original pick;  original combo was: {orig})")

    lat = [s.get("think_ms", 0) + s.get("plan_ms", 0) for s in ok]
    if lat:
        print(f"\nlatency  mean {sum(lat)//len(lat)}ms  min {min(lat)}  max {max(lat)}")

    print("\nSAMPLE INTENTS")
    for s in ok[:6]:
        print(f"  [{s['i']}] {(s.get('intent') or '')[:150]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("card", help="capture path, or '<session-prefix>:<day>'")
    ap.add_argument("-n", "--samples", type=int, default=10)
    ap.add_argument("--stage", choices=("full", "plan", "think"), default="full")
    ap.add_argument("--patch", help="JSON file of {find,replace,where} edits")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", help="write raw samples to this JSON file")
    ap.add_argument("--show-card", action="store_true",
                    help="print the (patched) THINK card and exit without calling")
    args = ap.parse_args()

    card = load_card(resolve_card(args.card))
    think, plan = card["think_prompt"], card["plan_prompt"]

    if args.patch:
        patches = json.loads(Path(args.patch).read_text(encoding="utf-8"))
        think, plan, plog = apply_patches(think, plan, patches)
        for line in plog:
            print(f"  {line}")

    if args.show_card:
        print(think)
        return

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        samples = list(pool.map(
            lambda i: run_sample(i, think, plan, card["reasoning"], args.stage),
            range(1, args.samples + 1),
        ))

    report(card, samples, args.stage)

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "card": str(card["path"]),
                    "session": card["session"],
                    "day": card["day"],
                    "stage": args.stage,
                    "patch": args.patch,
                    "original_pick": card["picked"],
                    "menu": card["menu"],
                    "samples": samples,
                },
                indent=2, default=str,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
