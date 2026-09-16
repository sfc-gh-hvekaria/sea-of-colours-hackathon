"""CLI for running orchestrator_2 evals.

Scenario suite for a single agent: fixed board states with assertions
about what a competent agent should do. Use it to check a fork didn't
regress before you queue it for a match.

Usage:
    # Baseline — the heuristic, no credentials needed:
    PYTHONPATH=. python -m sea_of_colours.orchestrator_2.evals.cli \\
        --config grid_v1 --runtime heuristic --backend memory

    # V12, the agent your fork has to beat (needs a Snowflake PAT):
    PYTHONPATH=. python -m sea_of_colours.orchestrator_2.evals.cli \\
        --config tabula_v12 --runtime cortex --backend memory

    # Your fork — any label registered in binding_registry works,
    # no eval config to write:
    PYTHONPATH=. python -m sea_of_colours.orchestrator_2.evals.cli \\
        --config redwatch_reaper --runtime cortex --backend memory

    # One scenario, for a tight edit loop:
    PYTHONPATH=. python -m sea_of_colours.orchestrator_2.evals.cli \\
        --config tabula_v12 --runtime cortex --scenario solo_drop_orbit

    # All scenarios, write a markdown report:
    PYTHONPATH=. python -m sea_of_colours.orchestrator_2.evals.cli \\
        --config tabula_v12 --runtime cortex \\
        --out reports/orchestrator_2_tabula_v12.md

    # Record replayable eval sessions (visible at /evals):
    PYTHONPATH=. python -m sea_of_colours.orchestrator_2.evals.cli \\
        --config tabula_v12 --runtime cortex --replay
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from sea_of_colours.evals import scenarios as _scenarios
from sea_of_colours.evals.runner import ScenarioResult
from sea_of_colours.orchestrator_2.evals.runner import (
    CONFIGS,
    get_config,
    run_scenario,
)


# Snapshot of scenarios available in the v1 evals package. We re-list
# them here so the CLI can iterate without relying on private internals.
ALL_SCENARIO_NAMES = [
    "probes_only",
    "collision_avoidance",
    "tier_choice",
    "blind_dawn",
    "enemy_telegraph",
    "enemy_trail_in_seam",
    "vault_pressure",
    "damaged_harvester",
    "final_night",
    "probe_collision_risk",
    "green_detour",
    "green_corridor",
    "friendly_probe_in_path",
    "multi_hop_seam",
    "enemy_intercept",
    "two_seams_choose_one",
    "solo_drop_orbit",
    "solo_drop_surface",
    # v0.9.23 — regression scenarios for bugs fixed during agent dev.
    "pure_cluster_priority",
    "two_harvesters_distinct_targets",
    "echo_only_no_blind_drop",
    "emp_threat_front_load",
    "posture_aggressive_probes",
    "pure_under_enemy_pressure",
]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orchestrator_2.evals",
        description="Run eval scenarios through orchestrator_2.",
    )
    p.add_argument("--config", default="tabula_v12",
                   help=f"Eval config, or any agent label registered in "
                        f"binding_registry (default: tabula_v12). "
                        f"Known configs: {', '.join(sorted(CONFIGS.keys()))}.")
    p.add_argument("--runtime", choices=("heuristic", "cortex"),
                   default="cortex",
                   help="Agent runtime (default: cortex).")
    p.add_argument("--backend", choices=("snowflake", "memory", "file"),
                   default=None,
                   help=(
                       "Storage backend (default: SOC_BACKEND env). "
                       "Cortex runtime REQUIRES snowflake — the agent's "
                       "tool reaches the session via warehouse-side proc."
                   ))
    p.add_argument("--scenario", action="append", default=None,
                   help=(
                       "Scenario name (repeatable). Default: all scenarios. "
                       f"Known: {', '.join(ALL_SCENARIO_NAMES)}."
                   ))
    p.add_argument("--out", default=None,
                   help=("Write markdown report to this path. Default: "
                         "no file output, just stdout summary."))
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-scenario rationale dump on stdout.")
    p.add_argument("--replay", action="store_true",
                   help=(
                       "Record a replayable eval session per scenario: tag "
                       "season_name='eval:<scenario>:<config>', persist the "
                       "fixture, and resolve the night so it shows up in the "
                       "/evals command center. Without this the run only "
                       "prints reports and persists nothing watchable."
                   ))
    return p


def _print_progress(idx: int, total: int, result: ScenarioResult, elapsed: float):
    verdict = "PASS" if result.passed else "FAIL"
    score = f"{result.pass_count}/{len(result.results)}"
    print(
        f"[{idx:2d}/{total}] {result.scenario_name:32s} "
        f"{verdict} {score}  {elapsed:5.1f}s  {result.agent_id}",
        flush=True,
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.backend:
        os.environ["SOC_BACKEND"] = args.backend
    if args.runtime == "cortex" and os.environ.get("SOC_BACKEND") != "snowflake":
        print("ERROR: cortex runtime requires SOC_BACKEND=snowflake "
              "(--backend snowflake).", file=sys.stderr)
        return 2

    try:
        config = get_config(args.config)
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    scenario_names = args.scenario or ALL_SCENARIO_NAMES

    print(f"\n=== orchestrator_2 eval run ===")
    print(f"  config:    {config.label}  (agent={config.cortex_agent}, view={config.world_view})")
    print(f"  runtime:   {args.runtime}")
    print(f"  backend:   {os.environ.get('SOC_BACKEND', 'memory')}")
    print(f"  scenarios: {len(scenario_names)}")
    print(f"")

    results: List[ScenarioResult] = []
    total_elapsed = 0.0
    for idx, name in enumerate(scenario_names, start=1):
        scenario = _scenarios.get_scenario(name)
        t0 = time.time()
        try:
            result = run_scenario(
                scenario, runtime_override=args.runtime, config=config,
                record_replay=args.replay,
            )
        except Exception as exc:
            elapsed = time.time() - t0
            print(
                f"[{idx:2d}/{len(scenario_names)}] {name:32s} "
                f"ERROR  {elapsed:5.1f}s  {exc}",
                flush=True,
            )
            continue
        elapsed = time.time() - t0
        total_elapsed += elapsed
        _print_progress(idx, len(scenario_names), result, elapsed)
        if not args.quiet:
            # Single-line policy summary.
            policy_summary = ", ".join(
                f"{m.get('a')}{('@'+str(m.get('at'))) if m.get('at') else ''}"
                for m in result.policy[:6]
            )
            if len(result.policy) > 6:
                policy_summary += f", +{len(result.policy)-6} more"
            print(f"      policy: {policy_summary or '(empty)'}")
        if args.replay and result.watch_url:
            print(f"      replay: {result.watch_url}")
        results.append(result)

    # Summary line.
    pass_count = sum(1 for r in results if r.passed)
    print(f"\nTOTAL: {pass_count}/{len(results)} scenarios passed "
          f"in {total_elapsed:.1f}s ({total_elapsed/max(1,len(results)):.1f}s/scenario avg).")

    # Markdown report.
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        body_parts = [
            f"# orchestrator_2 eval report — {config.label}",
            f"",
            f"- agent: `{config.cortex_agent}`",
            f"- runtime: `{args.runtime}`",
            f"- backend: `{os.environ.get('SOC_BACKEND', 'memory')}`",
            f"- scenarios: {len(results)} / total {pass_count} passed",
            f"- wall-clock: {total_elapsed:.1f}s ({total_elapsed/max(1,len(results)):.1f}s avg)",
            f"",
        ]
        for r in results:
            body_parts.append(r.render_markdown())
            body_parts.append("")
        out_path.write_text("\n".join(body_parts))
        print(f"\nReport written to {out_path}")

    return 0 if pass_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
