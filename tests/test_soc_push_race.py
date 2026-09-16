"""`soc push` has to survive forty teams pushing to one branch.

The publishing model is that everyone pushes to the same repo, often,
all day. Git rejects every push that is not a fast-forward, so on a busy
afternoon most pushes lose a race — and before v1.41 that surfaced as a
raw CalledProcessError traceback, at the moment an attendee is least
equipped to read one.

Rebasing is safe here *because* of the one-directory rule: two teams
never write the same file, so their histories interleave with nothing to
resolve. These tests pin both halves of that — that the retry happens,
and that the thing it relies on (no shared files) actually holds.
"""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.usefixtures("_git_identity")


def _git(*args: str, cwd, check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check,
        capture_output=True, text=True,
    )


@pytest.fixture
def _git_identity(monkeypatch):
    """Keep the test independent of whatever the machine's git config is."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.invalid")
    # A repo-level hook on the dev machine must not run inside the fixture.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


@pytest.fixture
def room(tmp_path):
    """A bare 'origin' plus two teams cloned off it."""
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)

    seats = {}
    for who in ("alice", "bob"):
        wt = tmp_path / who
        _git("clone", "-q", str(origin), str(wt), cwd=tmp_path)
        _git("checkout", "-q", "-B", "main", cwd=wt)
        seats[who] = wt

    # Seed the branch so both clones share a base commit.
    a = seats["alice"]
    (a / "shared.txt").write_text("kit\n")
    _git("add", "-A", cwd=a)
    _git("commit", "-qm", "init", cwd=a)
    _git("push", "-q", "origin", "main", cwd=a)
    _git("fetch", "-q", "origin", cwd=seats["bob"])
    _git("checkout", "-q", "-B", "main", "origin/main", cwd=seats["bob"])
    return seats


def _commit_agent(wt, team: str) -> None:
    d = wt / "harnesses" / team
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent.json").write_text(f'{{"team": "{team}"}}\n')
    _git("add", "--", f"harnesses/{team}", cwd=wt)
    _git("commit", "-qm", f"{team}: update agent", cwd=wt)


def test_the_loser_of_a_race_is_rejected(room):
    """The failure this guards against is real, not hypothetical."""
    _commit_agent(room["alice"], "alice_bot")
    _git("push", "-q", "origin", "main", cwd=room["alice"])

    _commit_agent(room["bob"], "bob_bot")
    losing = _git("push", "origin", "main", cwd=room["bob"], check=False)
    assert losing.returncode != 0
    assert "rejected" in (losing.stderr + losing.stdout)


def test_rebasing_resolves_it_with_no_conflict(room):
    """The one-directory rule is what makes the retry safe. Pin it."""
    _commit_agent(room["alice"], "alice_bot")
    _git("push", "-q", "origin", "main", cwd=room["alice"])
    _commit_agent(room["bob"], "bob_bot")

    bob = room["bob"]
    _git("pull", "-q", "--rebase", "origin", "main", cwd=bob)
    unresolved = _git(
        "diff", "--name-only", "--diff-filter=U", cwd=bob,
    ).stdout.split()
    assert not unresolved, "two teams' folders must never conflict"

    _git("push", "-q", "origin", "main", cwd=bob)
    listed = _git("ls-tree", "-r", "--name-only", "origin/main", cwd=bob).stdout
    assert "harnesses/alice_bot/agent.json" in listed
    assert "harnesses/bob_bot/agent.json" in listed, (
        "the loser's agent must survive the rebase — losing a race must "
        "never mean losing an entrant"
    )


def test_a_shared_file_is_what_actually_conflicts(room):
    """Why `soc push` refuses changes outside your folder, demonstrated.

    If two teams edit kit, the rebase this relies on stops being
    automatic. This is the case the retry deliberately does NOT paper
    over — it aborts and sends the attendee to an organiser.
    """
    for who, text in (("alice", "alice was here\n"), ("bob", "bob was here\n")):
        (room[who] / "shared.txt").write_text(text)
        _git("add", "-A", cwd=room[who])
        _git("commit", "-qm", f"{who} edits the kit", cwd=room[who])

    _git("push", "-q", "origin", "main", cwd=room["alice"])
    rebased = _git(
        "pull", "--rebase", "origin", "main", cwd=room["bob"], check=False,
    )
    assert rebased.returncode != 0, (
        "a shared-file edit should conflict — if this ever passes cleanly "
        "the one-directory rule has stopped being load-bearing"
    )
    _git("rebase", "--abort", cwd=room["bob"], check=False)
