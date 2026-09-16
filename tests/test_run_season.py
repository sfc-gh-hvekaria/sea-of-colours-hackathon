"""Regression contract for the headless season runner.

The CLI is a thin wrapper over ``soc_engine.init_session`` +
``runtime.run_agent_turn``, so the surface area that needs locking is
small:

1. A heuristic-vs-heuristic run on the memory backend completes a full
   ``SEASON_DAY_CAP`` season and exits ``0``.
2. Requesting a Cortex seat with ``SOC_BACKEND=memory`` exits ``2`` with
   a preflight error pointing the operator at ``--backend snowflake``.
3. The seed is deterministic — same seed → same auto-generated season
   name (rule of the watcher's "load any season by slug" contract).

Tests are kept lightweight: we invoke ``run_season.main`` in-process
with ``argv`` rather than ``subprocess`` so the assertions can read the
captured stdout directly. The memory backend is forced via env-var so
no Snowflake credentials are required to run these tests in CI.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _import_runner():
    """Import ``scripts.run_season`` fresh so env-var changes are honoured."""
    # ``soc_backend`` snapshots SOC_BACKEND at import time; force a
    # reload of any module that may have cached the previous value.
    for name in (
        "scripts.run_season",
        "sea_of_colours.snowpark.backend",
        "sea_of_colours.snowpark.engine",
        "sea_of_colours.agent.runtime",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    import scripts.run_season as runner  # noqa: E402
    return runner


@pytest.fixture
def memory_backend(monkeypatch: pytest.MonkeyPatch):
    """Force the memory backend for the duration of the test."""
    monkeypatch.setenv("SOC_BACKEND", "memory")
    yield


def test_heuristic_vs_heuristic_completes_full_season(
    memory_backend, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end happy path: the runner drives a full season to completion.

    The default cap is now 10 (v0.8.0); this test passes the cap
    explicitly so it stays a fast unit test rather than a 2x slower
    full-cap run. Asserting the banner + final-block text proves the
    runner threads the override all the way through to the report.
    """
    runner = _import_runner()
    exit_code = runner.main(
        [
            "--seed", "42",
            "--width", "24",
            "--height", "16",
            "--days", "5",
            "--quiet",
        ],
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured.out + captured.err
    out = captured.out
    # Banner + final block are the load-bearing UX surface; assert both
    # rendered with the deterministic season name for seed=42.
    assert "SEA OF COLOURS — headless season runner" in out
    assert "SEASON_COMPLETE — Tempus_Falcon" in out
    assert "days played  : 5/5" in out
    # Watcher link must include the lowercase URL-safe slug.
    assert "watch URL   : /watch.html?season=tempus-falcon" in out


def test_cortex_seat_is_rejected(
    memory_backend, capsys: pytest.CaptureFixture[str]
) -> None:
    """The 'cortex' runtime was removed with the Agents-API specs. This
    runner only drives deterministic seats now; LLM seasons go through
    scripts/run_matchup_v12.py."""
    runner = _import_runner()
    with pytest.raises(SystemExit) as excinfo:
        runner.main(["--seed", "1", "--p1", "cortex"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice: 'cortex'" in err
    assert "red_harvest_lite" in err


def test_season_name_deterministic_from_seed(
    memory_backend, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same seed → same auto-generated season name (watcher contract)."""
    runner = _import_runner()

    def _run_and_capture(seed: int) -> str:
        runner.main([
            "--seed", str(seed), "--width", "16", "--height", "12", "--quiet",
        ])
        text = capsys.readouterr().out
        # Pull the ``SEASON_COMPLETE — <name>`` line so we compare just
        # the season label, not the session_id (uuid is non-deterministic).
        for line in text.splitlines():
            if "SEASON_COMPLETE — " in line:
                return line.split("SEASON_COMPLETE — ", 1)[1].strip()
        raise AssertionError(
            "no SEASON_COMPLETE line in runner output:\n" + text
        )

    first = _run_and_capture(123)
    second = _run_and_capture(123)
    assert first == second, (
        f"season name not deterministic for seed=123: {first!r} vs {second!r}"
    )
    # Sanity: a different seed should land on a different name (extremely
    # unlikely to collide in our 40×40 word pool, but assert at least the
    # mechanism is wired).
    other = _run_and_capture(456)
    assert other != first, (
        f"seed 123 and 456 produced same season name {first!r} — "
        "deterministic generator may be ignoring its input"
    )
