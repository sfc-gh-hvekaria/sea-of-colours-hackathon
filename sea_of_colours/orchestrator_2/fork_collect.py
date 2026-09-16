"""Gathering the room's agents back out of their forks.

v1.44. The day runs on GitHub forks: each team forks the repo, clones
their own copy, and pushes to it. That keeps the main repo clean, keeps
one team's work out of another's face, and — the reason that actually
decides it — means nobody has to be given write access to the repo the
whole event is running from.

The cost is that the league is no longer a directory scan. The agents
are spread across forty repositories at the end of the day and something
has to bring them home. This is that something.

**Home is a staging area, not this repo.** Collection builds a separate
checkout — ``../soc-league`` by default — and assembles the field there.
The repo you are standing in is never written to, which matters for three
reasons. The forks keep pulling from upstream all day, and forty
entrants landing on ``main`` is forty merge conflicts posted to everyone
at once. The staging area is disposable, so a bad collection is fixed by
deleting a directory rather than by unpicking commits. And each entrant
stays owned by the fork it came from: the staging area is where the
league is *run*, never where an agent *lives*. Re-collecting resets to
upstream and re-fetches everybody, so the assembly is always a true
picture of the forks as they stand and never an accumulation of
half-remembered earlier runs.

**Why fetch rather than clone.** ``git fetch --depth 1 <url> <branch>``
followed by ``git checkout FETCH_HEAD -- <dir>`` lands one directory in
the working tree without a second checkout of the whole repo on disk.
Forty shallow fetches of a repo you already have most of is fast; forty
clones is a coffee break.

**What it will and will not take.** Only a directory under
``harnesses/`` that holds a valid ``agent.json``, and never one whose
label is already spoken for — by a shipped agent, or by a fork collected
earlier in the same run. A collision is reported and skipped rather than
resolved, because the two candidates are different teams' work and
picking one silently is the one outcome nobody wants. The
one-directory-per-fork rule (enforced at ``soc push``) is what keeps
collisions to a genuine name clash rather than a merge.

**Nothing here trusts a fork.** The paths are validated before checkout
so a crafted tree cannot write outside ``harnesses/``, and manifests are
loaded through the ordinary validator so a broken one is a skipped
entrant and not a crashed collection. The Python still runs when you
cast it — same honesty as :mod:`fork_parcel`.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from . import agent_manifest

#: Where a harness lives, relative to the repo root. Anything a fork
#: offers outside this prefix is not an agent and is not collected.
HARNESS_PREFIX = "sea_of_colours/orchestrator_2/harnesses"

#: Dropped in a staging area the first time we build one. Collection
#: resets its target hard, so it will only ever point at a directory
#: carrying this file — that is the whole guard against someone typing
#: `--into ~/work` and losing an afternoon.
STAGING_MARKER = ".soc-league-staging"

#: Agents that ship with the kit. A fork cannot hand one of these back —
#: taking a fork's `tabula_v12` would replace the baseline the whole
#: league is measured against with somebody's edited copy of it.
def shipped_labels() -> frozenset[str]:
    from . import binding_registry

    return frozenset(getattr(binding_registry, "SHIPPED_AGENT_LABELS", ()))


class CollectError(RuntimeError):
    """Collection cannot proceed. The message names the fix."""


@dataclass(frozen=True)
class Fork:
    """One entrant's repository."""

    owner: str
    full_name: str
    clone_url: str
    default_branch: str = "main"


@dataclass
class Taken:
    """One agent successfully collected."""

    label: str
    fork: str
    participants: tuple[str, ...] = ()


@dataclass
class Skipped:
    """One thing not collected, and why. Always reported, never silent."""

    fork: str
    what: str
    why: str


@dataclass
class Report:
    taken: list[Taken] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(s.why.startswith("collides") for s in self.skipped)


# ── talking to GitHub ─────────────────────────────────────────────────

def _gh(args: Sequence[str]) -> str:
    done = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        err = (done.stderr or "").strip()
        if "could not find" in err.lower() or "not logged" in err.lower():
            raise CollectError(
                f"gh could not talk to GitHub: {err}\n"
                f"  fix: run `gh auth login`"
            )
        raise CollectError(f"gh {' '.join(args)} failed: {err}")
    return done.stdout


def upstream_of(remote_url: str) -> str:
    """``owner/name`` for a remote URL, however it is spelled.

    Handles ssh://, git@ and https:// because a room of laptops will
    produce all three and the difference is not interesting.
    """
    cleaned = remote_url.strip().removesuffix(".git")
    match = re.search(r"[:/]([^/:]+)/([^/]+)$", cleaned)
    if not match:
        raise CollectError(
            f"cannot read an owner/name out of the remote {remote_url!r}"
        )
    return f"{match.group(1)}/{match.group(2)}"


def forks_of(repo: str, *, gh: Callable[[Sequence[str]], str] = _gh) -> list[Fork]:
    """Every fork GitHub knows about, newest first."""
    raw = gh([
        "api", f"repos/{repo}/forks", "--paginate",
        "--jq", ".[] | {owner: .owner.login, full_name, clone_url, "
                "default_branch}",
    ])
    found: list[Fork] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        found.append(Fork(
            owner=str(row.get("owner") or ""),
            full_name=str(row.get("full_name") or ""),
            clone_url=str(row.get("clone_url") or ""),
            default_branch=str(row.get("default_branch") or "main"),
        ))
    return found


# ── deciding what to take ─────────────────────────────────────────────

def candidate_dirs(tree: Iterable[str]) -> list[str]:
    """Harness directory names in a fork, from ``git ls-tree`` output.

    A directory counts only if it holds an ``agent.json`` at its top
    level, which is the same rule discovery uses. Paths are matched
    against an anchored pattern rather than split on ``/``, so ``..``
    or a nested lookalike deeper in the tree cannot present itself as a
    harness.
    """
    pattern = re.compile(
        rf"^{re.escape(HARNESS_PREFIX)}/([a-z][a-z0-9_]*)/"
        rf"{re.escape(agent_manifest.MANIFEST_NAME)}$"
    )
    out: list[str] = []
    for path in tree:
        found = pattern.match(path.strip())
        if found:
            out.append(found.group(1))
    return sorted(set(out))


def _claimed(root: Path) -> dict[str, str]:
    """Labels already spoken for locally, and by what."""
    claimed = {label: "the shipped kit" for label in shipped_labels()}
    base = root / HARNESS_PREFIX
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if (child / agent_manifest.MANIFEST_NAME).is_file():
                claimed.setdefault(child.name, "already in this repo")
    return claimed


# ── doing it ──────────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )


def collect(
    root: Path,
    forks: Sequence[Fork],
    *,
    dry_run: bool = False,
    skip_owners: Iterable[str] = (),
) -> Report:
    """Bring every fork's agents into ``root``'s working tree."""
    report = Report()
    # Everything the staging area already has: the shipped kit, and
    # anything upstream ships. Every fork carries a copy of all of it,
    # because that is what forking means — so these are not entrants and
    # not collisions, they are just the repo. Skipping them loudly would
    # bury the one line that matters under forty copies of "tabula_v12
    # is already here". A fork that edited the baseline is ignored the
    # same way: the baseline is what the league is measured against, and
    # it comes from upstream or it means nothing.
    inherited = _claimed(root)
    # Where each collected label came from, so the second fork to claim
    # a name is told who has it rather than just "taken".
    origin_of: dict[str, str] = {}
    skip = {o.lower() for o in skip_owners}

    for fork in forks:
        if fork.owner.lower() in skip:
            report.skipped.append(
                Skipped(fork.full_name, "(whole fork)", "owner skipped")
            )
            continue

        fetched = _git(
            root, "fetch", "--depth", "1", "--quiet",
            fork.clone_url, fork.default_branch,
        )
        if fetched.returncode != 0:
            report.skipped.append(Skipped(
                fork.full_name, "(whole fork)",
                f"could not fetch: {(fetched.stderr or '').strip()[:120]}",
            ))
            continue

        listed = _git(root, "ls-tree", "-r", "--name-only", "FETCH_HEAD")
        if listed.returncode != 0:
            report.skipped.append(Skipped(
                fork.full_name, "(whole fork)", "could not read its tree",
            ))
            continue

        names = [
            label for label in candidate_dirs(listed.stdout.splitlines())
            if label not in inherited
        ]
        if not names:
            report.skipped.append(Skipped(
                fork.full_name, "(whole fork)", "no agent of its own",
            ))
            continue

        for label in names:
            if label in origin_of:
                report.skipped.append(Skipped(
                    fork.full_name, label,
                    f"collides — {label!r} was already collected from "
                    f"{origin_of[label]}",
                ))
                continue

            path = f"{HARNESS_PREFIX}/{label}"
            if dry_run:
                report.taken.append(Taken(label, fork.full_name))
                origin_of[label] = fork.full_name
                continue

            out = _git(root, "checkout", "FETCH_HEAD", "--", path)
            if out.returncode != 0:
                report.skipped.append(Skipped(
                    fork.full_name, label,
                    f"checkout failed: {(out.stderr or '').strip()[:120]}",
                ))
                continue

            # Load it the ordinary way. A fork whose manifest does not
            # survive validation is an entrant we cannot register, and
            # leaving the files behind would break everybody else's
            # discovery — so it comes straight back out.
            manifest_path = root / path / agent_manifest.MANIFEST_NAME
            try:
                manifest = agent_manifest.load(manifest_path)
            except agent_manifest.ManifestError as exc:
                _git(root, "rm", "-r", "-f", "-q", "--", path)
                report.skipped.append(Skipped(
                    fork.full_name, label, f"unusable manifest: {exc}",
                ))
                continue

            report.taken.append(
                Taken(label, fork.full_name, manifest.participants)
            )
            origin_of[label] = fork.full_name

    return report


# ── the staging area ──────────────────────────────────────────────────

def prepare_staging(into: Path, repo: str, *, clone_url: str = "") -> Path:
    """Build, or reset, the checkout the field gets assembled in.

    Fresh directory: clone upstream. Existing staging area: throw away
    whatever the last run assembled and return to upstream, so a
    collection is a snapshot of the forks rather than a pile of
    everything ever collected. An agent withdrawn from a fork must
    disappear from the league, and only a reset makes that true.

    Refuses to touch a directory that is not empty and not already a
    staging area of ours. That check is the difference between a tool
    that resets its scratch space and a tool that resets your homedir.
    """
    url = clone_url or f"https://github.com/{repo}.git"
    marker = into / STAGING_MARKER

    if not into.exists():
        into.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            ["git", "clone", "--quiet", url, str(into)],
            capture_output=True, text=True, check=False,
        )
        if done.returncode != 0:
            raise CollectError(
                f"could not clone {repo} into {into}: "
                f"{(done.stderr or '').strip()[:200]}"
            )
        marker.write_text(f"{repo}\n", encoding="utf-8")
        return into

    if not marker.is_file():
        raise CollectError(
            f"{into} already exists and is not a staging area this tool "
            f"made.\n"
            f"  Collection resets its target, so it will not touch a "
            f"directory it does not recognise.\n"
            f"  fix: point --into somewhere new, or delete {into} yourself"
        )

    was = marker.read_text(encoding="utf-8").strip()
    if was and was != repo:
        raise CollectError(
            f"{into} is a staging area for {was}, not {repo}.\n"
            f"  fix: use a different --into for a different upstream"
        )

    branch = _git(into, "symbolic-ref", "--short", "HEAD").stdout.strip()
    fetched = _git(into, "fetch", "--quiet", "origin")
    if fetched.returncode != 0:
        raise CollectError(
            f"could not refresh {into} from origin: "
            f"{(fetched.stderr or '').strip()[:200]}"
        )
    _git(into, "reset", "--hard", "--quiet", f"origin/{branch or 'main'}")
    # Collected agents are untracked here, so reset alone leaves them
    # behind — which would quietly readmit an entrant who had withdrawn.
    _git(into, "clean", "-qfd", "--", HARNESS_PREFIX)
    return into


def write_roster(root: Path, report: "Report", repo: str) -> Path:
    """Record who is in the field and which fork they came out of.

    A collected directory looks exactly like a shipped one, so without
    this there is no way to answer "whose is this?" when an entrant
    misbehaves during the league — and no way for a team to check they
    were actually collected.
    """
    lines = [
        "# The field",
        "",
        f"Collected from the forks of `{repo}`.",
        "",
        "This is a staging area. Every agent below lives in the fork named",
        "beside it — that is where to send a fix, and where the team keeps",
        "working. Nothing here is upstream of anything.",
        "",
        "| Agent | Fork | Participants |",
        "| --- | --- | --- |",
    ]
    for t in sorted(report.taken, key=lambda x: x.label):
        who = ", ".join(t.participants) or "—"
        lines.append(f"| `{t.label}` | `{t.fork}` | {who} |")
    if report.skipped:
        lines += [
            "",
            "## Not collected",
            "",
            "| Fork | What | Why |",
            "| --- | --- | --- |",
        ]
        for s in report.skipped:
            lines.append(f"| `{s.fork}` | {s.what} | {s.why} |")
    out = root / "ENTRANTS.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def resolve_repo(root: Path, explicit: Optional[str] = None) -> str:
    """Which repo's forks to collect.

    Defaults to whatever ``origin`` points at, unless ``origin`` is
    itself a fork — in which case the entrants are siblings under its
    parent, not children of it.
    """
    if explicit:
        return explicit
    url = _git(root, "remote", "get-url", "origin").stdout.strip()
    if not url:
        raise CollectError(
            "this checkout has no 'origin' remote, so there is nothing to "
            "collect forks of.\n  fix: pass --repo owner/name"
        )
    repo = upstream_of(url)
    try:
        parent = _gh([
            "repo", "view", repo, "--json", "parent",
            "--jq", ".parent.owner.login + \"/\" + .parent.name",
        ]).strip()
    except CollectError:
        return repo
    return parent if parent and parent != "/" else repo
