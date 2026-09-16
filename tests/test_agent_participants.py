"""An agent must say who built it (v1.41).

The league table is the day's public record. A row reading
``redwatch_reaper`` and nothing else cannot be credited to anybody,
cannot be chased when it breaks, and cannot tell two forks of the same
idea apart. So the roster is required, and required in three places
because one is never enough:

* ``soc new`` will not mint without it — the gate almost everyone meets;
* ``soc push`` will not publish without it — the gate that matters,
  because it is the last point before the repo;
* the manifest loader rejects it — the backstop for a hand-written file,
  which is the only way to get past the first two.

The ordering matters. Discovery *skips* an unusable manifest, so if the
loader were the only check a team would publish happily all day and
simply not appear in the league. Catching it at push means the silent
absence cannot happen to anyone who used the tools.
"""

from __future__ import annotations

import json

import pytest

from sea_of_colours.orchestrator_2 import agent_manifest


def _write(tmp_path, **overrides):
    data = {
        "team": "redwatch",
        "name": "reaper",
        "participants": ["Ada Lovelace", "Grace Hopper"],
        "menu_label": "REAPER",
        "entry": "harness:run",
        "needs_llm": True,
    }
    data.update(overrides)
    for key in [k for k, v in data.items() if v is _OMIT]:
        del data[key]
    d = tmp_path / f"{data.get('team', 'x')}_{data.get('name', 'y')}"
    d.mkdir(exist_ok=True)
    path = d / "agent.json"
    path.write_text(json.dumps(data))
    return path


_OMIT = object()


# ── the manifest ────────────────────────────────────────────────────

def test_a_roster_is_read_and_kept_in_order(tmp_path):
    m = agent_manifest.load(_write(tmp_path))
    assert m.participants == ("Ada Lovelace", "Grace Hopper")


def test_a_manifest_without_participants_is_rejected(tmp_path):
    with pytest.raises(agent_manifest.ManifestError) as e:
        agent_manifest.load(_write(tmp_path, participants=_OMIT))
    assert "participants" in str(e.value)
    assert "Ada Lovelace" in str(e.value), "the error must show the shape"


def test_an_empty_roster_is_rejected(tmp_path):
    with pytest.raises(agent_manifest.ManifestError, match="at least one"):
        agent_manifest.load(_write(tmp_path, participants=[]))


def test_blank_names_do_not_count_as_a_roster(tmp_path):
    with pytest.raises(agent_manifest.ManifestError):
        agent_manifest.load(_write(tmp_path, participants=["", "   "]))


def test_a_bare_string_is_rejected_with_the_fix(tmp_path):
    """The likeliest hand-edit mistake, so the message has to teach."""
    with pytest.raises(agent_manifest.ManifestError) as e:
        agent_manifest.load(_write(tmp_path, participants="Ada"))
    assert '["Ada"]' in str(e.value)


def test_names_are_free_text(tmp_path):
    """A roster is not an identity system — handles and unicode are fine."""
    m = agent_manifest.load(
        _write(tmp_path, participants=["ada", "Grace H.", "Ada Lovelace"])
    )
    assert len(m.participants) == 3


def test_whitespace_is_trimmed(tmp_path):
    m = agent_manifest.load(_write(tmp_path, participants=["  Ada  "]))
    assert m.participants == ("Ada",)


# ── the shipped forks ───────────────────────────────────────────────

def test_every_discovered_agent_names_its_authors():
    """Whatever is in the repo must satisfy the rule it imposes."""
    found, problems = agent_manifest.discover()
    assert not problems, f"a shipped manifest does not load: {problems}"
    for m in found:
        assert m.participants, f"{m.label} does not say who built it"


# ── the error an attendee actually meets ────────────────────────────

def test_a_skipped_fork_is_not_reported_as_missing():
    """The folder is right there; "no such agent" would send them to
    delete it and start over.

    Discovery skips a manifest it cannot load, so by the time `soc push`
    resolves the name, a fork with one bad line looks identical to a
    fork that was never created. The two need different advice.
    """
    import scripts.soc as soc

    problem = (
        "/repo/sea_of_colours/orchestrator_2/harnesses/redwatch_reaper/"
        "agent.json: missing required field 'participants'."
    )
    with pytest.raises(SystemExit):
        soc._resolve_agent([], "redwatch_reaper", [problem])

    with pytest.raises(SystemExit) as e:
        soc._resolve_agent([], "someone_else", [problem])
    assert e.value.code != 0


def test_the_skipped_fork_error_quotes_the_problem(capsys):
    """The fix has to be in the message; the reader is often an agent."""
    import scripts.soc as soc

    problem = (
        "/repo/sea_of_colours/orchestrator_2/harnesses/redwatch_reaper/"
        "agent.json: missing required field 'participants'."
    )
    with pytest.raises(SystemExit):
        soc._resolve_agent([], "redwatch_reaper", [problem])
    err = capsys.readouterr().err
    assert "participants" in err
    assert "mint one" not in err, "must not advise creating what exists"


# ── the template ────────────────────────────────────────────────────

def test_the_template_produces_a_loadable_manifest(tmp_path):
    """The template is copied by hand, so it has to be correct itself."""
    text = agent_manifest.TEMPLATE % {
        "team": "redwatch",
        "name": "reaper",
        "participants": json.dumps(["Ada Lovelace"]),
        "menu_label": "REAPER",
    }
    d = tmp_path / "redwatch_reaper"
    d.mkdir()
    (d / "agent.json").write_text(text)
    m = agent_manifest.load(d / "agent.json")
    assert m.participants == ("Ada Lovelace",)
