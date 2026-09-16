"""Credentials come from the standard Snowflake connection store (v1.45).

The kit used to read a bespoke ``key=value`` file at ``~/.ssh/sf_config``
— not an SSH config despite living there, and not something any other
Snowflake tool has heard of. Attendees arrive with ``connections.toml``
already written by ``snow connection add``, Cortex Code or the VS Code
extension, so reading anything else means asking a room of people to
configure the same account twice.

What these tests pin is the *order*, because every interesting failure on
the day is an ordering question: which file won, which section won, and
whether the thing you explicitly asked for was silently ignored.
"""

from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "memory")

import pytest

from sea_of_colours.snowpark import sfconn


@pytest.fixture(autouse=True)
def _clean_env(tmp_path, monkeypatch):
    """Start from no opinion: the developer's own machine must not leak in.

    The legacy default is redirected unconditionally, not just where a
    test cares. An earlier draft left it pointing at the real
    ``~/.ssh/sf_config`` in one test, and a failing assertion printed the
    author's live PAT into the terminal — a test suite must not be able
    to read, let alone echo, a credential that belongs to the machine it
    happens to be running on.
    """
    for var in (
        "SNOWFLAKE_HOME",
        "SNOWFLAKE_DEFAULT_CONNECTION_NAME",
        "SNOWFLAKE_PAT",
        sfconn.CONNECTION_ENV,
        sfconn.LEGACY_CONFIG_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sfconn, "_LEGACY_DEFAULT", str(tmp_path / "absent_sf_config"))


def _home(tmp_path, monkeypatch, **files):
    d = tmp_path / "sfhome"
    d.mkdir(exist_ok=True)
    for name, text in files.items():
        (d / name.replace("_", ".")).write_text(text)
    monkeypatch.setenv("SNOWFLAKE_HOME", str(d))
    return d


CONNS = """
default_connection_name = "work"

[play]
account = "acct-play"
user = "player"

[work]
account = "acct-work"
user = "worker"
private_key_file = "/tmp/nope.p8"
"""


def test_the_standard_file_is_what_gets_read(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, connections_toml=CONNS)
    props, where = sfconn.resolve_source()
    assert props["account"] == "acct-work"
    assert "connections.toml" in where and "[work]" in where


def test_connections_beats_config_when_both_exist(tmp_path, monkeypatch):
    """The CLI's own precedence, not ours — disagreeing would be worse
    than either choice, because ``snow connection test`` would pass while
    the game connected somewhere else."""
    _home(
        tmp_path,
        monkeypatch,
        connections_toml='[only]\naccount = "from-connections"\n',
        config_toml='[connections.only]\naccount = "from-config"\n',
    )
    props, where = sfconn.resolve_source()
    assert props["account"] == "from-connections"
    assert "connections.toml" in where


def test_config_toml_is_read_when_it_is_the_only_one(tmp_path, monkeypatch):
    _home(
        tmp_path,
        monkeypatch,
        config_toml='[connections.solo]\naccount = "from-config"\n',
    )
    props, where = sfconn.resolve_source()
    assert props["account"] == "from-config"
    assert "config.toml" in where


def test_a_named_connection_is_honoured(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, connections_toml=CONNS)
    monkeypatch.setenv(sfconn.CONNECTION_ENV, "play")
    props, _ = sfconn.resolve_source()
    assert props["account"] == "acct-play"


def test_snowflakes_own_default_env_is_honoured(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, connections_toml=CONNS)
    monkeypatch.setenv("SNOWFLAKE_DEFAULT_CONNECTION_NAME", "play")
    props, _ = sfconn.resolve_source()
    assert props["account"] == "acct-play"


def test_a_name_that_does_not_exist_is_refused_not_swapped(tmp_path, monkeypatch):
    """The important one.

    Silently falling back to a different account is how someone spends an
    afternoon debugging permissions on a warehouse they were never
    pointed at. Asking for a connection that is not there must fail, and
    must say what IS there.
    """
    _home(tmp_path, monkeypatch, connections_toml=CONNS)
    monkeypatch.setenv(sfconn.CONNECTION_ENV, "wrok")
    props, where = sfconn.resolve_source()
    assert props == {}
    assert "wrok" in where
    assert "play" in where and "work" in where  # lists the real names


# ── the legacy file ────────────────────────────────────────────────────

LEGACY = "ACCOUNT=old-acct\nUSER=old-user\nPAT=old-token\n"


def test_the_legacy_file_still_works_when_there_is_no_standard_one(
    tmp_path, monkeypatch
):
    """Deprecated, not removed: the machines running the event depend on
    it, and breaking an install the week of the hackathon is worse than
    carrying the compatibility."""
    legacy = tmp_path / "sf_config"
    legacy.write_text(LEGACY)
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path / "empty"))
    monkeypatch.setenv(sfconn.LEGACY_CONFIG_ENV, str(legacy))
    props, where = sfconn.resolve_source()
    assert props["account"] == "old-acct"
    assert "sf_config" in where


def test_the_standard_file_wins_over_a_legacy_one_left_lying_around(
    tmp_path, monkeypatch
):
    _home(tmp_path, monkeypatch, connections_toml=CONNS)
    legacy = tmp_path / "sf_config"
    legacy.write_text(LEGACY)
    # NOT exported: merely existing at the default path must not win.
    monkeypatch.setattr(sfconn, "_LEGACY_DEFAULT", str(legacy))
    props, _ = sfconn.resolve_source()
    assert props["account"] == "acct-work"


def test_an_explicitly_exported_legacy_path_beats_the_standard_store(
    tmp_path, monkeypatch
):
    """Exporting SF_CONFIG_FILE is a deliberate act; honour it."""
    _home(tmp_path, monkeypatch, connections_toml=CONNS)
    legacy = tmp_path / "sf_config"
    legacy.write_text(LEGACY)
    monkeypatch.setenv(sfconn.LEGACY_CONFIG_ENV, str(legacy))
    props, where = sfconn.resolve_source()
    assert props["account"] == "old-acct"
    assert "SF_CONFIG_FILE" in where


# ── the PAT, which travels separately ──────────────────────────────────

def test_a_keypair_connection_without_a_token_still_finds_the_legacy_pat(
    tmp_path, monkeypatch
):
    """A key-pair connection is complete and correct and has no bearer
    token in it. Refusing to look further would break every setup that
    keeps persistence and Cortex credentials apart."""
    _home(tmp_path, monkeypatch, connections_toml=CONNS)
    legacy = tmp_path / "sf_config"
    legacy.write_text(LEGACY)
    monkeypatch.setattr(sfconn, "_LEGACY_DEFAULT", str(legacy))
    account, pat, where = sfconn.resolve_cortex()
    assert pat == "old-token"
    # Account travels WITH the token: a PAT is scoped to the account that
    # issued it, and mixing them fails as an unexplained 401.
    assert account == "old-acct"
    assert "sf_config" in where


def test_the_environment_beats_any_file(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, connections_toml=CONNS)
    monkeypatch.setenv("SNOWFLAKE_PAT", "env-token")
    _account, pat, where = sfconn.resolve_cortex()
    assert pat == "env-token"
    assert "env" in where


def test_a_token_on_the_connection_is_used(tmp_path, monkeypatch):
    _home(
        tmp_path,
        monkeypatch,
        connections_toml='[only]\naccount = "a"\ntoken = "toml-token"\n',
    )
    _account, pat, _where = sfconn.resolve_cortex()
    assert pat == "toml-token"


def test_a_pat_authenticator_puts_the_token_in_password(tmp_path, monkeypatch):
    """Snowflake permits the PAT in ``password`` under a PAT
    authenticator; normalise it so downstream never has to know."""
    _home(
        tmp_path,
        monkeypatch,
        connections_toml=(
            '[only]\naccount = "a"\n'
            'authenticator = "PROGRAMMATIC_ACCESS_TOKEN"\n'
            'password = "pw-token"\n'
        ),
    )
    _account, pat, _where = sfconn.resolve_cortex()
    assert pat == "pw-token"


def test_a_password_without_a_pat_authenticator_is_not_a_token(tmp_path, monkeypatch):
    """Ordinary password auth must not be mistaken for a bearer token."""
    _home(
        tmp_path,
        monkeypatch,
        connections_toml='[only]\naccount = "a"\npassword = "hunter2"\n',
    )
    _account, pat, _where = sfconn.resolve_cortex()
    assert pat == ""


def test_nothing_configured_says_so_rather_than_exploding(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path / "nowhere"))
    monkeypatch.setattr(sfconn, "_LEGACY_DEFAULT", str(tmp_path / "nofile"))
    props, where = sfconn.resolve_source()
    assert props == {}
    assert where


def test_a_broken_toml_does_not_crash_a_boot(tmp_path, monkeypatch):
    """`snow connection test` explains a malformed file far better than we
    could; our job is to not take the game down over it."""
    _home(tmp_path, monkeypatch, connections_toml="[unclosed\n")
    monkeypatch.setattr(sfconn, "_LEGACY_DEFAULT", str(tmp_path / "nofile"))
    props, _where = sfconn.resolve_source()
    assert props == {}
