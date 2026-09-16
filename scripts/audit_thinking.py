#!/usr/bin/env python3
"""Audit an agent's PERSISTED reasoning for a finished session.

Pulls every ``SOC_AGENT_INVOCATION`` row for a session and, per night for one
seat, lays the THINKER's chain-of-thought + reflection next to the GROUND-TRUTH
"WHAT HAPPENED LAST NIGHT" block the agent was actually shown (sliced out of the
captured ``prompt_excerpt``). That pairing is the whole point: it lets a human
(or model) read whether the agent REASONED over real state or HALLUCINATED —
did its ``reflection_on_last_night`` match the engine record, did its plan cite
cells/patterns that exist, did it invent events that never happened.

No new season is run — this reads what already happened. Requires the Snowflake
backend + PAT (that is where the audit rows live).

Usage::

    PYTHONPATH=. python scripts/audit_thinking.py \\
        --session 91cfd144adff4e07adf10a5f3759adb5 --player p1 \\
        --out reports/audit_v9_1v1_s42.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SECTION_3 = "=== SECTION 3 - WHAT HAPPENED LAST NIGHT"
_SCHEMA_MARK = "=== v9 AGENCY FIELDS"
_JSON_HINTS = ("OUTPUT", "Return ONLY", "JSON", "schema")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="audit_thinking")
    p.add_argument("--session", required=True, help="session_id to audit.")
    p.add_argument("--player", default="p1", help="Seat to audit (default p1).")
    p.add_argument("--thinker-match", default="THINKER",
                   help="Substring identifying the thinker agent_id rows.")
    p.add_argument("--out", default=None, help="Markdown report path.")
    return p


def _last_night_block(prompt_excerpt: str) -> str:
    """Slice SECTION 3 (ground truth) out of the captured thinker prompt."""
    if not prompt_excerpt:
        return ""
    i = prompt_excerpt.find(_SECTION_3)
    if i < 0:
        return ""
    tail = prompt_excerpt[i:]
    # Stop at the agency/schema addendum if present (keeps just the recap).
    j = tail.find(_SCHEMA_MARK)
    if j > 0:
        tail = tail[:j]
    return tail.strip()


def _parse_decision(response_text: str) -> Dict[str, Any]:
    """Best-effort parse of the thinker's decision from the raw response.

    The v9 thinker emits REASONING-FIRST free text ("CHAIN OF THOUGHT: ...")
    rather than a single JSON object, so JSON parsing usually fails — that is
    expected, not an error. We fall back to treating the whole response as the
    chain-of-thought and slicing out a reflection sub-section if the model
    labelled one.
    """
    if not response_text:
        return {}
    s = response_text.strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    a, b = s.find("{"), s.rfind("}")
    if 0 <= a < b:
        try:
            obj = json.loads(s[a:b + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    # Free-text CoT path: the whole thing IS the reasoning.
    out: Dict[str, Any] = {"reasoning": s}
    m = re.search(
        r"(?is)(?:last[ _-]?night\s+reflection|reflection\s+on\s+last\s+night)"
        r"[:*\s]+(.+?)(?:\n\s*\n|\*\*)",
        s,
    )
    if m:
        out["reflection_on_last_night"] = m.group(1).strip()
    return out


def _parse_rationale(rationale: str) -> Dict[str, Any]:
    """Pull posture/plan/targets from the harness rationale summary line."""
    out: Dict[str, Any] = {}
    if not rationale:
        return out
    mp = re.search(r"posture=(\w+)", rationale, re.IGNORECASE)
    if mp:
        out["posture"] = mp.group(1)
    mplan = re.search(r"plan=(\[[^\]]*\])", rationale, re.IGNORECASE)
    if mplan:
        out["plan"] = mplan.group(1)
    mt = re.search(r"targets=(\[[^\]]*\])", rationale, re.IGNORECASE)
    if mt:
        out["targets"] = mt.group(1)
    return out


def _first(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _fmt_val(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    os.environ.setdefault("SOC_BACKEND", "snowflake")
    if os.environ["SOC_BACKEND"].lower() != "snowflake":
        print("preflight: audit rows live in snowflake; set SOC_BACKEND=snowflake",
              file=sys.stderr)
        return 2

    from sea_of_colours.snowpark import backend as soc_backend
    store = soc_backend.get_store()
    rows = store.list_agent_invocations(args.session)
    if not rows:
        print(f"no invocation rows for session {args.session}", file=sys.stderr)
        return 1

    seat = args.player
    tmatch = args.thinker_match.upper()

    # Group rows by day for the audited seat.
    by_day: Dict[int, List[Dict[str, Any]]] = {}
    agent_ids = set()
    for r in rows:
        if str(r.get("player")) != seat:
            continue
        agent_ids.add(str(r.get("agent_id")))
        by_day.setdefault(int(r.get("day") or 0), []).append(r)

    lines: List[str] = []
    lines.append(f"# Reasoning audit — session `{args.session}` · seat {seat}\n")
    lines.append(f"agent_ids seen for {seat}: {sorted(agent_ids)}\n")

    flags: List[str] = []
    for day in sorted(by_day):
        drows = by_day[day]
        thinker = next(
            (r for r in drows if tmatch in str(r.get("agent_id", "")).upper()), None
        )
        primary = next(
            (r for r in drows if tmatch not in str(r.get("agent_id", "")).upper()),
            None,
        )
        lines.append(f"\n## day {day}")
        if thinker is None:
            lines.append("_(no thinker row — turn fell back or was an orbit turn)_")
            # Still show the primary rationale if any.
            if primary is not None:
                lines.append("\n**primary/mover rationale:**\n")
                lines.append("> " + str(primary.get("rationale") or "")[:600]
                             .replace("\n", "\n> "))
            continue

        decision = _parse_decision(str(thinker.get("response_text") or ""))
        rat = _parse_rationale(str(thinker.get("rationale") or ""))
        reasoning = _first(decision, "reasoning", "chain_of_thought") or ""
        reflection = _first(
            decision, "reflection_on_last_night", "reflection"
        ) or ""
        posture = _first(decision, "posture") or rat.get("posture") or ""
        plan = _first(decision, "plan") or rat.get("plan") or []
        situational = _first(decision, "situational") or {}
        plan_label = _first(decision, "plan_this_turn", "plan_label") or ""

        ln_block = _last_night_block(str(thinker.get("prompt_excerpt") or ""))

        lines.append(f"- thinker_ms: {thinker.get('ms_elapsed')}  "
                     f"posture: `{posture}`  plan: {_fmt_val(plan)}")
        if situational:
            lines.append(f"- situational (agent's read): {_fmt_val(situational)}")
        if plan_label:
            lines.append(f"- plan_this_turn: {plan_label}")

        lines.append("\n**GROUND TRUTH shown to agent (SECTION 3 — last night):**\n")
        lines.append("```\n" + (ln_block or "(section not captured)") + "\n```")

        lines.append("\n**AGENT reflection_on_last_night (its claim):**\n")
        lines.append("> " + (str(reflection).strip() or "(empty)")
                     .replace("\n", "\n> "))

        lines.append("\n**AGENT reasoning (chain-of-thought):**\n")
        lines.append("> " + (str(reasoning).strip() or "(empty)")
                     .replace("\n", "\n> "))

        # Cheap automatic hallucination heuristics (advisory only).
        if not reasoning.strip():
            flags.append(f"day {day}: EMPTY reasoning captured")
        if ln_block and "collision" in ln_block.lower():
            claim = (str(reflection) + " " + str(reasoning)).lower()
            if "collision" not in claim and "collid" not in claim and "crash" not in claim:
                flags.append(
                    f"day {day}: ground truth mentions a COLLISION but the "
                    f"agent's reflection/reasoning never acknowledges it")
        # Plan IDs that look invented (not present anywhere in the prompt menu).
        prompt_full = str(thinker.get("prompt_excerpt") or "").upper()
        if isinstance(plan, list):
            plan_ids = [str(p).strip() for p in plan]
        else:
            plan_ids = re.findall(r"[A-Z0-9_#]{2,}", str(plan).upper())
        for pid_s in plan_ids:
            if pid_s and pid_s.upper() not in prompt_full:
                flags.append(
                    f"day {day}: plan id {pid_s!r} not found in the option "
                    f"menu shown to the agent (possible invention)")

    if flags:
        lines.append("\n\n## AUTOMATIC FLAGS (advisory — verify by reading)\n")
        for f in flags:
            lines.append(f"- {f}")
    else:
        lines.append("\n\n## AUTOMATIC FLAGS\n\n_None tripped._")

    report = "\n".join(lines) + "\n"
    out = args.out or str(
        _REPO_ROOT / "reports" / f"audit_thinking_{args.session[:8]}_{seat}.md"
    )
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(report, encoding="utf-8")
    print(f"report written: {out}")
    print(f"days audited: {sorted(by_day)}  flags: {len(flags)}")
    for f in flags:
        print("  FLAG:", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
