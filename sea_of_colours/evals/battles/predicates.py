"""Scoring a night — shape predicates over what the agent actually played.

Every check here reads the compiled moves and the engine's own terrain,
and asks whether the play has the right SHAPE. None of them compare
against a stored answer, and that is deliberate: an agent that finds a
better line than the canonical should pass, and an agent that reproduces
the canonical's cell list by luck while getting the ordering wrong
should not.

This is why the boards do not ship an expected move list. A fixed list
would score imitation, and imitation of a nine-board corpus is exactly
the overfitting the difficulty ladder exists to detect.

**Every failure message names the play, not the rule.** These are read
by people debugging at speed and by agents with no other context, so
"stepped onto (17,7), which a rival stripped last night — that is
synthetic green and costs 100" beats "no_green violated". The message is
the product; the boolean is almost incidental.

Adding a predicate: write the function, add it to :data:`CHECKS`, and
use its name as a key in a board's ``expect``. The signature is fixed so
the runner can call them uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from sea_of_colours.game.session import Tile

XY = tuple[int, int]


@dataclass(frozen=True)
class Check:
    """One predicate's verdict on one turn."""

    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        return f"{'PASS' if self.passed else 'FAIL'}  {self.name}: {self.detail}"


@dataclass(frozen=True)
class Play:
    """The turn, pre-chewed into the shapes the predicates keep needing.

    Built once per run rather than per predicate: fourteen checks each
    re-deriving the harvester paths off a raw move list was both slower
    and, more importantly, fourteen places for the parsing to disagree
    with itself.
    """

    moves: Sequence[Mapping[str, Any]]
    session: Any

    @property
    def drops(self) -> list[tuple[str, XY]]:
        out = []
        for m in self.moves:
            if m.get("a") == "drop":
                cell = _cell(m, "at") or _cell(m, "to")
                if cell:
                    out.append((str(m.get("unit") or "?"), cell))
        return out

    @property
    def probes(self) -> list[XY]:
        return [c for m in self.moves if m.get("a") == "probe"
                and (c := _cell(m, "at"))]

    @property
    def weapons(self) -> list[tuple[str, XY | None]]:
        return [(str(m.get("a")), _cell(m, "at"))
                for m in self.moves
                if m.get("a") in ("emp_launch", "chaff_flare")]

    def paths(self) -> dict[str, list[XY]]:
        """Per-unit cell sequence, landing cell first.

        The landing cell belongs in the path because a drop
        auto-harvests the cell it lands on — several of these boards
        turn entirely on that, and a path that started at the first
        STEP would score a pure-on-landing as never having been taken.
        """
        out: dict[str, list[XY]] = {}
        for m in self.moves:
            act = m.get("a")
            if act not in ("drop", "step"):
                continue
            unit = str(m.get("unit") or "?")
            cell = _cell(m, "at") or _cell(m, "to")
            if cell:
                out.setdefault(unit, []).append(cell)
        return out

    def tile(self, cell: XY) -> tuple[int, int]:
        """``(tile, purity)`` from engine truth, not from the agent's view."""
        x, y = cell
        s = self.session
        if not (0 <= x < s.width and 0 <= y < s.height):
            return (int(Tile.EMPTY), 0)
        c = s.grid[y][x]
        return (int(c.tile), int(c.purity))

    def is_pure(self, cell: XY) -> bool:
        t, p = self.tile(cell)
        return t == int(Tile.RED) and p >= 255

    def is_green(self, cell: XY) -> bool:
        """Synthetic green only — the stuff a harvest left behind.

        Natural green is ordinary terrain and banks normally, so walking
        it is not a mistake and must not be scored as one. Synthetic
        green is what RED becomes once somebody has stripped it: it
        looks identical on the board, banks nothing, and costs 100 per
        parcel at settlement. Only the ledger's lineage can tell them
        apart, which is precisely what makes it a trap worth testing.
        """
        if self.tile(cell)[0] != int(Tile.GREEN):
            return False
        ledger = getattr(self.session, "ledger", None)
        if ledger is None:
            return False
        try:
            row = ledger.record(int(cell[0]), int(cell[1])) or {}
        except Exception:
            return False
        return str(row.get("lineage") or "natural") == "synthetic"


def _cell(m: Mapping[str, Any], key: str) -> XY | None:
    v = m.get(key)
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        try:
            return (int(v[0]), int(v[1]))
        except (TypeError, ValueError):
            return None
    return None


def _fmt(cells) -> str:
    return ", ".join(f"({x},{y})" for x, y in cells) or "nothing"


# ── the checks ──────────────────────────────────────────────────────
#
# Signature is ``(play, board, want) -> Check``. ``want`` is whatever the
# board's ``expect`` mapped to the key, so a check can be a flag
# (``True``) or carry a number (``max_steps_past_pure: 1``).


def take_pure(play: Play, board, want) -> Check:
    """Did any unit's path touch a pure at all?

    The floor. On these boards a night that never reaches the jackpot
    has misunderstood the assignment, whatever else it banked.
    """
    hit = [c for path in play.paths().values() for c in path if play.is_pure(c)]
    if hit:
        return Check("take_pure", True, f"reached the pure at {_fmt(set(hit))}")
    reached = [c for path in play.paths().values() for c in path]
    return Check(
        "take_pure", False,
        f"no unit reached a pure. Pures are at {_fmt(board.pures)}; the "
        f"harvesters went to {_fmt(reached)}",
    )


def pure_first(play: Play, board, want) -> Check:
    """Was the pure banked on the LANDING, not walked to?

    Landing on a pure auto-harvests it, so it is in the hold at hour
    one and nothing that happens later can take it away. Walking to it
    means every hour in between is an hour a rival can land there
    first, or chaff can cut the chain before it arrives. The cells are
    the same; the ordering is the whole play.

    Judged per unit, not per turn. On a two-pure board it is not enough
    that SOMETHING landed on a jackpot — a unit that walks to the other
    one has conceded that race, and a whole-turn check would let it hide
    behind its partner. Reaching further pures later in the same walk is
    fine: what matters is that the landing itself banked one.
    """
    paths = play.paths()
    engaged = {u: p for u, p in paths.items() if any(play.is_pure(c) for c in p)}
    if not engaged:
        return Check("pure_first", False, "never reached a pure at all")

    walked = {u: p for u, p in engaged.items() if not play.is_pure(p[0])}
    if not walked:
        return Check(
            "pure_first", True,
            f"landed directly on the pure at "
            f"{_fmt([p[0] for p in engaged.values()])}",
        )
    unit, path = sorted(walked.items())[0]
    step = next(i for i, c in enumerate(path) if play.is_pure(c))
    return Check(
        "pure_first", False,
        f"{unit} reached the pure at step {step} instead of landing on it. "
        f"Every step before it is an hour a rival can take the cell first, "
        f"or chaff can cut the chain short of it",
    )


def no_green(play: Play, board, want) -> Check:
    """Did anything walk over ground a rival already stripped?

    Synthetic green is what RED leaves behind once harvested. It looks
    like terrain and costs 100 per parcel at settlement, and stale
    memory will still advertise the cell at its old value — which is
    exactly the trap these boards set.
    """
    bad = [(u, c) for u, path in play.paths().items()
           for c in path if play.is_green(c)]
    if not bad:
        return Check("no_green", True, "no unit touched synthetic green")
    where = ", ".join(f"{u} at ({x},{y})" for u, (x, y) in bad)
    return Check(
        "no_green", False,
        f"stepped on ground a rival already stripped: {where}. That is "
        f"synthetic green — it scores nothing and costs 100 per parcel",
    )


def no_wake_reentry(play: Play, board, want) -> Check:
    """Did a second unit walk through the first unit's wake?

    Runs are sequenced, not simultaneous, so the second wave arrives to
    find cells the first already stripped. It reads as a legal walk over
    known-good red and it pays -100 a cell. The fix is always the same:
    send the second unit OUTWARD.
    """
    paths = play.paths()
    seen: dict[XY, str] = {}
    clashes: list[str] = []
    for unit in sorted(paths):
        for cell in paths[unit]:
            prior = seen.get(cell)
            if prior is not None and prior != unit:
                clashes.append(f"({cell[0]},{cell[1]}) after {prior}")
            seen.setdefault(cell, unit)
    if not clashes:
        return Check("no_wake_reentry", True,
                     "no unit re-entered another's wake")
    return Check(
        "no_wake_reentry", False,
        f"a later unit walked cells an earlier one already stripped: "
        f"{'; '.join(clashes)}. Those are synthetic green by the time it "
        f"arrives — route the second unit outward instead",
    )


def no_repeat_pure(play: Play, board, want) -> Check:
    """Did a second wave land on the pure the first already took?

    Only worth insuring against when the opposition holds chaff, since
    chaff is the one thing that can deny an hour-one landing. Without
    it the second pass insures against nothing and simply pays green.
    """
    counts: dict[XY, int] = {}
    for path in play.paths().values():
        for cell in set(path):
            if play.is_pure(cell) or cell in set(map(tuple, board.pures)):
                counts[cell] = counts.get(cell, 0) + 1
    repeats = [c for c, n in counts.items() if n > 1]
    if not repeats:
        return Check("no_repeat_pure", True, "the pure was taken once")
    return Check(
        "no_repeat_pure", False,
        f"two waves went through {_fmt(repeats)}. The first stripped it, so "
        f"the second lands on synthetic green. A repeat only insures against "
        f"chaff, and there is none in play here",
    )


def deploy_all(play: Play, board, want) -> Check:
    """Did every harvester fly?

    An idle harvester on a redsign night is a wasted unit, and it is the
    most common way a good plan still loses.
    """
    flown = {u for u, _ in play.drops}
    if len(flown) >= board.harvesters:
        return Check("deploy_all", True,
                     f"all {board.harvesters} harvester(s) deployed")
    return Check(
        "deploy_all", False,
        f"only {len(flown)} of {board.harvesters} harvesters were deployed. "
        f"An idle unit on a jackpot night banks nothing and denies nothing",
    )


def max_steps_past_pure(play: Play, board, want) -> Check:
    """Did it wander after banking the jackpot?

    On a contested board every extra hour is exposure bought with a
    pure already in the hold.
    """
    limit = int(want)
    worst, worst_unit = 0, ""
    for unit, path in play.paths().items():
        idx = [i for i, c in enumerate(path) if play.is_pure(c)]
        if not idx:
            continue
        tail = len(path) - 1 - idx[-1]
        if tail > worst:
            worst, worst_unit = tail, unit
    if worst <= limit:
        return Check("max_steps_past_pure", True,
                     f"stopped {worst} step(s) past the pure (limit {limit})")
    return Check(
        "max_steps_past_pure", False,
        f"{worst_unit} took {worst} steps after banking the pure, limit is "
        f"{limit}. On this board those hours are exposure paid for with a "
        f"jackpot already in the hold",
    )


def min_steps_past_pure(play: Play, board, want) -> Check:
    """Did it stop short on a board where the extension was free?"""
    need = int(want)
    best = 0
    for path in play.paths().values():
        idx = [i for i, c in enumerate(path) if play.is_pure(c)]
        if idx:
            best = max(best, len(path) - 1 - idx[-1])
    if best >= need:
        return Check("min_steps_past_pure", True,
                     f"continued {best} step(s) past the pure (wanted {need})")
    return Check(
        "min_steps_past_pure", False,
        f"stopped {best} step(s) past the pure and wanted at least {need}. "
        f"Nothing on this board can punish the extra hours, so the "
        f"extension is nearly free and stopping short is the failure",
    )


def max_probes(play: Play, board, want) -> Check:
    limit = int(want)
    n = len(play.probes)
    if n <= limit:
        return Check("max_probes", True, f"spent {n} probe(s) (limit {limit})")
    return Check("max_probes", False,
                 f"spent {n} probes with only {limit} available or wanted")


def denial_probe(play: Play, board, want) -> Check:
    """Was a probe put ON a rival's eye?

    Superseding a finder is the premise of a blind attack, not a bonus:
    the harvester is landing in the quadrant the blind opens.
    """
    eyes = {tuple(c) for c in board.rival_probes}
    hits = [p for p in play.probes if p in eyes]
    if hits:
        return Check("denial_probe", True,
                     f"blinded the rival eye at {_fmt(hits)}")
    return Check(
        "denial_probe", False,
        f"no probe landed on a rival eye. Their finders are at "
        f"{_fmt(eyes)}; probes went to {_fmt(play.probes)}. Blinding the "
        f"finder is the premise of this attack",
    )


def comb_gradient(play: Play, board, want) -> Check:
    """On a blind attack, did the walk ride the beacon inward?

    The pure is invisible here, so there is no cell to name. What can be
    scored is whether the comb moves up the smear gradient — toward the
    centre of the beacon — rather than away from it, which is the exact
    error this board caught in the baseline.
    """
    if board.redsign_center is None:
        return Check("comb_gradient", True, "no beacon on this board")
    cx, cy = board.redsign_center

    def dist(c: XY) -> float:
        return max(abs(c[0] - cx), abs(c[1] - cy))

    best_unit, best_gain = "", None
    for unit, path in play.paths().items():
        if len(path) < 2:
            continue
        gain = dist(path[0]) - min(dist(c) for c in path)
        if best_gain is None or gain > best_gain:
            best_unit, best_gain = unit, gain
    if best_gain is None:
        return Check("comb_gradient", False,
                     "no harvester walked, so nothing combed the beacon")
    if best_gain >= 0 and min(
        dist(c) for path in play.paths().values() for c in path
    ) <= 2:
        return Check("comb_gradient", True,
                     f"{best_unit} combed inward to within "
                     f"{min(dist(c) for c in play.paths()[best_unit]):.0f} "
                     f"of the beacon centre")
    return Check(
        "comb_gradient", False,
        f"the walk moved away from the beacon at ({cx},{cy}) rather than "
        f"into it. The pure is not visible, so the beacon gradient is the "
        f"only information there is — comb up it",
    )


def min_red_cells(play: Play, board, want) -> Check:
    """Did the night actually collect RED, on a board with no jackpot?

    Every other predicate here anchors on a pure or a beacon, because
    every other board has one. A plain working night has neither, and
    without this it would score full marks for landing two harvesters on
    empty ground and lifting them again. What "good" means on such a
    board is unglamorous and countable: put the units where the ore is
    and walk enough cells to pay for the outing.
    """
    need = int(want)
    cells = {c for path in play.paths().values() for c in path}
    red = {c for c in cells
           if play.tile(c)[0] == int(Tile.RED) and not play.is_green(c)}
    if len(red) >= need:
        return Check("min_red_cells", True,
                     f"walked {len(red)} RED cell(s), wanted {need}")
    return Check(
        "min_red_cells", False,
        f"only {len(red)} RED cell(s) walked, wanted {need}. There is no "
        f"pure on this board and nothing to contest — the whole night is "
        f"the seam, so a short outing is not caution, it is just less ore",
    )


def seam_commitment(play: Play, board, want) -> Check:
    """How many units actually went to the seam?

    The check for nerve. When a rival holds hour one the temptation is
    to take safe cells elsewhere; conceding costs the same as trying and
    wins nothing.
    """
    need = int(want)
    anchor = board.redsign_center or (board.pures[0] if board.pures else None)
    if anchor is None:
        return Check("seam_commitment", True, "no seam on this board")
    cx, cy = anchor
    on_seam = {
        u for u, path in play.paths().items()
        if any(max(abs(x - cx), abs(y - cy)) <= 3 for x, y in path)
    }
    if len(on_seam) >= need:
        return Check("seam_commitment", True,
                     f"{len(on_seam)} unit(s) committed to the seam")
    return Check(
        "seam_commitment", False,
        f"only {len(on_seam)} of a wanted {need} units went to the seam at "
        f"({cx},{cy}). Tempo sizes the commitment; it does not cancel it",
    )


def probes_after_harvesters(play: Play, board, want) -> Check:
    """Were the harvesters away before probes were spent?

    On a tempo-critical night an hour spent probing ahead of a run is an
    hour the rival spends landing on your jackpot.

    One exception, and it is not a loophole: when the pure is an ECHO it
    is not drop-legal, so something has to light it before anything can
    land. That hot-drop probe is part of the grab rather than a
    distraction from it, so exactly one probe may precede the first
    drop on a fogged board. A second one is back to costing tempo.
    """
    probe_idx = [i for i, m in enumerate(play.moves) if m.get("a") == "probe"]
    drops = [i for i, m in enumerate(play.moves) if m.get("a") == "drop"]
    if not probe_idx or not drops:
        return Check("probes_after_harvesters", True, "nothing to order")

    allowance = 1 if board.pure_is_fogged else 0
    early = [i for i in probe_idx if i < drops[-1]]
    if len(early) <= allowance:
        if early and allowance:
            return Check(
                "probes_after_harvesters", True,
                "one probe went in ahead of the drops to light the echo "
                "pure, the rest waited for the harvesters",
            )
        return Check("probes_after_harvesters", True,
                     "harvesters were away before probes were spent")
    return Check(
        "probes_after_harvesters", False,
        f"{len(early)} probes were queued ahead of the last harvester drop "
        f"and only {allowance} is justified here. On a tempo-critical night "
        f"the rest are hours handed to whoever else wants the pure",
    )


def compiler_clean(play: Play, board, want) -> Check:
    """Did the plan survive packaging without being rewritten?

    A plan the packager had to repair is a plan the agent could not
    express. That is worth knowing even when the repaired version scores
    well, because the agent did not choose what got played.
    """
    n = len(play.moves)
    if n == 0:
        return Check("compiler_clean", False,
                     "no moves reached the engine at all")
    return Check("compiler_clean", True, f"{n} moves reached the engine")


CHECKS: Mapping[str, Callable[..., Check]] = {
    "take_pure": take_pure,
    "pure_first": pure_first,
    "no_green": no_green,
    "no_wake_reentry": no_wake_reentry,
    "no_repeat_pure": no_repeat_pure,
    "deploy_all": deploy_all,
    "max_steps_past_pure": max_steps_past_pure,
    "min_steps_past_pure": min_steps_past_pure,
    "max_probes": max_probes,
    "denial_probe": denial_probe,
    "comb_gradient": comb_gradient,
    "min_red_cells": min_red_cells,
    "seam_commitment": seam_commitment,
    "probes_after_harvesters": probes_after_harvesters,
    "compiler_clean": compiler_clean,
}

# Keys in a board's ``expect`` that are documentation rather than
# checks. Listed explicitly so an unknown key is a loud typo instead of
# a predicate that silently never runs — the failure mode where a board
# looks strict and tests nothing.
NON_CHECK_KEYS = frozenset({"note"})


def evaluate(play: Play, board) -> list[Check]:
    """Run every predicate the board asks for."""
    out: list[Check] = []
    for key, want in board.expect.items():
        if key in NON_CHECK_KEYS:
            continue
        fn = CHECKS.get(key)
        if fn is None:
            out.append(Check(
                key, False,
                f"unknown predicate {key!r} in board {board.id!r}. Known: "
                + ", ".join(sorted(CHECKS)),
            ))
            continue
        try:
            out.append(fn(play, board, want))
        except Exception as exc:  # a broken check must not kill the run
            out.append(Check(key, False, f"check raised {type(exc).__name__}: {exc}"))
    return out


def weapon_usage(play: Play) -> dict[str, int]:
    """What ordnance was actually fired.

    Not a pass/fail. It answers the loadout question — whether handing
    the agent a full rack changes anything at all — and an agent that
    fires nothing at ``siege`` is telling you something even when every
    predicate passes.
    """
    used = {"emp_launch": 0, "chaff_flare": 0}
    for act, _ in play.weapons:
        used[act] = used.get(act, 0) + 1
    return used
