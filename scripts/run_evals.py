#!/usr/bin/env python3
"""CLI for running Sea of Colours agent eval scenarios.

Examples::

    # Run every scenario against the heuristic, print a markdown report
    python -m scripts.run_evals

    # Run one scenario by name
    python -m scripts.run_evals --scenario collision_avoidance

    # Run against the deployed Cortex agent (requires SOC_BACKEND=snowflake
    # in the environment plus the usual Snowpark config). Each scenario
    # is sampled 3 times because Cortex is non-deterministic.
    SOC_BACKEND=snowflake python -m scripts.run_evals --backend cortex --samples 3

    # Pin a specific orchestrator config (Phase 1 A/B). This sets
    # SOC_AGENT_WORLD_VIEW and SOC_CORTEX_AGENT for the run.
    SOC_BACKEND=snowflake python -m scripts.run_evals --backend cortex --config grid_v1

    # Side-by-side compare of two configs across every scenario:
    SOC_BACKEND=snowflake python -m scripts.run_evals \\
        --backend cortex --compare list_v1,grid_v1 --out reports/phase1.md

    # Save the markdown report to a file
    python -m scripts.run_evals --out reports/evals_$(date +%F).md

Output formats:

* Default (``--format markdown``): one section per scenario + a summary
  table. Suitable for committing to a docs/ folder or pasting into a
  PR comment.
* ``--format json``: machine-readable, one object per
  (scenario, sample) row.
* ``--format compact``: one PASS/FAIL line per scenario; for fast CI
  smoke checks.

When ``--compare A,B`` is supplied the output format is forced to a
side-by-side markdown comparison report (PASS/FAIL per config per
scenario, totals, and the delta).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from sea_of_colours.evals import (
    CONFIGS,
    SCENARIOS,
    EvalConfig,
    get_config,
    get_scenario,
    run_scenario,
)
from sea_of_colours.evals.runner import ScenarioResult


def _select_scenarios(names: Optional[Iterable[str]]):
    if not names:
        return list(SCENARIOS)
    out = []
    for n in names:
        try:
            out.append(get_scenario(n))
        except KeyError:
            print(f"warning: no scenario named {n!r}", file=sys.stderr)
    return out


def _emit_markdown(results: List[ScenarioResult]) -> str:
    """Render a full markdown report including the summary table at the top."""
    lines: List[str] = []
    lines.append("# Agent Eval Report")
    lines.append("")
    by_scenario: dict = {}
    for r in results:
        by_scenario.setdefault(r.scenario_name, []).append(r)
    # Summary table — one row per scenario aggregating across samples
    lines.append("| Scenario | Samples | Passed | Detail |")
    lines.append("|---|---|---|---|")
    for name, runs in by_scenario.items():
        n = len(runs)
        passed = sum(1 for r in runs if r.passed)
        # Concatenate the names of failing assertions across samples for context
        failing = []
        for r in runs:
            for ar in r.results:
                if not ar.passed:
                    failing.append(ar.name)
        detail = ", ".join(sorted(set(failing))) if failing else "—"
        lines.append(f"| `{name}` | {n} | {passed}/{n} | {detail} |")
    lines.append("")
    for r in results:
        lines.append(r.render_markdown())
        lines.append("")
    return "\n".join(lines)


def _print_replay_index(results: List[ScenarioResult]) -> None:
    """Print a compact replay-URL index to stdout.

    The markdown report embeds replay links per-scenario, but readers
    landing on the CLI output also want a clickable list right there
    without grepping the report. We group by scenario so the user
    can compare configs side-by-side (e.g. open the list_v1 and
    grid_v1 replays of ``enemy_intercept`` in adjacent tabs).
    """
    replayable = [r for r in results if r.watch_url and r.session_id]
    if not replayable:
        return
    print("")
    print("# Replays")
    by_scenario: Dict[str, List[ScenarioResult]] = {}
    for r in replayable:
        by_scenario.setdefault(r.scenario_name, []).append(r)
    for name in sorted(by_scenario):
        print(f"  {name}:")
        for r in by_scenario[name]:
            tag = r.config_label or r.runtime or "default"
            print(f"    [{tag}] {r.watch_url}")


def _emit_compact(results: List[ScenarioResult]) -> str:
    """One PASS/FAIL line per (scenario, sample). Suitable for CI logs."""
    out = []
    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        suffix = f" [sample {r.sample_index}]" if r.sample_index > 0 else ""
        out.append(
            f"{tag:4} {r.scenario_name}{suffix} "
            f"({r.pass_count}/{len(r.results)})"
        )
    return "\n".join(out)


def _emit_compare_markdown(
    configs: List[EvalConfig],
    results_by_config: Dict[str, List[ScenarioResult]],
) -> str:
    """Render the Phase 1 orchestrator A/B side-by-side report.

    Per scenario, list each config's PASS/FAIL count (aggregated across
    samples) plus the delta vs. the first config. Then a totals row,
    plus per-config aggregate stats (prompt size median, latency median)
    when the runtime captured them. The shape matches the spec in
    `orchestrator_config_search` plan.md and is what a reviewer scans
    to decide which config wins Phase 1.
    """
    if not configs:
        return "(no configs to compare)"

    # Collect every scenario name in the order they first appear.
    scenario_order: List[str] = []
    seen: set = set()
    for cfg in configs:
        for r in results_by_config.get(cfg.label, []):
            if r.scenario_name not in seen:
                scenario_order.append(r.scenario_name)
                seen.add(r.scenario_name)

    today = _dt.date.today().isoformat()
    lines: List[str] = []
    lines.append(f"# Phase 1 — orchestrator config comparison · {today}")
    lines.append("")
    lines.append("## Configs")
    for cfg in configs:
        lines.append(
            f"- **`{cfg.label}`** — world_view=`{cfg.world_view}`, "
            f"agent=`{cfg.cortex_agent}`. {cfg.description}"
        )
    lines.append("")

    # Compute per-(config, scenario) aggregates so the rows fit a table.
    def _agg(cfg_label: str, scenario_name: str) -> Dict[str, int]:
        rows = [
            r for r in results_by_config.get(cfg_label, [])
            if r.scenario_name == scenario_name
        ]
        if not rows:
            return {"passed": 0, "total": 0, "samples": 0}
        total = len(rows[0].results)
        passed = sum(1 for r in rows if r.passed)
        return {"passed": passed, "total": total, "samples": len(rows)}

    headers = ["Scenario"] + [c.label for c in configs]
    if len(configs) >= 2:
        headers.append(f"Δ ({configs[-1].label} − {configs[0].label})")
    lines.append("## Per-scenario results")
    lines.append("")
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    totals = {c.label: {"passed": 0, "total": 0, "samples": 0} for c in configs}
    for name in scenario_order:
        cells = [f"`{name}`"]
        per_cfg_pass = []
        for cfg in configs:
            agg = _agg(cfg.label, name)
            verdict = "PASS" if agg["passed"] == agg["samples"] and agg["samples"] > 0 else "FAIL"
            cells.append(
                f"{verdict} {agg['passed']}/{agg['samples']}"
                if agg["samples"] > 0
                else "—"
            )
            per_cfg_pass.append(agg["passed"])
            totals[cfg.label]["passed"] += agg["passed"]
            totals[cfg.label]["total"] += agg["total"] * agg["samples"]
            totals[cfg.label]["samples"] += agg["samples"]
        if len(configs) >= 2:
            delta = per_cfg_pass[-1] - per_cfg_pass[0]
            cells.append(f"{delta:+d}" if delta != 0 else "—")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    # Aggregate totals row.
    lines.append("## Totals")
    lines.append("")
    lines.append("| Metric | " + " | ".join(c.label for c in configs) + " |")
    lines.append("|" + "|".join(["---"] * (len(configs) + 1)) + "|")
    lines.append(
        "| Scenarios passed (samples) | "
        + " | ".join(
            f"{totals[c.label]['passed']}/{totals[c.label]['samples']}"
            for c in configs
        )
        + " |"
    )
    lines.append("")

    # Per-config full markdown blocks for drill-down.
    for cfg in configs:
        lines.append(f"## Detail · `{cfg.label}`")
        lines.append("")
        for r in results_by_config.get(cfg.label, []):
            lines.append(r.render_markdown())
            lines.append("")
    return "\n".join(lines)


def _emit_json(results: List[ScenarioResult]) -> str:
    """Machine-readable dump."""
    payload = []
    for r in results:
        payload.append({
            "scenario": r.scenario_name,
            "summary": r.summary,
            "sample": r.sample_index,
            "passed": r.passed,
            "agent_id": r.agent_id,
            "runtime": r.runtime,
            "rationale": r.rationale,
            "policy": r.policy,
            "assertions": [
                {
                    "name": a.name,
                    "passed": a.passed,
                    "detail": a.detail,
                    "expected": a.expected,
                    "observed": a.observed,
                }
                for a in r.results
            ],
        })
    return json.dumps(payload, indent=2)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        action="append",
        help="Scenario name to run (repeatable). Default: all.",
    )
    parser.add_argument(
        "--backend",
        choices=("heuristic", "cortex"),
        default="heuristic",
        help=(
            "Agent runtime to evaluate. 'cortex' requires SOC_BACKEND=snowflake "
            "and a working Snowpark session (see sea_of_colours.snowpark.backend)."
        ),
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help=(
            "Number of samples per scenario. Useful for Cortex (non-"
            "deterministic). Default: 1."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "compact"),
        default="markdown",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write output to this file instead of stdout.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available scenarios and exit.",
    )
    parser.add_argument(
        "--config",
        choices=sorted(CONFIGS.keys()),
        help=(
            "Orchestrator config to pin (Phase 1 A/B). Sets "
            "SOC_AGENT_WORLD_VIEW + SOC_CORTEX_AGENT for the run. "
            "Mutually exclusive with --compare."
        ),
    )
    parser.add_argument(
        "--compare",
        help=(
            "Comma-separated config labels (e.g. 'list_v1,grid_v1') to "
            "run side-by-side. Forces --format=markdown and emits the "
            "comparison report. Mutually exclusive with --config."
        ),
    )
    parser.add_argument(
        "--list-configs",
        action="store_true",
        help="List available orchestrator configs and exit.",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help=(
            "Resolve the night after each scenario so replay frames "
            "materialise in the backing store. Adds a watcher URL "
            "(``http://127.0.0.1:8000/?session=<id>``) to each result so "
            "you can scrub the policy in the UI. Override the base via "
            "$SOC_WATCH_BASE_URL. Assertions are unaffected."
        ),
    )
    args = parser.parse_args(argv)

    if args.list:
        for s in SCENARIOS:
            tag_str = f"  [{', '.join(s.tags)}]" if s.tags else ""
            print(f"{s.name}{tag_str}")
            print(f"  {s.summary}")
        return 0

    if args.list_configs:
        for label, cfg in CONFIGS.items():
            print(f"{label}")
            print(f"  world_view={cfg.world_view}  cortex_agent={cfg.cortex_agent}")
            print(f"  {cfg.description}")
        return 0

    if args.config and args.compare:
        print("--config and --compare are mutually exclusive", file=sys.stderr)
        return 2

    scenarios = _select_scenarios(args.scenario)
    if not scenarios:
        print("no scenarios selected", file=sys.stderr)
        return 2

    # ── Comparison mode ───────────────────────────────────────────
    if args.compare:
        labels = [s.strip() for s in args.compare.split(",") if s.strip()]
        try:
            compare_configs = [get_config(lbl) for lbl in labels]
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if len(compare_configs) < 2:
            print(
                "--compare needs at least two configs (e.g. 'list_v1,grid_v1')",
                file=sys.stderr,
            )
            return 2

        results_by_config: Dict[str, List[ScenarioResult]] = {
            c.label: [] for c in compare_configs
        }
        for cfg in compare_configs:
            for s in scenarios:
                for i in range(max(1, args.samples)):
                    res = run_scenario(
                        s,
                        runtime_override=args.backend,
                        sample_index=i,
                        config=cfg,
                        record_replay=args.replay,
                    )
                    results_by_config[cfg.label].append(res)

        body = _emit_compare_markdown(compare_configs, results_by_config)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(body, encoding="utf-8")
            total_runs = sum(len(rs) for rs in results_by_config.values())
            print(
                f"wrote comparison report ({total_runs} runs across "
                f"{len(compare_configs)} configs) to {args.out}"
            )
        else:
            print(body)

        if args.replay:
            _print_replay_index(
                [r for rs in results_by_config.values() for r in rs]
            )

        # Exit nonzero if ANY config has any failing scenario — keeps
        # CI honest about the matrix.
        all_passed = all(
            r.passed for rs in results_by_config.values() for r in rs
        )
        return 0 if all_passed else 1

    # ── Single-config (or no config) mode ─────────────────────────
    cfg = get_config(args.config) if args.config else None

    results: List[ScenarioResult] = []
    for s in scenarios:
        for i in range(max(1, args.samples)):
            res = run_scenario(
                s,
                runtime_override=args.backend,
                sample_index=i,
                config=cfg,
                record_replay=args.replay,
            )
            results.append(res)

    if args.format == "markdown":
        body = _emit_markdown(results)
    elif args.format == "json":
        body = _emit_json(results)
    else:
        body = _emit_compact(results)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body, encoding="utf-8")
        print(f"wrote {len(results)} result(s) to {args.out}")
    else:
        print(body)

    if args.replay:
        _print_replay_index(results)

    # Exit nonzero if anything failed — pipeline-friendly.
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
