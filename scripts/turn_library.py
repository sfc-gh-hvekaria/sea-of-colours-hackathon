#!/usr/bin/env python3
"""Build a replayable regression library of interesting turns.

A "turn" here is one captured planning card. Because the card carries the whole
reconstructed memory for that moment (journal, last night, world view, menu) and
the chat invoker is stateless, a turn can be re-asked forever without an engine.
That makes a fixed set of cards into a regression suite for agent JUDGEMENT.

For each turn in ``reports/turn_library/library.json`` this writes a dossier to
``reports/turn_library/<LABEL>.md`` containing the full card text followed by:

  AGENT POLICY      the options the agent asked for, with the geometry and yield
                    the menu ADVERTISED for each
  PACKAGER POLICY   the move queue the packager actually emitted, hour by hour
  DELTA             per pick: advertised vs executed, plus picks the packager
                    silently dropped and moves it injected on its own
  OUTCOME           engine truth for that night (parsed from the NEXT day's card)
                    — banked red, rejections, inventory used vs held
  REPLAY            N fresh samples of the same card: what does the agent pick
                    when asked again?

and a cross-turn ``SUMMARY.md``.

Usage
-----
    python scripts/turn_library.py            # build dossiers + replay n=6
    python scripts/turn_library.py -n 12
    python scripts/turn_library.py --no-replay        # parse-only, no API calls
    python scripts/turn_library.py --only VERIFY_r2_d5_blind_grab
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.replay_card import load_card, run_sample  # noqa: E402

_LIB_DIR = _ROOT / "reports" / "turn_library"
_LIB_FILE = _LIB_DIR / "library.json"

Cell = Tuple[int, int]


# ──────────────────────────────────────────────────────────────────────────
# parsing the menu
# ──────────────────────────────────────────────────────────────────────────
_ROW = re.compile(r"^\s*\[([A-Z][A-Z0-9_]*)\]\s*(.*)$")
_CELL = re.compile(r"\((\d+)\s*,\s*(\d+)\)")


def _cells(text: str) -> List[Cell]:
    return [(int(a), int(b)) for a, b in _CELL.findall(text)]


def parse_menu(menu_block: str) -> Dict[str, Dict[str, Any]]:
    """``option_id -> {title, walk, drop, n_cells, yield_line, red, probe}``."""
    out: Dict[str, Dict[str, Any]] = {}
    current: Optional[str] = None
    for line in menu_block.splitlines():
        m = _ROW.match(line)
        if m:
            current = m.group(1)
            title = m.group(2).strip()
            out[current] = {
                "title": title,
                "walk": [],
                "drop": None,
                "n_cells": 0,
                "yield_line": "",
                "red": 0.0,
                "probe": 0 if "no probe" in title else 1,
                "detail": [],
            }
            # Probe-ish options carry their target in the title, not a walk line.
            head = _cells(title)
            if head:
                out[current]["drop"] = head[0]
            continue
        if current is None:
            continue
        s = line.strip()
        if not s or s.startswith("[") or not line.startswith(" "):
            if s and not s.startswith(("walk:", "yield:", "crush:")):
                current = None
            continue
        rec = out[current]
        rec["detail"].append(s)
        if s.startswith("walk:"):
            w = _cells(s)
            rec["walk"] = w
            rec["n_cells"] = len(w)
            if w:
                rec["drop"] = w[0]
        elif s.startswith("yield:"):
            rec["yield_line"] = s
            m2 = re.search(r"red\s*~?\+?(-?\d+)", s)
            if m2:
                rec["red"] = float(m2.group(1))
    return out


# ──────────────────────────────────────────────────────────────────────────
# parsing what the packager emitted
# ──────────────────────────────────────────────────────────────────────────
def _mv_cell(mv: Mapping[str, Any]) -> Optional[Cell]:
    for k in ("at", "to", "cell"):
        v = mv.get(k)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            return (int(v[0]), int(v[1]))
    return None


def parse_runs(moves: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Group the flat move queue into harvester runs and standalone probes."""
    runs: List[Dict[str, Any]] = []
    probes: List[Dict[str, Any]] = []
    open_run: Dict[str, Dict[str, Any]] = {}
    for i, mv in enumerate(moves, 1):
        act = str(mv.get("a") or "")
        unit = str(mv.get("unit") or "")
        cell = _mv_cell(mv)
        if act == "probe":
            probes.append({"hour": i, "at": cell})
        elif act == "drop":
            run = {"unit": unit, "drop": cell, "steps": [], "hour": i,
                   "pickup_hour": None}
            open_run[unit] = run
            runs.append(run)
        elif act == "step":
            r = open_run.get(unit)
            if r is not None and cell is not None:
                r["steps"].append(cell)
        elif act == "pickup":
            r = open_run.get(unit)
            if r is not None:
                r["pickup_hour"] = i
    return {"runs": runs, "probes": probes}


def build_delta(
    picks: Sequence[str], menu: Mapping[str, Mapping[str, Any]],
    emitted: Mapping[str, Any],
) -> Dict[str, Any]:
    """Match each pick to what the packager actually emitted for it."""
    runs = list(emitted["runs"])
    probes = list(emitted["probes"])
    used_run: set = set()
    used_probe: set = set()
    rows: List[Dict[str, Any]] = []

    for oid in picks:
        adv = menu.get(oid)
        if adv is None:
            rows.append({"id": oid, "status": "NOT ON MENU"})
            continue
        drop = adv.get("drop")
        row: Dict[str, Any] = {
            "id": oid,
            "adv_drop": drop,
            "adv_cells": adv.get("n_cells") or (1 if drop else 0),
            "adv_red": adv.get("red") or 0.0,
        }
        hit = None
        # A harvester option is honoured only if a run LANDS on its drop cell.
        if adv.get("n_cells"):
            for i, r in enumerate(runs):
                if i not in used_run and r["drop"] == drop:
                    hit, used_run = r, used_run | {i}
                    break
            if hit is None:
                row.update(status="DROPPED BY PACKAGER", exec_cells=0)
            else:
                n = 1 + len(hit["steps"])
                row.update(
                    status="OK" if n >= row["adv_cells"] else "TRUNCATED",
                    exec_cells=n, exec_drop=hit["drop"],
                    exec_walk=hit["steps"], hour=hit["hour"],
                )
        else:
            for i, p in enumerate(probes):
                if i not in used_probe and p["at"] == drop:
                    hit, used_probe = p, used_probe | {i}
                    break
            row.update(
                status="OK" if hit else "DROPPED BY PACKAGER",
                exec_cells=1 if hit else 0,
                hour=hit["hour"] if hit else None,
            )
        rows.append(row)

    injected = [
        {"kind": "run", "drop": r["drop"], "steps": len(r["steps"]), "hour": r["hour"]}
        for i, r in enumerate(runs) if i not in used_run
    ] + [
        {"kind": "probe", "at": p["at"], "hour": p["hour"]}
        for i, p in enumerate(probes) if i not in used_probe
    ]
    return {"rows": rows, "injected": sorted(injected, key=lambda d: d["hour"])}


# ──────────────────────────────────────────────────────────────────────────
# engine truth, read off the NEXT day's card
# ──────────────────────────────────────────────────────────────────────────
def find_next_card(root: Path, session: str, day: int) -> Optional[Path]:
    hits = sorted(root.glob(f"{session}*/turn_*day{day + 1}_*planning.json"))
    return hits[0] if hits else None


def parse_outcome(next_card: Optional[Path]) -> Dict[str, Any]:
    if next_card is None:
        return {"available": False,
                "note": "no following card — season ended, engine truth not captured"}
    p = json.loads(next_card.read_text(encoding="utf-8"))
    text = (p.get("extras") or {}).get("thinker_prompt") or ""
    out: Dict[str, Any] = {"available": True}
    m = re.search(
        r"YIELD \(actual vs expected\):\s*\n\s*(.+?)\n", text,
    )
    if m:
        line = m.group(1).strip()
        out["yield_line"] = line
        mr = re.search(r"red\s*\+?(-?\d+)", line)
        me = re.search(r"exp\s*~?\+?(-?\d+)", line)
        mp = re.search(r"\((\d+) parcel", line)
        out["red_actual"] = int(mr.group(1)) if mr else None
        out["red_expected"] = int(me.group(1)) if me else None
        out["parcels"] = int(mp.group(1)) if mp else None
        out["blue"] = 255 if "blue +255" in line else (
            0 if "blue 0" in line or "blue +0" in line else None
        )
    blk = re.search(
        r"EXECUTION LOG \(per hour[^\n]*\n(.*?)\n(?:YIELD|WHAT YOU SAW)", text,
        re.S,
    )
    if blk:
        log = [l.rstrip() for l in blk.group(1).splitlines() if l.strip()]
        out["execution_log"] = log
        out["rejected"] = [l.strip() for l in log if " ok" not in l]
    return out


def parse_inventory(prompt: str, emitted: Mapping[str, Any]) -> Dict[str, Any]:
    m = re.search(r"you have (\d+) harvester\(s\) alive", prompt)
    harv = int(m.group(1)) if m else None
    m = re.search(r"PROBE BUDGET: you have (\d+)", prompt)
    stock = int(m.group(1)) if m else None
    return {
        "harvesters_alive": harv,
        "harvesters_deployed": len({r["unit"] for r in emitted["runs"]}),
        "probe_stock": stock,
        "probes_launched": len(emitted["probes"]),
    }


# ──────────────────────────────────────────────────────────────────────────
# dossier
# ──────────────────────────────────────────────────────────────────────────
def _fmt_cell(c: Optional[Cell]) -> str:
    return f"({c[0]},{c[1]})" if c else "—"


def render_dossier(
    entry: Mapping[str, Any], card: Mapping[str, Any], menu: Mapping[str, Any],
    emitted: Mapping[str, Any], delta: Mapping[str, Any],
    inv: Mapping[str, Any], outcome: Mapping[str, Any],
    replay: Optional[Mapping[str, Any]], ms: Optional[int],
) -> str:
    L: List[str] = []
    A = L.append
    A(f"# {entry['label']}")
    A("")
    A(f"- season: `{entry['season']}`  session: `{card['session']}`  day: **{card['day']}**")
    A(f"- question: {entry['question']}")
    A(f"- agent response time: {ms} ms" if ms else "- agent response time: n/a")
    A("")
    A("## INVENTORY vs DEPLOYED")
    A("")
    A(f"| | held | used |")
    A("|---|---|---|")
    A(f"| harvesters | {inv['harvesters_alive']} | {inv['harvesters_deployed']} |")
    A(f"| probes | {inv['probe_stock']} | {inv['probes_launched']} |")
    A("")

    A("## AGENT POLICY (proposed) — exactly what the agent asked for")
    A("")
    A("```")
    for oid in card["picked"]:
        adv = menu.get(oid)
        if adv is None:
            A(f"[{oid}]  (not on menu)")
            continue
        A(f"[{oid}] {adv['title']}")
        for d in adv["detail"]:
            A(f"     {d}")
    A("```")
    A("")
    A(f"agent intent: {card['intent']}")
    A("")

    A("## PACKAGER POLICY (executed) — exactly what was emitted")
    A("")
    A("```")
    for i, mv in enumerate(card["moves"], 1):
        cell = _mv_cell(mv)
        A(f"H{i:02d}  {str(mv.get('a')):<7} {str(mv.get('unit') or ''):<17}"
          f"{_fmt_cell(cell)}")
    A("```")
    A("")
    A(f"packager log: `{card['packager_log']}`")
    A("")

    A("## DELTA — proposed vs executed")
    A("")
    A("| pick | advertised | executed | status |")
    A("|---|---|---|---|")
    for r in delta["rows"]:
        if r.get("status") == "NOT ON MENU":
            A(f"| `{r['id']}` | — | — | NOT ON MENU |")
            continue
        adv = f"drop {_fmt_cell(r.get('adv_drop'))}, {r['adv_cells']} cell(s), red ~+{r['adv_red']:.0f}"
        ex = f"{r.get('exec_cells', 0)} cell(s)"
        if r.get("hour"):
            ex += f", from H{r['hour']:02d}"
        flag = {"OK": "ok", "TRUNCATED": "**TRUNCATED**",
                "DROPPED BY PACKAGER": "**DROPPED**"}.get(r["status"], r["status"])
        A(f"| `{r['id']}` | {adv} | {ex} | {flag} |")
    A("")
    if delta["injected"]:
        A("**Moves the packager added on its own (not from any pick):**")
        A("")
        for j in delta["injected"]:
            if j["kind"] == "probe":
                A(f"- H{j['hour']:02d} probe {_fmt_cell(j['at'])}")
            else:
                A(f"- H{j['hour']:02d} harvester drop {_fmt_cell(j['drop'])}"
                  f" + {j['steps']} step(s)")
        A("")

    A("## OUTCOME (engine truth)")
    A("")
    if not outcome.get("available"):
        A(f"_{outcome.get('note')}_")
    else:
        A(f"- yield: `{outcome.get('yield_line','?')}`")
        A(f"- banked red: **{outcome.get('red_actual')}** (harness expected"
          f" ~{outcome.get('red_expected')})")
        A(f"- parcels banked: {outcome.get('parcels')}")
        rej = outcome.get("rejected") or []
        A(f"- engine rejections: {len(rej)}")
        for r in rej:
            A(f"    - {r}")
        A("")
        A("<details><summary>execution log</summary>")
        A("")
        A("```")
        for l in outcome.get("execution_log") or []:
            A(l)
        A("```")
        A("")
        A("</details>")
    A("")

    if replay:
        A(f"## REPLAY (n={replay['n']}) — same card, asked again")
        A("")
        A("| option | picked | advertised cells | watched |")
        A("|---|---|---|---|")
        for oid, c in replay["per_option"].most_common():
            adv = menu.get(oid) or {}
            w = "yes" if oid in (entry.get("watch") or []) else ""
            A(f"| `{oid}` | {c}/{replay['ok']} | {adv.get('n_cells','?')} | {w} |")
        A("")
        A("exact combinations:")
        A("")
        for combo, c in replay["combos"].most_common():
            A(f"- {c}/{replay['ok']}  `{combo}`")
        A("")
        A(f"- original pick: `{' + '.join(card['picked'])}`")
        A(f"- mean advertised walk length of picked harvester options:"
          f" **{replay['mean_chain']:.1f}** cells")
        A(f"- mean latency: {replay['mean_ms']} ms")
        A("")

    A("---")
    A("")
    A("## THE CARD AS SENT")
    A("")
    A("```text")
    A(card["think_prompt"])
    A("```")
    return "\n".join(L)


# ──────────────────────────────────────────────────────────────────────────
def do_replay(card: Mapping[str, Any], menu: Mapping[str, Any],
              n: int, workers: int) -> Dict[str, Any]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        samples = list(pool.map(
            lambda i: run_sample(
                i, card["think_prompt"], card["plan_prompt"],
                card["reasoning"], "full",
            ),
            range(1, n + 1),
        ))
    ok = [s for s in samples if s.get("parsed")]
    per_option: Counter = Counter()
    combos: Counter = Counter()
    chain_lens: List[int] = []
    for s in ok:
        per_option.update(s["picks"])
        combos[" + ".join(s["picks"]) or "(empty)"] += 1
        for oid in s["picks"]:
            nc = (menu.get(oid) or {}).get("n_cells") or 0
            if nc:
                chain_lens.append(nc)
    lat = [s.get("think_ms", 0) + s.get("plan_ms", 0) for s in ok] or [0]
    return {
        "n": n, "ok": len(ok), "per_option": per_option, "combos": combos,
        "mean_chain": (sum(chain_lens) / len(chain_lens)) if chain_lens else 0.0,
        "mean_ms": sum(lat) // len(lat),
        "samples": samples,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--samples", type=int, default=6)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-replay", action="store_true")
    ap.add_argument("--only", help="build a single label")
    args = ap.parse_args()

    library = json.loads(_LIB_FILE.read_text(encoding="utf-8"))
    if args.only:
        library = [e for e in library if e["label"] == args.only]
        if not library:
            raise SystemExit(f"no library entry {args.only!r}")

    summary: List[Dict[str, Any]] = []
    for entry in library:
        root = _ROOT / entry["root"]
        hits = sorted(root.glob(
            f"{entry['session']}*/turn_*day{entry['day']}_*planning.json"))
        if not hits:
            raise SystemExit(f"{entry['label']}: no capture found")
        card = load_card(hits[0])
        blob = json.loads(hits[0].read_text(encoding="utf-8"))
        extras = blob.get("extras") or {}
        menu = parse_menu(extras.get("option_menu_block") or card["think_prompt"])
        emitted = parse_runs(card["moves"])
        delta = build_delta(card["picked"], menu, emitted)
        inv = parse_inventory(card["think_prompt"], emitted)
        outcome = parse_outcome(find_next_card(root, entry["session"], entry["day"]))
        ms = blob.get("ms_elapsed")

        replay = None
        if not args.no_replay:
            print(f"replaying {entry['label']} x{args.samples} ...")
            replay = do_replay(card, menu, args.samples, args.workers)

        (_LIB_DIR / f"{entry['label']}.md").write_text(
            render_dossier(entry, card, menu, emitted, delta, inv, outcome,
                           replay, ms),
            encoding="utf-8",
        )
        summary.append({
            "label": entry["label"], "season": entry["season"], "day": card["day"],
            "picked": card["picked"], "watch": entry.get("watch") or [],
            "inv": inv, "outcome": outcome, "ms": ms,
            "truncated": [r["id"] for r in delta["rows"]
                          if r.get("status") == "TRUNCATED"],
            "dropped": [r["id"] for r in delta["rows"]
                        if r.get("status") == "DROPPED BY PACKAGER"],
            "injected": len(delta["injected"]),
            "replay": ({
                "per_option": dict(replay["per_option"]),
                "combos": dict(replay["combos"]),
                "ok": replay["ok"], "mean_chain": replay["mean_chain"],
                "mean_ms": replay["mean_ms"],
            } if replay else None),
        })

    _write_summary(summary)
    (_LIB_DIR / "results.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {_LIB_DIR}/SUMMARY.md and {len(summary)} dossier(s)")


def _write_summary(rows: Sequence[Mapping[str, Any]]) -> None:
    L: List[str] = ["# Turn library — summary", ""]
    L.append("| turn | day | held H/P | used H/P | banked red | truncated | dropped |"
             " injected | ms |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        i, o = r["inv"], r["outcome"]
        red = o.get("red_actual") if o.get("available") else "n/a"
        L.append(
            f"| `{r['label']}` | {r['day']} |"
            f" {i['harvesters_alive']}/{i['probe_stock']} |"
            f" {i['harvesters_deployed']}/{i['probes_launched']} | {red} |"
            f" {', '.join(r['truncated']) or '—'} |"
            f" {', '.join(r['dropped']) or '—'} | {r['injected']} | {r['ms']} |"
        )
    L += ["", "## Replay — what the agent picks when asked again", ""]
    for r in rows:
        rep = r.get("replay")
        L.append(f"### `{r['label']}`")
        L.append("")
        L.append(f"original pick: `{' + '.join(r['picked'])}`")
        L.append("")
        if not rep:
            L += ["_not replayed_", ""]
            continue
        watch = r["watch"]
        L.append("| option | frequency | watched |")
        L.append("|---|---|---|")
        for oid, c in sorted(rep["per_option"].items(), key=lambda kv: -kv[1]):
            L.append(f"| `{oid}` | {c}/{rep['ok']} |"
                     f" {'**yes**' if oid in watch else ''} |")
        L.append("")
        missing = [w for w in watch if w not in rep["per_option"]]
        if missing:
            L.append(f"never picked: {', '.join('`%s`' % m for m in missing)}")
            L.append("")
        L.append(f"mean advertised walk length of picked chains:"
                 f" **{rep['mean_chain']:.1f}** cells · mean latency {rep['mean_ms']} ms")
        L.append("")
    (_LIB_DIR / "SUMMARY.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
