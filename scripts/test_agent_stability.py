#!/usr/bin/env python3
"""Loop-test rig for the Sea of Colours anti-rambling experiments.

Fires the SAME frozen prompt at a Cortex agent N times and records a
clean-agent scorecard per run: wallclock, output size, ramble count,
preamble count, first/final candidate ID mentioned (stability), tool
call success, wallclock-cap hits.

Purpose: iterate on prompt tweaks in seconds against the SCRATCH agent
without running full seasons. Only promote a variant to PILOT_V3 after
it beats baseline on ≥ 4/6 metrics across the fixture set.

Usage::

    PYTHONPATH=. python scripts/test_agent_stability.py \\
        --fixture scripts/fixtures/d3s9_night_ramble.txt \\
        --agent SOC_RED_REAPER_SCRATCH \\
        --repeats 5

Adds one row per run to a CSV under --out (default:
scripts/fixtures/results.csv) so consecutive experiments accumulate a
comparable history.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure repo root is on sys.path so `sea_of_colours.*` imports resolve
# when the script is run directly from anywhere.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sea_of_colours.agent.cortex_invoker import CortexAgentInvoker  # noqa: E402


# ─── two-call preambles ─────────────────────────────────────────────────
# Prepended to the fixture prompt in two-call mode. The fixture already
# contains V3's night/orbit doctrine (which tells the agent to call the
# tool) — we override that with a role-specific header. The Cortex
# agent's OWN spec (SOC_RED_REAPER_STRATEGIC / _TACTICAL) also has a
# response section that reinforces the role, but the preamble ensures
# the LLM prefers the role instructions over V3's tool-call doctrine.

_STRATEGIST_PREAMBLE = (
    "ROLE=STRATEGIST. You have NO tools. YOU HAVE 15 SECONDS.\n\n"
    "You will be CUT OFF at 15 seconds — the network socket closes and "
    "whatever you've written is what the tactician sees. So be terse.\n\n"
    "Give your GUT call. Skim for score gap, posture, top 2-3 candidates. "
    "Then WRITE the 4 lines. Any analysis you add after eats into your "
    "own decision time.\n\n"
    "OUTPUT — start with these 4 lines. Any prose before them is wasted:\n\n"
    "  DIAGNOSIS: <one sentence — day X/7, my score vs rival, main tension>\n"
    "  PLAN: <exactly one of: harvest_pure, harvest_mixed, denial_dominant, "
    "emp_race, vault_flush_orbit, probe_seed, defensive_repair, final_day_push>\n"
    "  TOP: <1-3 candidate IDs like H0, C0, P1, S1 — or coordinates>\n"
    "  RATIONALE: <one sentence — your gut>\n\n"
    "═══════════════════════════════════════════════════════════\n"
    "STATE (skim, don't dwell):\n\n"
)

_STRATEGIST_POSTAMBLE = (
    "\n═══════════════════════════════════════════════════════════\n"
    "END OF STATE. 15s clock ticking. FIRST TOKEN you emit must be "
    "'DIAGNOSIS:'. Any bold-headers or 'Let me parse' phrasing wastes "
    "your decision budget. Go:\n"
)

_TACTICIAN_PREAMBLE_TMPL = (
    "Return ONE JSON object only. No prose. No markdown. No tools.\n"
    "Copy DECISION STATE.meta.player into seat and DECISION STATE.meta.phase into phase.\n"
    "Allowed choices: recommended_policy, recommended_orbit_policy, or a candidate id shown below.\n"
    "If unsure choose the recommended choice.\n"
    "Strategist note:\n{strategist_note}\n"
)

_TACTICIAN_POSTAMBLE = (
    "\n═══════════════════════════════════════════════════════════\n"
    "END STATE. Output ONE JSON object now. First byte MUST be '{'. "
    "No analysis, no bullets, no markdown, no tools.\n"
)


# Regex for parsing the strategist's 4-line output. Broadened to catch
# common variants the LLM emits when it deviates from spec:
#   - "PLAN: probe_seed"          (spec-compliant)
#   - "PLAN LABEL: probe_seed"
#   - "**PLAN:** probe_seed"
#   - "PLAN LABEL: This matches **probe_seed**" (worst case)
_PLAN_RE = re.compile(
    r"\bPLAN(?:\s+LABEL)?\s*:?\s*\**\s*(?:This matches\s*)?\**\s*"
    r"(harvest_pure|harvest_mixed|denial_dominant|emp_race|"
    r"vault_flush_orbit|probe_seed|defensive_repair|final_day_push)",
    re.I,
)
_TOP_RE = re.compile(r"^\s*\**\s*TOP(?:\s+CANDIDATES?)?\s*:?\s*\**\s*(.+)$", re.M | re.I)


def _parse_strategist_note(text: str) -> Dict[str, str]:
    """Extract PLAN + TOP from the strategist's output.

    Returns dict with keys 'plan', 'top', 'raw'. Missing fields fall
    back to empty strings so the tactician still has a valid preamble.
    Uses a broad regex so we tolerate the strategist wrapping its label
    in bold or writing "PLAN LABEL: ..." instead of the spec's "PLAN:".
    """
    plan_m = _PLAN_RE.search(text or "")
    top_m = _TOP_RE.search(text or "")
    return {
        "plan": plan_m.group(1).lower() if plan_m else "",
        "top": top_m.group(1).strip() if top_m else "",
        "raw": (text or "").strip(),
    }


def _compact_strategist_note(text: str) -> str:
    note = _parse_strategist_note(text)
    plan = note["plan"] or "harvest_mixed"
    top = note["top"] or "recommended_policy"
    return (
        "DIAGNOSIS: strategist compressed by harness.\n"
        f"PLAN: {plan}\n"
        f"TOP: {top}\n"
        "RATIONALE: choose the best validated candidate for this plan."
    )


# ─── clean-agent scorecard regexes ──────────────────────────────────────
# Ramble markers: reconsideration language that costs wallclock. Every
# hit is a paragraph of second-guessing.
RAMBLE_RE = re.compile(
    r"\b(let me reconsider|let me re-?read|let me check|let me parse|"
    r"let me verify|let me look|let me think|let me consider|"
    r"actually|wait|hmm|on second thought|"
    r"reconsider|re-read|double-check|one more thing)\b",
    re.I,
)
# Preamble bloat: bold section headers that pad output before decision.
# Old V3 style plus the newer '**FOO:**' pattern the agent gravitates to.
PREAMBLE_RE = re.compile(
    r"(STATE SUMMARY|SITUATION SUMMARY|CANDIDATE ANALYSIS|"
    r"FINAL DAY RULES|STATE DIGEST|THREAT ASSESSMENT|"
    r"MY ASSETS|VAULT STATE|WORLD LIVE|POSTURE|COMBAT STATE|"
    r"HARVEST CHAINS|PROBE (?:CANDIDATES|BUDGET)|COMPOSITION|"
    r"\*\*[A-Z][A-Z0-9 _/-]{2,30}:\*\*)"
)
# Candidate ID mentions — first vs last tells us if the agent committed
# or switched. Matches H0, H0_harvester_p1, C0, S1, P2, EMP0, drop@(x,y).
# Broadened to catch the underscore-suffixed forms and coordinates.
CANDIDATE_RE = re.compile(
    r"(EMP\d+|H\d+(?:_[a-z_0-9]+)?|C\d+|S\d+|P\d+"
    r"|drop\s*@?\s*\(\s*\d+\s*,\s*\d+\s*\)"
    r"|\bat\s*\(\s*\d+\s*,\s*\d+\s*\))"
)


def score_run(result: Dict[str, Any]) -> Dict[str, Any]:
    """Turn an invoker result dict into a scorecard row."""
    text = result.get("response") or ""
    ms = int(result.get("elapsed_ms") or 0)
    cands = CANDIDATE_RE.findall(text)
    first = cands[0] if cands else ""
    final = cands[-1] if cands else ""
    return {
        "ok": bool(result.get("ok")),
        "ms": ms,
        "resp_chars": len(text),
        "ramble_count": len(RAMBLE_RE.findall(text)),
        "preamble_count": len(PREAMBLE_RE.findall(text)),
        "first_cand": first,
        "final_cand": final,
        "stable_choice": bool(first and final and first == final),
        "tool_called": bool(result.get("submitted_policy")) or bool(result.get("tool_calls")),
        "wallclock_capped": bool(result.get("wallclock_capped")),
    }


def _extract_state(prompt: str) -> Dict[str, Any]:
    marker = "STATE (JSON):\n```json\n"
    i = prompt.find(marker)
    if i < 0:
        marker = "DECISION STATE (JSON):\n```json\n"
        i = prompt.find(marker)
    if i < 0:
        return {}
    j = prompt.find("\n```", i + len(marker))
    if j < 0:
        return {}
    try:
        parsed = json.loads(prompt[i + len(marker): j])
    except Exception:
        header = prompt[:i]
        seat_m = re.search(r"\bseat=([a-z0-9_]+)", header, flags=re.I)
        session_m = re.search(r"\bsession=([a-f0-9_\-]+)", header, flags=re.I)
        day_m = re.search(r"\bday=(\d+)", header, flags=re.I)
        phase_m = re.search(r'"phase"\s*:\s*"([^"]+)"', prompt)
        return {
            "meta": {
                "session_id": session_m.group(1) if session_m else None,
                "player": seat_m.group(1) if seat_m else None,
                "day": int(day_m.group(1)) if day_m else None,
                "phase": phase_m.group(1) if phase_m else "planning",
            }
        }
    return parsed if isinstance(parsed, dict) else {}


def _candidate_ids(state: Dict[str, Any]) -> set[str]:
    out = {"recommended_policy", "recommended_orbit_policy"}
    candidates = state.get("candidates") or {}
    for key in (
        "harvest", "probes", "probe_supersede", "harvester_crush",
        "hot_drop", "drop_block", "emp_launch",
    ):
        for cand in candidates.get(key) or []:
            cid = str((cand or {}).get("id") or "")
            if cid:
                out.add(cid)
                out.add(cid.split("_", 1)[0])
    return out


def _compact_decision_state(prompt: str) -> str:
    state = _extract_state(prompt)
    meta = state.get("meta") or {}
    phase = str(meta.get("phase") or "planning").lower()
    compact: Dict[str, Any] = {
        "meta": {
            "session_id": meta.get("session_id"),
            "player": meta.get("player"),
            "phase": phase,
            "day": meta.get("day"),
        }
    }
    if phase == "orbit":
        orbital = state.get("orbital") or {}
        compact["orbital"] = {
            "recommended_orbit_policy": orbital.get("recommended_orbit_policy") or {},
        }
    else:
        candidates = state.get("candidates") or {}
        compact_candidates: Dict[str, Any] = {
            "recommended_policy": candidates.get("recommended_policy") or {},
        }
        for key in (
            "harvest", "probes", "probe_supersede", "harvester_crush",
            "hot_drop", "drop_block", "emp_launch",
        ):
            compact_candidates[key] = [
                {
                    "id": (c or {}).get("id"),
                    "kind": (c or {}).get("kind") or (c or {}).get("type"),
                    "score": (c or {}).get("score", (c or {}).get("expected_score_after_vault_cascade")),
                }
                for c in (candidates.get(key) or [])[:6]
            ]
        compact["candidates"] = compact_candidates
    return "DECISION STATE (JSON):\n```json\n" + json.dumps(compact, separators=(",", ":")) + "\n```\n"


def _score_decision_json(result: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    text = (result.get("response") or "").strip()
    state = _extract_state(prompt)
    meta = state.get("meta") or {}
    expected_seat = str(meta.get("player") or "")
    expected_phase = str(meta.get("phase") or "planning").lower() or "planning"
    parsed: Dict[str, Any] = {}
    parseable = False
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            maybe, _ = decoder.raw_decode(text[match.start():])
            if isinstance(maybe, dict) and {"seat", "phase", "choice"}.issubset(maybe):
                parsed = maybe
                parseable = True
                break
        except Exception:
            continue
    choice = str(parsed.get("choice") or "")
    return {
        "json_parseable": parseable,
        "valid_seat": bool(parseable and str(parsed.get("seat") or "") == expected_seat),
        "valid_phase": bool(parseable and str(parsed.get("phase") or "").lower() == expected_phase),
        "valid_choice": bool(parseable and choice in _candidate_ids(state)),
        "decision_choice": choice,
        "model_tool_called": bool(result.get("tool_calls")),
    }


def _fmt_scorecard(label: str, rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return f"{label}: no runs"

    def _med(k: str) -> float:
        return statistics.median(r[k] for r in rows)

    def _p95(k: str) -> int:
        vals = sorted(r[k] for r in rows)
        idx = max(0, min(len(vals) - 1, int(round(0.95 * (len(vals) - 1)))))
        return vals[idx]

    stable_pct = sum(1 for r in rows if r["stable_choice"]) / len(rows)
    tool_pct = sum(1 for r in rows if r["tool_called"]) / len(rows)
    json_pct = sum(1 for r in rows if r.get("json_parseable")) / len(rows)
    valid_seat_pct = sum(1 for r in rows if r.get("valid_seat")) / len(rows)
    valid_phase_pct = sum(1 for r in rows if r.get("valid_phase")) / len(rows)
    valid_choice_pct = sum(1 for r in rows if r.get("valid_choice")) / len(rows)
    model_tool_pct = sum(1 for r in rows if r.get("model_tool_called")) / len(rows)
    cap_hits = sum(1 for r in rows if r["wallclock_capped"])
    ok_pct = sum(1 for r in rows if r["ok"]) / len(rows)

    return (
        f"\n=== {label}  (N={len(rows)}) ===\n"
        f"  ok:                {ok_pct:6.0%}\n"
        f"  ms:                med={_med('ms'):>7.0f}   p95={_p95('ms'):>7d}\n"
        f"  resp_chars:        med={_med('resp_chars'):>7.0f}   p95={_p95('resp_chars'):>7d}\n"
        f"  ramble_count:      med={_med('ramble_count'):>7.1f}   p95={_p95('ramble_count'):>7d}\n"
        f"  preamble_count:    med={_med('preamble_count'):>7.1f}   p95={_p95('preamble_count'):>7d}\n"
        f"  stable_choice:     {stable_pct:6.0%}\n"
        f"  tool_called:       {tool_pct:6.0%}\n"
        f"  json_parseable:    {json_pct:6.0%}\n"
        f"  valid_seat:        {valid_seat_pct:6.0%}\n"
        f"  valid_phase:       {valid_phase_pct:6.0%}\n"
        f"  valid_choice:      {valid_choice_pct:6.0%}\n"
        f"  model_tool_called: {model_tool_pct:6.0%}\n"
        f"  wallclock_capped:  {cap_hits} / {len(rows)}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Loop-test a Cortex agent against a frozen prompt.")
    ap.add_argument("--fixture", required=True, help="Path to prompt .txt fixture.")
    ap.add_argument("--mode", choices=("single", "two-call"), default="single",
                    help="single = one .invoke() call (default). "
                    "two-call = STRATEGIST invocation then TACTICIAN invocation.")
    ap.add_argument("--agent", default="SOC_RED_REAPER_SCRATCH",
                    help="Cortex agent name for single-mode (default: SOC_RED_REAPER_SCRATCH).")
    ap.add_argument("--strategic-agent", default="SOC_RED_REAPER_STRATEGIC",
                    help="Cortex agent name for the strategist in two-call mode.")
    ap.add_argument("--tactical-agent", default="SOC_RED_REAPER_TACTICAL",
                    help="Cortex agent name for the tactician in two-call mode.")
    ap.add_argument("--strategic-cap", type=int, default=2000,
                    help="Response byte cap for the strategist call (default: 2000).")
    ap.add_argument("--tactical-cap", type=int, default=3000,
                    help="Response byte cap for the tactician call (default: 3000).")
    ap.add_argument("--repeats", type=int, default=5, help="Number of invocations (default: 5).")
    ap.add_argument("--out", default="scripts/fixtures/results.csv",
                    help="CSV path to append per-run results (default: scripts/fixtures/results.csv).")
    ap.add_argument("--label", default="",
                    help="Optional label to tag this batch of runs in the CSV.")
    ap.add_argument("--wallclock-cap", type=int, default=None,
                    help="Override the invoker's wallclock cap in seconds (single mode).")
    ap.add_argument("--strategic-wallclock", type=int, default=15,
                    help="Wallclock cap for the strategist call in two-call mode (default: 15s).")
    ap.add_argument("--tactical-wallclock", type=int, default=25,
                    help="Wallclock cap for the tactician call in two-call mode (default: 25s).")
    ap.add_argument("--response-cap", type=int, default=None,
                    help="Override the invoker's response byte cap (single mode only).")
    args = ap.parse_args()

    fixture_path = Path(args.fixture).resolve()
    if not fixture_path.exists():
        print(f"ERROR: fixture not found: {fixture_path}", file=sys.stderr)
        return 2
    prompt = fixture_path.read_text()
    if len(prompt.strip()) < 100:
        print(f"ERROR: fixture too short ({len(prompt)} chars) — likely empty.", file=sys.stderr)
        return 2

    if args.mode == "single":
        inv = CortexAgentInvoker(agent_name=args.agent)
        if not inv.is_ready():
            print("ERROR: CortexAgentInvoker not ready — check SNOWFLAKE_PAT / ~/.ssh/sf_config.",
                  file=sys.stderr)
            return 2
        print(f"→ mode      : single")
        print(f"→ fixture   : {fixture_path.name} ({len(prompt)} chars)")
        print(f"→ agent     : {args.agent}")
        print(f"→ repeats   : {args.repeats}")
        print(f"→ label     : {args.label or '(none)'}")
        print()

        rows: List[Dict[str, Any]] = []
        for i in range(args.repeats):
            started = time.time()
            kw: Dict[str, Any] = {}
            if args.wallclock_cap is not None:
                kw["wallclock_cap_s"] = args.wallclock_cap
            if args.response_cap is not None:
                kw["response_cap_bytes"] = args.response_cap
            result = inv.invoke(prompt, **kw)
            row = score_run(result)
            row.update(_score_decision_json(result, prompt))
            row["run_idx"] = i
            row["label"] = args.label
            row["fixture"] = fixture_path.name
            row["agent"] = args.agent
            row["plan_chosen"] = ""
            rows.append(row)
            wall = time.time() - started
            marker = "!" if row["wallclock_capped"] else " " if row["ok"] else "✗"
            print(
                f"  run {i+1}/{args.repeats}{marker} "
                f"ms={row['ms']:>6}  chars={row['resp_chars']:>6}  "
                f"rambles={row['ramble_count']:>2}  "
                f"pream={row['preamble_count']:>2}  "
                f"first={row['first_cand'] or '-':<5} final={row['final_cand'] or '-':<5}  "
                f"stable={'Y' if row['stable_choice'] else '-'}  "
                f"tool={'Y' if row['tool_called'] else '-'}  "
                f"json={'Y' if row.get('json_parseable') else '-'}  "
                f"[wall {wall:.1f}s]"
            )
    else:
        # two-call mode: STRATEGIST → TACTICIAN with combined scoring.
        strat_inv = CortexAgentInvoker(agent_name=args.strategic_agent)
        tact_inv = CortexAgentInvoker(agent_name=args.tactical_agent)
        if not strat_inv.is_ready() or not tact_inv.is_ready():
            print("ERROR: CortexAgentInvoker not ready — check SNOWFLAKE_PAT / ~/.ssh/sf_config.",
                  file=sys.stderr)
            return 2
        print(f"→ mode      : two-call")
        print(f"→ fixture   : {fixture_path.name} ({len(prompt)} chars)")
        print(f"→ strategic : {args.strategic_agent}  (cap {args.strategic_cap}B, {args.strategic_wallclock}s)")
        print(f"→ tactical  : {args.tactical_agent}   (cap {args.tactical_cap}B, {args.tactical_wallclock}s)")
        print(f"→ repeats   : {args.repeats}")
        print(f"→ label     : {args.label or '(none)'}")
        print()

        strategic_prompt = _STRATEGIST_PREAMBLE + prompt + _STRATEGIST_POSTAMBLE
        rows = []
        for i in range(args.repeats):
            started = time.time()
            kw: Dict[str, Any] = {
                "response_cap_bytes": args.strategic_cap,
                "wallclock_cap_s": args.strategic_wallclock,
            }
            s_result = strat_inv.invoke(strategic_prompt, **kw)
            s_row = score_run(s_result)
            note = _parse_strategist_note(s_result.get("response") or "")

            tactical_prompt = _TACTICIAN_PREAMBLE_TMPL.format(
                strategist_note=_compact_strategist_note(s_result.get("response") or "")
            ) + _compact_decision_state(prompt) + _TACTICIAN_POSTAMBLE
            kw2: Dict[str, Any] = {
                "response_cap_bytes": args.tactical_cap,
                "wallclock_cap_s": args.tactical_wallclock,
                "tool_choice": {"type": "auto"},
            }
            t_result = tact_inv.invoke(tactical_prompt, **kw2)
            t_row = score_run(t_result)
            decision_row = _score_decision_json(t_result, tactical_prompt)

            row = {
                "ok": s_row["ok"] and t_row["ok"],
                "ms": s_row["ms"] + t_row["ms"],
                "resp_chars": s_row["resp_chars"] + t_row["resp_chars"],
                "ramble_count": s_row["ramble_count"] + t_row["ramble_count"],
                "preamble_count": s_row["preamble_count"] + t_row["preamble_count"],
                "first_cand": s_row["first_cand"],
                "final_cand": t_row["final_cand"] or t_row["first_cand"],
                "stable_choice": False,   # not meaningful across two roles
                "tool_called": t_row["tool_called"],   # only the tactician calls tools
                "wallclock_capped": s_row["wallclock_capped"] or t_row["wallclock_capped"],
                "run_idx": i,
                "label": args.label,
                "fixture": fixture_path.name,
                "agent": f"{args.strategic_agent}+{args.tactical_agent}",
                "plan_chosen": note["plan"],
                **decision_row,
            }
            rows.append(row)
            wall = time.time() - started
            marker = "!" if row["wallclock_capped"] else " " if row["ok"] else "✗"
            plan = note["plan"] or "(none)"
            print(
                f"  run {i+1}/{args.repeats}{marker} "
                f"ms={row['ms']:>6} (s={s_row['ms']}+t={t_row['ms']})  "
                f"chars={row['resp_chars']:>6}  "
                f"rambles={row['ramble_count']:>2} (s={s_row['ramble_count']}+t={t_row['ramble_count']})  "
                f"plan={plan:<18}  "
                f"json={'Y' if row.get('json_parseable') else '-'}  "
                f"seat={'Y' if row.get('valid_seat') else '-'}  "
                f"choice={row.get('decision_choice') or '-'}  "
                f"[wall {wall:.1f}s]"
            )

    print(_fmt_scorecard(f"{args.mode} × {fixture_path.name}", rows))

    # Append to shared CSV (create with header if missing).
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out_path.exists() or out_path.stat().st_size == 0
    columns = [
        "label", "agent", "fixture", "run_idx", "ok",
        "ms", "resp_chars", "ramble_count", "preamble_count",
        "first_cand", "final_cand", "stable_choice", "tool_called",
        "wallclock_capped", "plan_chosen", "json_parseable",
        "valid_seat", "valid_phase", "valid_choice", "decision_choice",
        "model_tool_called",
    ]
    with out_path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        if write_header:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in columns})
    print(f"→ appended {len(rows)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
