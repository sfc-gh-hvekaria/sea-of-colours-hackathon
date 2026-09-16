"""Who can be cast into a seat — discovered, never hand-listed.

The lab exists to show a fork diverging from what it forked, so the
roster has to grow the moment somebody mints an agent. It is a
directory scan for exactly that reason (see ``agent_manifest``):
registering a fork edits nothing shared, which is what lets a room full
of teams work in one repo without queueing behind each other.

Two properties matter to a caller and are surfaced here rather than
rediscovered:

``needs_llm`` — a heuristic seat is free and instant; an LLM seat costs
a model call and about twenty seconds. A lab that let you fill four
seats with LLM agents without saying so would be the slow tool this one
was built to replace, so the cost is on the roster where a UI can show
it before you press Play.

``baseline`` — stock V12 is the thing forks are measured against, so it
is flagged rather than left for the caller to string-match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

#: The agent every fork is measured against.
BASELINE = "tabula_v12"


@dataclass(frozen=True)
class Castable:
    """One agent that can take a seat."""

    label: str
    display: str
    needs_llm: bool
    baseline: bool = False
    team: str = ""
    participants: tuple[str, ...] = ()
    problem: str = ""

    @property
    def is_fork(self) -> bool:
        return bool(self.team) and not self.baseline


def roster(root: Optional[object] = None) -> tuple[list[Castable], list[str]]:
    """Every agent available to cast, plus any broken manifests.

    Built on ``binding_registry.selectable_agents``, which is already
    the union of the shipped bindings and the discovered forks — the
    same list the New Game modal shows. Rebuilding that union here
    would mean two places to fix when a fork does not appear, and the
    shipped V12 is not manifest-declared, so a manifest-only scan
    silently loses the baseline.

    Problems are returned rather than raised, matching ``discover``: at
    a hackathon one team's typo must not stop anybody else casting.
    """
    from sea_of_colours.orchestrator_2 import agent_manifest, binding_registry

    manifests, problems = agent_manifest.discover(root)  # type: ignore[arg-type]
    by_label = {m.label: m for m in manifests}

    out: list[Castable] = []
    seen: set[str] = set()
    for entry in binding_registry.selectable_agents():
        label = str(entry.get("value") or "")
        # A human cannot take a lab turn: the whole board is planned
        # headlessly and resolved in one shot, with no seat to pilot.
        if not label or entry.get("kind") == "human":
            continue
        seen.add(label)
        manifest = by_label.get(label)
        out.append(
            Castable(
                label=label,
                display=str(entry.get("label") or label),
                needs_llm=bool(entry.get("needs_llm")),
                baseline=(label == BASELINE),
                team=manifest.team if manifest else "",
                participants=(
                    tuple(manifest.participants) if manifest else ()
                ),
            )
        )

    if BASELINE not in seen:
        # The baseline is not optional: without it there is nothing to
        # diverge FROM, and a lab that quietly dropped it would still
        # run and still look fine.
        problems.append(
            f"the baseline agent {BASELINE!r} is not selectable — every "
            f"lab comparison is against it, so check its binding in "
            f"binding_registry.py"
        )

    out.sort(key=lambda c: (not c.baseline, c.needs_llm, c.label))
    return out, problems


def get(label: str) -> Castable:
    entries, _ = roster()
    for entry in entries:
        if entry.label == label:
            return entry
    raise KeyError(
        f"no castable agent {label!r}; available: "
        f"{', '.join(e.label for e in entries)}"
    )


def llm_seats(cast: dict[str, str]) -> list[str]:
    """Seats in ``cast`` that will cost a model call.

    What a UI needs to warn with before Play, and what the cache is
    worth avoiding.
    """
    entries = {e.label: e for e in roster()[0]}
    return [
        seat for seat, agent in sorted(cast.items())
        if entries.get(agent) and entries[agent].needs_llm
    ]
