"""Backend auto-detection (v1.12).

Pins the fix for the worst first-run bug in the repo: ``SOC_BACKEND``
defaulted to ``snowflake`` while snowpark shipped only in the optional
requirements, so a by-the-book install booted a server and then died
with ``ModuleNotFoundError`` on the first game action.

The safety property in :func:`test_auto_never_picks_snowflake_under_pytest`
matters more than the convenience ones. Detection keys off a config file
that exists on any machine that has ever done the key-pair setup, and
``init_session`` wipes the target schema — so a developer running the
suite on their own laptop must never write to their Snowflake account.
"""

from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "memory")

import pytest

from sea_of_colours.snowpark import backend as soc_backend


@pytest.fixture
def sf_config(tmp_path, monkeypatch):
    """Point the backend at a throwaway sf_config and clear the cache."""

    def _write(text: str | None) -> str:
        path = tmp_path / "sf_config"
        if text is not None:
            path.write_text(text)
        monkeypatch.setenv("SF_CONFIG_FILE", str(path))
        soc_backend.reset_for_tests()
        return str(path)

    yield _write
    soc_backend.reset_for_tests()


# ── detection ──────────────────────────────────────────────────────────

def test_no_config_is_not_ready(sf_config):
    path = sf_config(None)
    ready, reason, fix = soc_backend.snowflake_readiness()
    assert ready is False
    # v1.45 — the reason names the file it looked at rather than saying
    # "no config" and leaving you to guess which of several it meant.
    assert path in reason
    assert fix


def test_pat_only_config_is_not_ready(sf_config, tmp_path):
    """A PAT-only config is the *Cortex* setup and implies nothing about storage.

    Someone who followed docs §1 so they could play against V12 has an
    ``sf_config`` — resolving to the Snowflake store off the back of it
    would surprise them and, worse, point NEW GAME at a live schema.
    """
    sf_config("account=abc123\npat=sometoken\n")
    ready, reason, _ = soc_backend.snowflake_readiness()
    assert ready is False
    assert "private_key_file" in reason


def test_keypair_config_is_ready(sf_config, tmp_path):
    key = tmp_path / "rsa_key.p8"
    key.write_text("not-a-real-key")
    sf_config(f"account=abc123\nuser=me\nprivate_key_file={key}\n")
    ready, _, _ = soc_backend.snowflake_readiness()
    # Only meaningful when snowpark is actually installed; when it isn't,
    # the dependency check short-circuits first and that's correct too.
    if soc_backend.snowflake_readiness()[1].endswith("not installed"):
        pytest.skip("snowpark not installed in this environment")
    assert ready is True


def test_dangling_key_file_is_not_ready(sf_config):
    sf_config("account=abc\nuser=me\nprivate_key_file=/nope/missing.p8\n")
    ready, reason, _ = soc_backend.snowflake_readiness()
    assert ready is False
    assert "does not exist" in reason


# ── resolution ─────────────────────────────────────────────────────────

def test_auto_never_picks_snowflake_under_pytest(monkeypatch):
    """The guard that keeps a test run off a developer's own account."""
    monkeypatch.delenv("SOC_BACKEND", raising=False)
    soc_backend.reset_for_tests()
    res = soc_backend.resolution()
    assert res.name == "memory"
    assert "pytest" in res.reason
    soc_backend.reset_for_tests()


@pytest.mark.parametrize("name", soc_backend.VALID_BACKENDS)
def test_explicit_backend_is_honoured(name, monkeypatch):
    monkeypatch.setenv("SOC_BACKEND", name)
    soc_backend.reset_for_tests()
    res = soc_backend.resolution()
    assert res.name == name
    assert res.requested == name
    soc_backend.reset_for_tests()


def test_unknown_backend_falls_back_to_memory_and_says_so(monkeypatch):
    monkeypatch.setenv("SOC_BACKEND", "postgres")
    soc_backend.reset_for_tests()
    res = soc_backend.resolution()
    assert res.name == "memory"
    assert "not one of" in res.reason
    assert res.fix
    soc_backend.reset_for_tests()


def test_explicit_is_strict_but_auto_degrades(monkeypatch):
    """Explicit requests fail loudly; auto falls back. Never the reverse."""
    boom = RuntimeError("Snowflake config not found: /nope")

    def _explode():
        raise boom

    monkeypatch.setattr(soc_backend, "_get_snowflake_store", _explode)

    monkeypatch.setenv("SOC_BACKEND", "snowflake")
    soc_backend.reset_for_tests()
    with pytest.raises(soc_backend.BackendUnavailable) as caught:
        soc_backend.probe_store()
    assert caught.value.fix

    # Same failure reached via auto must degrade rather than raise.
    monkeypatch.delenv("SOC_BACKEND", raising=False)
    soc_backend.reset_for_tests()
    monkeypatch.setattr(
        soc_backend,
        "_resolution",
        soc_backend.BackendResolution(
            name="snowflake", requested="auto", reason="forced for test",
        ),
    )
    res = soc_backend.probe_store()
    assert res.name == "memory"
    assert "failed" in res.reason
    soc_backend.reset_for_tests()


def test_resolution_summary_is_human_readable(monkeypatch):
    monkeypatch.setenv("SOC_BACKEND", "memory")
    soc_backend.reset_for_tests()
    res = soc_backend.resolution()
    assert res.summary() == "store backend: memory — sessions will not persist"
    assert res.persists is False
    soc_backend.reset_for_tests()
