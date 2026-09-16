"""Agent manifests — how a fork announces itself, and the only file that
has to exist for one to be found.

v1.39. An agent is a directory under ``orchestrator_2/harnesses/`` that
contains an ``agent.json``. That is the whole contract. Nothing else in
the tree needs to change for a fork to be routable, selectable in the
New Game menu, runnable by the scenario suite, and collectable into the
end-of-day league.

**Why this exists.** Registration used to mean inserting two lines into
``binding_registry.py``. That works fine for one fork and fails badly
for forty: every team edits the same two lines, so every team's push
conflicts with every other team's push, and collecting the room's agents
at the end of the day becomes forty manual merges. Worse, the conflicts
land in a shared file, so one bad resolution can unregister somebody
else's agent hours after they last touched it.

Discovery moves the declaration inside the fork. An agent's entire
footprint becomes one self-contained directory, which means:

* two teams can never collide, because they never write the same file;
* ``soc push`` can enforce "your folder and nothing else" as a real
  check rather than an instruction people are asked to follow;
* the league entrant list is just the set of directories with a
  manifest, so nobody can be left out by a merge going wrong.

**Why JSON and not TOML.** ``tomllib`` is stdlib from 3.11 and a room of
laptops will not all be on 3.11. JSON parses everywhere, and — since
most attendees will be editing this through a coding agent rather than
by hand — it is the format an LLM is least likely to get subtly wrong.
The cost is no comments, which is why ``TEMPLATE`` below is written out
in full for people to copy.

**On being strict.** Every failure here is raised with the file path and
the fix in the message, because the reader is often an agent with no
other context. A manifest that cannot be parsed is skipped rather than
fatal: one team's typo must never stop the rest of the room's server
from starting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

MANIFEST_NAME = "agent.json"

# Same rule as ``scripts/new_agent.py`` applies to ``--team``/``--name``:
# the parts become a package directory, a dict key and an env-var
# namespace, so they are held to lowercase ascii.
_PART_RE = re.compile(r"^[a-z][a-z0-9_]*$")

TEMPLATE = """\
{
  "team": "%(team)s",
  "name": "%(name)s",
  "participants": %(participants)s,
  "menu_label": "%(menu_label)s",
  "entry": "harness:run",
  "needs_llm": true
}
"""


class ManifestError(ValueError):
    """A manifest exists but cannot be used.

    The message always names the file and the fix — this is read by
    people and agents who did not write the file and cannot see the
    schema.
    """


@dataclass(frozen=True)
class AgentManifest:
    """One fork's declaration of itself."""

    team: str
    name: str
    menu_label: Optional[str]
    entry: str
    needs_llm: bool
    directory: Path
    # Who actually built it. Required (v1.41) because the league table is
    # the day's public record and a row reading `redwatch_reaper` names
    # nobody — there is no way to hand out a prize, chase a broken
    # entrant, or tell two forks of the same idea apart. Free text on
    # purpose: real names, handles and nicknames are all fine, this is a
    # roster and not an identity system.
    participants: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        """``<team>_<name>`` — the id used everywhere else.

        This is the agent's identity in the New Game menu, in the audit
        trail, in suite results and on the league table. It is derived
        rather than stored so a manifest cannot disagree with itself.
        """
        return f"{self.team}_{self.name}"

    @property
    def const(self) -> str:
        """The ``SOC_*`` key under which the binding is also filed."""
        return f"SOC_{self.label.upper()}"

    @property
    def locator(self) -> str:
        """Fully-qualified ``module:callable`` for the dispatcher."""
        mod, _, func = self.entry.partition(":")
        pkg = self.directory.name
        return (
            f"sea_of_colours.orchestrator_2.harnesses.{pkg}.{mod}:{func}"
        )


def _require_str(data: dict, key: str, path: Path) -> str:
    val = data.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ManifestError(
            f"{path}: missing required string field {key!r}. "
            f"Add it, e.g. \"{key}\": \"...\". "
            f"See {path.parent / MANIFEST_NAME} against the template in "
            f"sea_of_colours/orchestrator_2/agent_manifest.py."
        )
    return val.strip()


def _require_part(value: str, key: str, path: Path) -> str:
    part = value.strip().lower()
    if not _PART_RE.match(part) or part.endswith("_"):
        raise ManifestError(
            f"{path}: {key!r} must be lowercase letters, digits and "
            f"underscores, start with a letter and not end with one "
            f"(got {value!r}). This becomes a package name and a menu id, "
            f"which is why it is restricted."
        )
    return part


def _require_participants(data: dict, path: Path) -> tuple[str, ...]:
    """Who built this. A list of at least one non-empty name.

    Enforced here so it cannot be skipped by hand-writing a manifest,
    but the gate people actually meet is ``soc new`` (which will not
    mint without it) and ``soc push`` (which will not publish without
    it). By the time discovery runs, a fork that used the tools has it.
    """
    raw = data.get("participants")
    if raw is None:
        raise ManifestError(
            f"{path}: missing required field 'participants'. Add everyone "
            f"who worked on this agent, e.g. "
            f"\"participants\": [\"Ada Lovelace\", \"grace\"]. "
            f"The league table is the day's public record and a row that "
            f"names nobody cannot be credited or chased."
        )
    if isinstance(raw, str):
        raise ManifestError(
            f"{path}: 'participants' must be a LIST of names, not a single "
            f"string (got {raw!r}). Use [\"{raw}\"] even for one person — "
            f"a list stays right when the second person joins."
        )
    if not isinstance(raw, list):
        raise ManifestError(
            f"{path}: 'participants' must be a list of names, got "
            f"{type(raw).__name__}."
        )
    people = tuple(
        p.strip() for p in raw if isinstance(p, str) and p.strip()
    )
    if not people:
        raise ManifestError(
            f"{path}: 'participants' is empty. Name at least one person — "
            f"this is how the league credits the work."
        )
    if len(people) != len(raw):
        raise ManifestError(
            f"{path}: every entry in 'participants' must be a non-empty "
            f"string (got {raw!r})."
        )
    return people


def load(path: Path) -> AgentManifest:
    """Read one ``agent.json``. Raises :class:`ManifestError` if unusable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"{path}: cannot be read ({exc}).") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(
            f"{path}: is not valid JSON ({exc}). "
            f"A trailing comma after the last field is the usual cause."
        ) from exc

    if not isinstance(data, dict):
        raise ManifestError(
            f"{path}: must contain a JSON object, got {type(data).__name__}."
        )

    team = _require_part(_require_str(data, "team", path), "team", path)
    name = _require_part(_require_str(data, "name", path), "name", path)

    # The directory name is the import path, so a manifest that claims a
    # different identity than the folder it sits in would register a
    # binding pointing at a package that does not exist. Caught here
    # rather than at dispatch, where it would surface as a seat silently
    # falling back to the heuristic mid-game.
    expected = f"{team}_{name}"
    if path.parent.name != expected:
        raise ManifestError(
            f"{path}: declares team={team!r} name={name!r}, which means the "
            f"directory must be named {expected!r}, but it is "
            f"{path.parent.name!r}. Rename the directory to {expected!r}, or "
            f"change the manifest to match the directory."
        )

    entry = data.get("entry") or "harness:run"
    if not isinstance(entry, str) or ":" not in entry:
        raise ManifestError(
            f"{path}: 'entry' must look like 'module:callable' "
            f"(got {entry!r}). The default is 'harness:run'."
        )

    menu_label = data.get("menu_label")
    if menu_label is not None and not isinstance(menu_label, str):
        raise ManifestError(
            f"{path}: 'menu_label' must be a string or omitted "
            f"(got {type(menu_label).__name__})."
        )

    return AgentManifest(
        team=team,
        name=name,
        menu_label=(menu_label or "").strip() or None,
        entry=entry.strip(),
        needs_llm=bool(data.get("needs_llm", True)),
        directory=path.parent,
        participants=_require_participants(data, path),
    )


def harness_root() -> Path:
    """Where forks live. One place, so nothing has to guess."""
    return Path(__file__).resolve().parent / "harnesses"


def iter_manifest_paths(root: Optional[Path] = None) -> Iterator[Path]:
    """Every ``agent.json`` under the harness root, in a stable order.

    Sorted so the New Game dropdown and the league table do not reshuffle
    between runs on filesystem ordering alone.
    """
    base = root or harness_root()
    if not base.is_dir():
        return
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        candidate = child / MANIFEST_NAME
        if candidate.is_file():
            yield candidate


def discover(
    root: Optional[Path] = None,
) -> tuple[list[AgentManifest], list[str]]:
    """Find every declared agent.

    Returns ``(manifests, problems)``. A broken manifest becomes a
    problem string and is skipped, never an exception — at a hackathon
    one team's typo must not stop anybody else's server from booting.
    Callers that care (``soc doctor``, the league collator) print the
    problems; callers that do not (the registry) can ignore them and
    still come up.
    """
    found: list[AgentManifest] = []
    problems: list[str] = []
    seen: dict[str, Path] = {}

    for path in iter_manifest_paths(root):
        try:
            manifest = load(path)
        except ManifestError as exc:
            problems.append(str(exc))
            continue
        prior = seen.get(manifest.label)
        if prior is not None:
            problems.append(
                f"{path}: duplicate agent {manifest.label!r}, already "
                f"declared by {prior}. Two agents cannot share a label — "
                f"rename one with a different --team or --name."
            )
            continue
        seen[manifest.label] = path
        found.append(manifest)

    return found, problems
