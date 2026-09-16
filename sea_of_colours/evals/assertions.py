"""Declarative pass/fail criteria for eval scenarios.

Each assertion is a small dataclass with one method::

    evaluate(policy, *, context) -> AssertionResult

``policy`` is the wire-format move list extracted from
``store.list_policies(session_id, day)[player]`` after
:func:`sea_of_colours.agent.runtime.run_agent_turn` returned. Each move
is a dict with ``a`` (action) plus ``at`` / ``to`` / ``unit`` per the
SOC move grammar (RULEBOOK §3.10).

``context`` carries the constructed session, the world state at the
moment of evaluation, and a helper for resolving purity at a cell.
Assertions read whichever fields they need.

Why declarative not assertion-style: each scenario's pass/fail summary
should be aggregable into a markdown report ("3/3 passed",
"2/3 — MinExpectedValue 220 observed 110"). Raising ``AssertionError``
would terminate evaluation at the first failure and lose the rest.

Future assertions slot in by subclassing :class:`Assertion` and adding
their own ``evaluate``. The eval CLI auto-discovers them through the
:data:`SCENARIOS` registry — no central enumeration needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sea_of_colours.game.session import GameSession
from sea_of_colours.generator import Tile


XY = Tuple[int, int]


@dataclass
class AssertionContext:
    """Snapshot of "everything an assertion might want to introspect".

    Constructed by :func:`sea_of_colours.evals.runner.run_scenario`
    after the agent turn lands. We keep this thin — assertions don't
    need to traverse all of Snowflake; they're checking a single
    submitted policy against a known world state.
    """
    session: GameSession
    player: str
    day_at_run: int

    # -- v1.9 (pilot-comprehension surface) -------------------------
    # Agent text + planning telemetry captured by the runner. Optional
    # so pre-existing scenarios keep passing their AssertionContext
    # without the runner having to fill these in.
    #
    # * ``rationale`` — full concatenated rationale text emitted by the
    #   agent (strategist + tactician if two-phase). Used by
    #   :class:`RationaleMentions` / :class:`RationaleDoesNotMention`
    #   to check the reasoning is grounded in observable game state.
    # * ``plan_label`` — the compact plan token the strategist chose
    #   (e.g. ``"harvest_pure"``, ``"chase_redsign"``, ``"fleet_rebuild"``).
    # * ``selection_count`` — how many candidates the tactician
    #   *selected* from the compiled menu.
    # * ``materialized_count`` — how many of those actually landed as
    #   moves in the submitted policy queue. A gap (``selection_count >
    #   materialized_count``) is a silent materialisation bug.
    rationale: str = ""
    plan_label: str = ""
    selection_count: int = 0
    materialized_count: int = 0

    def cell_purity(self, x: int, y: int) -> int:
        """RED purity at (x, y), 0 for non-RED or out-of-bounds."""
        if not (0 <= x < self.session.width and 0 <= y < self.session.height):
            return 0
        cell = self.session.grid[y][x]
        if cell.tile != Tile.RED:
            return 0
        return int(cell.purity or 0)

    def cell_tile(self, x: int, y: int) -> str:
        """Tile name at (x, y), 'OOB' if out of bounds."""
        if not (0 <= x < self.session.width and 0 <= y < self.session.height):
            return "OOB"
        return self.session.grid[y][x].tile.name

    def is_synthetic_green(self, x: int, y: int) -> bool:
        """True if ``(x, y)`` is a synthetic-green cell (already harvested)."""
        return f"{x}:{y}" in self.session.track_harvests.get("p1", set())


@dataclass
class AssertionResult:
    """Outcome of a single assertion. Aggregated into a scenario report."""
    name: str
    passed: bool
    detail: str
    expected: str = ""
    observed: str = ""


@dataclass
class Assertion:
    """Base class. Subclasses override :meth:`evaluate` and set ``name``."""
    name: str = "assertion"

    def evaluate(
        self,
        policy: Sequence[Dict[str, Any]],
        *,
        context: AssertionContext,
    ) -> AssertionResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def describe(self) -> str:
        """Human-readable one-liner of the pass condition.

        Surfaced in the eval command center headline so a reader can
        see what a scenario is testing without running it. The default
        falls back to the assertion's class name; subclasses override
        when they can render their configured parameters concisely.
        """
        return self.name


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _xy_of(move: Dict[str, Any]) -> Optional[XY]:
    """Extract the (x, y) coordinate from a move, or ``None`` if absent."""
    for key in ("at", "to"):
        xy = move.get(key)
        if isinstance(xy, (list, tuple)) and len(xy) == 2:
            try:
                return (int(xy[0]), int(xy[1]))
            except (TypeError, ValueError):
                return None
    return None


def _harvester_path(policy: Sequence[Dict[str, Any]]) -> List[XY]:
    """Reconstruct the harvester's visit sequence from drop+step moves.

    Returns the ordered list of cells the harvester *would* land on if
    the policy executed as submitted (ignoring engine-level rejections
    — that's what the engine tests are for). Includes the drop cell
    and every step destination. Pickups don't move the harvester so
    they don't extend the list.
    """
    path: List[XY] = []
    for move in policy:
        if move.get("a") not in ("drop", "step"):
            continue
        xy = _xy_of(move)
        if xy is not None:
            path.append(xy)
    return path


# ---------------------------------------------------------------------------
# Concrete assertions
# ---------------------------------------------------------------------------
@dataclass
class ProbeCount(Assertion):
    """Assert N ≤ #probes in policy ≤ M."""
    min: int = 0
    max: int = 2
    name: str = "ProbeCount"

    def describe(self) -> str:
        return f"{self.min} ≤ probes ≤ {self.max}"

    def evaluate(self, policy, *, context):
        n = sum(1 for m in policy if m.get("a") == "probe")
        ok = self.min <= n <= self.max
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=f"probes={n}, want {self.min}..{self.max}",
            expected=f"{self.min}..{self.max}",
            observed=str(n),
        )


@dataclass
class MustAvoid(Assertion):
    """Assert the harvester chain does NOT touch any of ``cells``.

    Used for §3.17 collision-avoidance (enemy harvester present) and
    §3.16 probe-on-probe risk. ``cells`` is checked against drop+step
    destinations and (separately) probe placements.
    """
    cells: Sequence[XY] = ()
    reason: str = ""
    check_probes: bool = True
    check_harvester: bool = True
    name: str = "MustAvoid"

    def describe(self) -> str:
        cells = sorted({(int(x), int(y)) for x, y in self.cells})
        which = []
        if self.check_harvester:
            which.append("drop/step")
        if self.check_probes:
            which.append("probe")
        kinds = "/".join(which) or "any action"
        suffix = f" — {self.reason}" if self.reason else ""
        return f"no {kinds} into {cells}{suffix}"

    def evaluate(self, policy, *, context):
        forbidden: Set[XY] = {(int(x), int(y)) for x, y in self.cells}
        touched: List[XY] = []
        for move in policy:
            xy = _xy_of(move)
            if xy is None:
                continue
            if move.get("a") == "probe" and not self.check_probes:
                continue
            if move.get("a") in ("drop", "step") and not self.check_harvester:
                continue
            if xy in forbidden:
                touched.append(xy)
        ok = not touched
        reason = f" ({self.reason})" if self.reason else ""
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"all clear{reason}"
                if ok
                else f"forbidden cells touched: {touched}{reason}"
            ),
            expected=f"no moves into {sorted(forbidden)}",
            observed=str(touched),
        )


@dataclass
class MustTouch(Assertion):
    """Assert the harvester chain includes every cell in ``cells``."""
    cells: Sequence[XY] = ()
    name: str = "MustTouch"

    def describe(self) -> str:
        cells = sorted({(int(x), int(y)) for x, y in self.cells})
        return f"harvester chain touches every cell in {cells}"

    def evaluate(self, policy, *, context):
        required: Set[XY] = {(int(x), int(y)) for x, y in self.cells}
        path = set(_harvester_path(policy))
        missing = sorted(required - path)
        ok = not missing
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail="all touched" if ok else f"missing: {missing}",
            expected=str(sorted(required)),
            observed=str(sorted(path)),
        )


@dataclass
class HarvesterChainHits(Assertion):
    """Softer ``MustTouch``: assert ≥ ``min_hits`` cells in ``region`` are touched."""
    region: Sequence[XY] = ()
    min_hits: int = 1
    name: str = "HarvesterChainHits"

    def describe(self) -> str:
        cells = sorted({(int(x), int(y)) for x, y in self.region})
        return f"harvester chain hits ≥{self.min_hits} of {cells}"

    def evaluate(self, policy, *, context):
        target: Set[XY] = {(int(x), int(y)) for x, y in self.region}
        path = set(_harvester_path(policy))
        hits = sorted(target & path)
        ok = len(hits) >= self.min_hits
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=f"hit {len(hits)}/{self.min_hits} of {len(target)} target cells",
            expected=f"≥{self.min_hits} of {sorted(target)}",
            observed=str(hits),
        )


@dataclass
class MinExpectedValue(Assertion):
    """Assert the projected RED value of the harvester chain ≥ threshold.

    "Projected value" = sum of ``cell_purity`` for every cell in the
    harvester path that's RED on the grid right now. This is a *lower
    bound* — fog cells along the path could harvest more, but we
    don't credit the policy for unknown unknowns. If the agent's chain
    only walks trace tiles, this catches it.
    """
    min_value: int = 100
    name: str = "MinExpectedValue"

    def describe(self) -> str:
        return f"projected RED value ≥ {self.min_value}"

    def evaluate(self, policy, *, context):
        path = _harvester_path(policy)
        total = sum(context.cell_purity(x, y) for x, y in path)
        ok = total >= self.min_value
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=f"projected RED value {total} (min {self.min_value})",
            expected=f"≥{self.min_value}",
            observed=str(total),
        )


@dataclass
class NoSyntheticGreenSteps(Assertion):
    """Assert no harvester drop/step lands on a synthetic-green cell.

    Synthetic green = a cell where p1 previously harvested RED →
    banking it scores zero (RULEBOOK §3.10). Stepping onto one wastes
    a hold slot.
    """
    name: str = "NoSyntheticGreenSteps"

    def describe(self) -> str:
        return "no drops/steps on synthetic-green cells"

    def evaluate(self, policy, *, context):
        offenders: List[XY] = []
        for move in policy:
            if move.get("a") not in ("drop", "step"):
                continue
            xy = _xy_of(move)
            if xy is None:
                continue
            if context.is_synthetic_green(*xy):
                offenders.append(xy)
        ok = not offenders
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail="no synth-green steps" if ok else f"hit synth-green: {offenders}",
            expected="no drops/steps on synthetic-green cells",
            observed=str(offenders),
        )


@dataclass
class ProbesInDistinctQuadrants(Assertion):
    """Assert probes are spread across ≥ ``min_quadrants`` map quadrants.

    Quadrants are NW/NE/SW/SE based on the map midpoint. Forces the
    blind-dawn agent to spread probe vision rather than stacking
    them all in one corner.
    """
    min_quadrants: int = 2
    name: str = "ProbesInDistinctQuadrants"

    def describe(self) -> str:
        return f"probes spread across ≥{self.min_quadrants} of the 4 map quadrants"

    def evaluate(self, policy, *, context):
        sess = context.session
        mx, my = sess.width / 2, sess.height / 2
        quads: Set[str] = set()
        n = 0
        for move in policy:
            if move.get("a") != "probe":
                continue
            xy = _xy_of(move)
            if xy is None:
                continue
            n += 1
            qx = "E" if xy[0] >= mx else "W"
            qy = "S" if xy[1] >= my else "N"
            quads.add(qy + qx)
        ok = len(quads) >= self.min_quadrants and n > 0
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"{n} probe(s) in quadrants {sorted(quads)}"
                if n
                else "no probes submitted"
            ),
            expected=f"probes in ≥{self.min_quadrants} quadrants",
            observed=str(sorted(quads)),
        )


@dataclass
class ProbesInRegion(Assertion):
    """Assert every submitted probe lies inside ``region`` (inclusive rect)."""
    region: Tuple[int, int, int, int] = (0, 0, 0, 0)  # (x0, y0, x1, y1) inclusive
    name: str = "ProbesInRegion"

    def describe(self) -> str:
        return f"every probe lands inside the box {self.region}"

    def evaluate(self, policy, *, context):
        x0, y0, x1, y1 = self.region
        outside: List[XY] = []
        for move in policy:
            if move.get("a") != "probe":
                continue
            xy = _xy_of(move)
            if xy is None:
                continue
            if not (x0 <= xy[0] <= x1 and y0 <= xy[1] <= y1):
                outside.append(xy)
        ok = not outside
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail="all probes in region" if ok else f"probes outside region: {outside}",
            expected=f"probes in box {(x0, y0, x1, y1)}",
            observed=str(outside),
        )


@dataclass
class NoHarvesterDeployment(Assertion):
    """Assert the policy contains no harvester drop/step actions.

    Used by probes-only scenarios where the agent should leave the
    harvester in orbit — blind-dropping a harvester onto fog is a
    well-known heuristic failure mode (it gambles a unit for a single
    vision tick) and we want the eval to flag it.
    """
    name: str = "NoHarvesterDeployment"

    def describe(self) -> str:
        return "no drop / step actions submitted (harvester stays in orbit)"

    def evaluate(self, policy, *, context):
        offenders: List[Dict[str, Any]] = [
            dict(m) for m in policy if m.get("a") in ("drop", "step")
        ]
        ok = not offenders
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                "no harvester deployment"
                if ok
                else f"{len(offenders)} drop/step move(s) submitted: {offenders}"
            ),
            expected="0 drop/step actions",
            observed=str(len(offenders)),
        )


@dataclass
class EndsWithPickup(Assertion):
    """Assert the policy ends every deployed harvester's chain with a pickup.

    "Deployed" here means: a harvester that the policy either drops or
    steps. Probe-only policies are skipped (no pickup needed). The
    check walks the policy and tracks each harvester's last action;
    we fail any harvester whose last action wasn't ``pickup``.
    """
    name: str = "EndsWithPickup"

    def describe(self) -> str:
        return "every deployed harvester ends its chain with a pickup"

    def evaluate(self, policy, *, context):
        last_action: Dict[str, str] = {}
        deployed: Set[str] = set()
        for move in policy:
            action = move.get("a")
            unit = str(move.get("unit") or "")
            if action in ("drop", "step", "pickup") and unit:
                last_action[unit] = str(action)
                if action != "pickup":
                    deployed.add(unit)
        missing = [u for u in sorted(deployed) if last_action.get(u) != "pickup"]
        ok = not missing
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                "all deployed harvesters end with pickup"
                if ok
                else f"missing pickup: {missing}"
            ),
            expected="last action per deployed harvester is 'pickup'",
            observed=str(last_action),
        )


@dataclass
class MinPureCellsTouched(Assertion):
    """Assert the harvester path touches ≥ ``min_pure`` cells at or above
    ``purity_threshold``.

    Pure RED (purity 255) is the highest-value cell tier — chains that
    skip a visible pure cluster in favour of a vein chain leak score.
    This assertion catches that regression at the eval level.
    """
    min_pure: int = 1
    purity_threshold: int = 255
    name: str = "MinPureCellsTouched"

    def describe(self) -> str:
        return f"harvester chain touches ≥{self.min_pure} cell(s) at purity ≥{self.purity_threshold}"

    def evaluate(self, policy, *, context):
        path = _harvester_path(policy)
        hits = [
            (x, y) for (x, y) in path
            if context.cell_purity(x, y) >= self.purity_threshold
        ]
        ok = len(hits) >= self.min_pure
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"hit {len(hits)} pure cell(s) at purity ≥{self.purity_threshold}: {hits}"
                if hits
                else f"no cells at purity ≥{self.purity_threshold} on path"
            ),
            expected=f"≥{self.min_pure} cell(s) at purity ≥{self.purity_threshold}",
            observed=str(hits),
        )


@dataclass
class HarvesterChainActionCount(Assertion):
    """Assert every deployed harvester's chain has ≤ ``max_actions`` moves.

    Counts ``drop`` + ``step`` + ``pickup`` per harvester unit. Used by
    EMP-resilience scenarios where chains must finish by hour ~6 to
    avoid being interrupted by a confirmed-incoming EMP cloud.
    """
    max_actions: int = 6
    name: str = "HarvesterChainActionCount"

    def describe(self) -> str:
        return f"every deployed harvester completes its chain in ≤{self.max_actions} actions"

    def evaluate(self, policy, *, context):
        counts: Dict[str, int] = {}
        for move in policy:
            if move.get("a") not in ("drop", "step", "pickup"):
                continue
            unit = str(move.get("unit") or "")
            if not unit:
                continue
            counts[unit] = counts.get(unit, 0) + 1
        offenders = {u: n for u, n in counts.items() if n > self.max_actions}
        ok = not offenders
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"all chains ≤{self.max_actions} actions: {counts}"
                if ok
                else f"over budget: {offenders}"
            ),
            expected=f"each harvester ≤{self.max_actions} actions",
            observed=str(counts),
        )


@dataclass
class DropsInDifferentRegions(Assertion):
    """Assert at least two distinct harvester drops with pairwise Manhattan ≥ ``min_manhattan``.

    Used for multi-harvester sibling-claim scenarios: when two orbit
    harvesters drop, they must target separate RED clusters rather than
    stacking on the same site (the second one walks over synthetic
    green and banks zero).
    """
    min_manhattan: int = 6
    name: str = "DropsInDifferentRegions"

    def describe(self) -> str:
        return f"≥2 harvester drops separated by ≥{self.min_manhattan} Manhattan"

    def evaluate(self, policy, *, context):
        drops: List[XY] = []
        for move in policy:
            if move.get("a") != "drop":
                continue
            xy = _xy_of(move)
            if xy is not None:
                drops.append(xy)
        if len(drops) < 2:
            return AssertionResult(
                name=self.name,
                passed=False,
                detail=f"only {len(drops)} drop(s) submitted",
                expected=f"≥2 drops separated by ≥{self.min_manhattan}",
                observed=str(drops),
            )
        worst = min(
            abs(drops[i][0] - drops[j][0]) + abs(drops[i][1] - drops[j][1])
            for i in range(len(drops))
            for j in range(i + 1, len(drops))
        )
        ok = worst >= self.min_manhattan
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=f"min pairwise distance {worst} (want ≥{self.min_manhattan}); drops {drops}",
            expected=f"min pairwise Manhattan ≥{self.min_manhattan}",
            observed=str(worst),
        )


@dataclass
class NoDropAtCells(Assertion):
    """Assert no ``drop`` action lands at any of ``cells``.

    Used by drop-legality scenarios (e.g. echo-only cell is NOT a legal
    landing pad under live_only mode — the agent must not queue a drop
    there).
    """
    cells: Sequence[XY] = ()
    reason: str = ""
    name: str = "NoDropAtCells"

    def describe(self) -> str:
        cells = sorted({(int(x), int(y)) for x, y in self.cells})
        suffix = f" — {self.reason}" if self.reason else ""
        return f"no drop into {cells}{suffix}"

    def evaluate(self, policy, *, context):
        forbidden: Set[XY] = {(int(x), int(y)) for x, y in self.cells}
        landed: List[XY] = []
        for move in policy:
            if move.get("a") != "drop":
                continue
            xy = _xy_of(move)
            if xy is None:
                continue
            if xy in forbidden:
                landed.append(xy)
        ok = not landed
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                "no forbidden drops"
                if ok
                else f"drop(s) into forbidden cells: {landed}"
            ),
            expected=f"no drops in {sorted(forbidden)}",
            observed=str(landed),
        )


@dataclass
class PolicyContainsAction(Assertion):
    """Assert the submitted policy contains at least ``min_count`` moves
    whose ``a`` field equals ``action``.

    Used by combat-composition scenarios (e.g. must_fire_emp) where the
    agent's compose decision — not just legality — is under test. If
    the scenario is set up so an EMP salvo dominates every alternative,
    a failure here means the agent read the menu but didn't pick the
    right primitive.
    """
    action: str = ""
    min_count: int = 1
    name: str = "PolicyContainsAction"

    def describe(self) -> str:
        return f"policy contains ≥{self.min_count} '{self.action}' action(s)"

    def evaluate(self, policy, *, context):
        matches = [m for m in policy if str(m.get("a") or "") == self.action]
        ok = len(matches) >= self.min_count
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"found {len(matches)} '{self.action}' action(s)"
                if ok
                else f"only found {len(matches)} '{self.action}' action(s), "
                     f"expected ≥{self.min_count}"
            ),
            expected=f"≥{self.min_count} '{self.action}'",
            observed=str([m for m in matches]),
        )


@dataclass
class EmpSalvoCoversCell(Assertion):
    """Assert at least one emp_launch move's 3-target salvo covers ``target``
    within radius ``radius`` (default 2 = the EMP cloud radius).

    Used to verify the agent didn't just fire an EMP anywhere — it fired
    it with intent, tiling the salvo so a specific tactical cell (e.g.
    the walking corridor for an incoming enemy hot-drop) sits inside a
    resulting cloud.
    """
    target: XY = (0, 0)
    radius: int = 2
    name: str = "EmpSalvoCoversCell"

    def describe(self) -> str:
        return (
            f"an emp_launch salvo covers {tuple(self.target)} "
            f"within radius {self.radius}"
        )

    def evaluate(self, policy, *, context):
        tx, ty = int(self.target[0]), int(self.target[1])
        r2 = int(self.radius) * int(self.radius)
        salvos_seen: List[List[List[int]]] = []
        for move in policy:
            if str(move.get("a") or "") != "emp_launch":
                continue
            targets = move.get("at") or []
            if not isinstance(targets, list):
                continue
            salvos_seen.append(targets)
            for t in targets:
                if not (isinstance(t, (list, tuple)) and len(t) == 2):
                    continue
                try:
                    x, y = int(t[0]), int(t[1])
                except (TypeError, ValueError):
                    continue
                dx, dy = x - tx, y - ty
                if dx * dx + dy * dy <= r2:
                    return AssertionResult(
                        name=self.name,
                        passed=True,
                        detail=(
                            f"salvo target ({x},{y}) covers "
                            f"({tx},{ty}) within radius {self.radius}"
                        ),
                        expected=(
                            f"emp salvo covering ({tx},{ty}) "
                            f"within r{self.radius}"
                        ),
                        observed=f"missile at ({x},{y})",
                    )
        detail = (
            f"no emp_launch move covers ({tx},{ty})"
            if salvos_seen
            else "no emp_launch action in policy"
        )
        return AssertionResult(
            name=self.name,
            passed=False,
            detail=detail,
            expected=(
                f"emp salvo covering ({tx},{ty}) within r{self.radius}"
            ),
            observed=str(salvos_seen),
        )


@dataclass
class ActionOrder(Assertion):
    """Assert that in the submitted queue, the first move with action
    ``before`` appears strictly before the first move with action
    ``after``. If either action is missing, the assertion fails.

    Enables temporal-composition checks:
      * ActionOrder(before="emp_launch", after="drop") — EMP fires
        before any harvester lands (so drops land outside a resulting
        cloud OR after cloud clears).
      * ActionOrder(before="pickup", after="emp_launch") — pickup
        completes before the EMP slot fires (so the harvester's cargo
        is safely banked before its cells freeze).
    """
    before: str = ""
    after: str = ""
    name: str = "ActionOrder"

    def describe(self) -> str:
        return f"'{self.before}' appears before '{self.after}' in the policy queue"

    def evaluate(self, policy, *, context):
        first_before = -1
        first_after = -1
        for idx, move in enumerate(policy):
            act = str(move.get("a") or "")
            if act == self.before and first_before < 0:
                first_before = idx
            elif act == self.after and first_after < 0:
                first_after = idx
        if first_before < 0:
            return AssertionResult(
                name=self.name, passed=False,
                detail=f"no '{self.before}' action in queue",
                expected=f"'{self.before}' present before '{self.after}'",
                observed="missing 'before'",
            )
        if first_after < 0:
            return AssertionResult(
                name=self.name, passed=False,
                detail=f"no '{self.after}' action in queue",
                expected=f"'{self.before}' present before '{self.after}'",
                observed="missing 'after'",
            )
        ok = first_before < first_after
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"'{self.before}' at index {first_before} < "
                f"'{self.after}' at index {first_after}"
                if ok
                else f"'{self.before}' at index {first_before} "
                     f"is NOT before '{self.after}' at index {first_after}"
            ),
            expected=f"idx('{self.before}') < idx('{self.after}')",
            observed=f"before={first_before}, after={first_after}",
        )


# ---------------------------------------------------------------------------
# Coherence assertions (v1.9)
#
# The assertions below inspect the *agent's reasoning* alongside its
# policy queue. They exist because the standard action/geometry checks
# can pass on a scenario the agent is actually solving by luck — e.g.
# a probe lands in the seam because we compiled it as a candidate,
# not because the agent understood the seam is worth probing. When
# scaling from single-turn comprehension to full seasons, we need
# grounding assertions that catch:
#
#   * strategist rationale that never mentions the observable trigger
#     (redsign the agent should have raced to, EMP the agent should
#     have anticipated) — even when the resulting policy happens to
#     touch the right cells;
#   * strategist plan labels drifting from the compiled/tactician
#     menu (compiler-blindness — the classic ``fleet_rebuild ⇒
#     build_probes`` hallucination we saw in Season 3);
#   * tactician "selected 4 candidates, materialised 1" silent drops
#     that let a scenario score positive on `MustTouch` while the
#     other 3 vanished from the queue.
# ---------------------------------------------------------------------------


@dataclass
class RationaleMentions(Assertion):
    """Assert the agent's rationale contains at least one of ``needles``.

    Case-insensitive substring match — the rationale is free-form
    LLM text so we deliberately keep the check loose (missing a
    keyword is a signal, not a syntactic failure).

    Typical use:
      * ``RationaleMentions(needles={"redsign", "red-sign"})`` — the
        strategist explicitly reasoned about the beacon the world
        placed for it, rather than silently drifting into a
        procedural harvest chain.
      * ``RationaleMentions(needles={"emp", "chaff", "denial"},
        require_all=False)`` — the tactician acknowledged the rival's
        offensive posture at all.
    """
    needles: Sequence[str] = ()
    require_all: bool = False
    name: str = "RationaleMentions"

    def describe(self) -> str:
        joiner = " AND " if self.require_all else " OR "
        return (
            f"rationale mentions {joiner.join(repr(n) for n in self.needles)}"
        )

    def evaluate(self, policy, *, context):
        text = (context.rationale or "").lower()
        found: List[str] = []
        missing: List[str] = []
        for n in self.needles:
            if str(n).lower() in text:
                found.append(str(n))
            else:
                missing.append(str(n))
        if self.require_all:
            ok = not missing
        else:
            ok = bool(found)
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"found={found} missing={missing} "
                f"(rationale len={len(context.rationale or '')})"
            ),
            expected=(
                f"rationale contains {'all' if self.require_all else 'any'} of "
                f"{list(self.needles)}"
            ),
            observed=f"found={found}",
        )


@dataclass
class RationaleDoesNotMention(Assertion):
    """Assert the agent's rationale contains NONE of ``needles``.

    The inverse of :class:`RationaleMentions`. Use to catch
    hallucinations the agent should never emit — e.g. an orbit-only
    verb during a Nox turn (``build_probes``), an action for a
    non-existent unit, or a plan label the compiler can't turn into
    a candidate.

    Also useful for negative-space doctrine checks (the agent must
    not "blind-drop" when the arsenal tracker flagged a chaff-capable
    rival — the negative check is easier to encode than the positive
    one because there's no single correct alternative to enumerate).
    """
    needles: Sequence[str] = ()
    name: str = "RationaleDoesNotMention"

    def describe(self) -> str:
        return f"rationale does NOT mention {list(self.needles)}"

    def evaluate(self, policy, *, context):
        text = (context.rationale or "").lower()
        offenders = [n for n in self.needles if str(n).lower() in text]
        ok = not offenders
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                "clean" if ok else f"forbidden strings present: {offenders}"
            ),
            expected=f"rationale free of {list(self.needles)}",
            observed=f"offenders={offenders}",
        )


@dataclass
class PlanLabelIn(Assertion):
    """Assert the strategist's plan label is one of ``labels``.

    The plan label is a compact token the strategist emits summarising
    its chosen doctrine for the turn (e.g. ``"harvest_pure"``,
    ``"chase_redsign"``, ``"fleet_rebuild"``). The runner extracts it
    and stashes it on :class:`AssertionContext.plan_label`.

    Scenarios pin the acceptable set — a redsign scenario expects the
    plan to be in ``{"chase_redsign", "harvest_seam", "probe_seam"}``;
    a fleet-rebuild orbit expects ``{"fleet_rebuild", "build_probes",
    "build_emp"}``.

    Matching is case-insensitive on the whole label. Empty label
    always fails (the runner failed to extract one).
    """
    labels: Sequence[str] = ()
    name: str = "PlanLabelIn"

    def describe(self) -> str:
        return f"plan_label ∈ {list(self.labels)}"

    def evaluate(self, policy, *, context):
        got = (context.plan_label or "").strip().lower()
        allowed = {str(l).strip().lower() for l in self.labels}
        if not got:
            return AssertionResult(
                name=self.name,
                passed=False,
                detail="no plan_label extracted by runner",
                expected=f"plan_label ∈ {list(self.labels)}",
                observed="<empty>",
            )
        ok = got in allowed
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"plan_label={got!r} in allowed set"
                if ok
                else f"plan_label={got!r} NOT in {sorted(allowed)}"
            ),
            expected=f"plan_label ∈ {list(self.labels)}",
            observed=got,
        )


@dataclass
class PlanMatchesMaterialisedVerb(Assertion):
    """Assert the strategist's plan label maps to a verb that actually
    appears in the submitted policy.

    Catches the compiler-blind-to-plan class of bugs we hit in Season
    3, where the strategist emitted ``plan=fleet_rebuild`` and the
    tactician wrote a rationale about probe orbit-builds, but the
    compiler produced a Nox-harvest menu and the final queue contained
    only ``move_and_harvest`` moves — plan and policy drifted apart
    silently.

    ``mapping`` is ``{plan-substring: {allowed-action-verbs, …}}``.
    If any key is a substring (case-insensitive) of ``plan_label``,
    the policy must contain at least one move whose ``a`` field is in
    that key's verb set. If no key matches (label is exotic or
    empty), the assertion passes with a "no rule applies" note — the
    scenario should also pin ``PlanLabelIn`` if it wants to require a
    specific label.
    """
    mapping: Optional[Dict[str, Sequence[str]]] = None
    name: str = "PlanMatchesMaterialisedVerb"

    def _get_map(self) -> Dict[str, Sequence[str]]:
        if self.mapping is not None:
            return self.mapping
        return {
            # ── Nox / planning-phase verbs (see MoveTag in game/policy.py) ──
            # A "harvester chain" is a drop followed by step moves; both
            # verbs count as chain-consistent for the coherence check.
            "harvest": ("drop", "step"),
            "chain":   ("drop", "step"),
            "seam":    ("drop", "step", "probe"),
            "redsign": ("drop", "step", "probe"),
            "probe":   ("probe",),
            "scout":   ("probe",),
            "drop":    ("drop", "step"),
            "blind":   ("drop", "step"),
            "emp":     ("emp_launch",),
            # v1.31 — the caltrop is retired, so ``mine_lay`` can never
            # materialise: leaving it here would let a "deny" plan claim
            # coherence against a verb the parser now refuses outright.
            "deny":    ("emp_launch", "chaff_flare"),
            "chaff":   ("chaff_flare",),
            "pickup":  ("pickup",),
            "recover": ("pickup",),
            "ship":    ("ship_catapult", "solar_jettison"),
            "vault":   ("ship_catapult", "solar_jettison", "pickup",
                        "refine", "refine_cascade"),
            "rebuild":       ("build_probe", "build_emp", "build_chaff",
                              "build_harvester"),
            "fleet_rebuild": ("build_probe", "build_emp", "build_chaff",
                              "build_harvester"),
            "build":         ("build_probe", "build_emp", "build_chaff",
                              "build_harvester"),
            "refine":  ("refine", "refine_cascade"),
            "repair":  ("repair",),
        }

    def describe(self) -> str:
        return "plan_label maps to a verb that appears in the policy queue"

    def evaluate(self, policy, *, context):
        label = (context.plan_label or "").strip().lower()
        m = self._get_map()
        matched_keys = [k for k in m if k in label]
        if not label or not matched_keys:
            return AssertionResult(
                name=self.name,
                passed=True,
                detail=(
                    f"no rule applies (plan_label={label!r}); "
                    "assertion vacuously passes"
                ),
                expected="plan_label matches a mapping key",
                observed=f"plan_label={label!r}",
            )
        needed: Set[str] = set()
        for k in matched_keys:
            needed.update(str(v) for v in m[k])
        seen_verbs = {str(mv.get("a") or "") for mv in policy}
        overlap = needed & seen_verbs
        ok = bool(overlap)
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"plan_label={label!r} → keys {matched_keys} → verbs {sorted(needed)}; "
                f"policy verbs = {sorted(v for v in seen_verbs if v)}; "
                f"overlap = {sorted(overlap)}"
            ),
            expected=f"policy contains any of {sorted(needed)}",
            observed=f"policy verbs = {sorted(v for v in seen_verbs if v)}",
        )


@dataclass
class NoSilentSelectionDrops(Assertion):
    """Assert every candidate the tactician selected made it to the
    final policy queue.

    ``selection_count`` is the tactician's ``len(selections)``;
    ``materialized_count`` is the length of the policy queue actually
    submitted for the seat. In a healthy run these are equal (or, if
    the compiler is expanding chained candidates into multiple moves,
    ``materialized_count >= selection_count``).

    A gap of ``selection_count > materialized_count`` means the
    tactician chose a candidate the materialiser then silently
    dropped — the OBS-005 pathology we tracked in the pilot_v4 season.

    ``tolerance`` allows N missing before failing (default 0). Bump
    to 1 for a harness that legitimately merges the last two chain
    tails, etc. — anything beyond ``tolerance`` is treated as a bug.
    """
    tolerance: int = 0
    name: str = "NoSilentSelectionDrops"

    def describe(self) -> str:
        return (
            f"materialized_count >= selection_count "
            f"(tolerance={self.tolerance})"
        )

    def evaluate(self, policy, *, context):
        sel = int(context.selection_count or 0)
        mat = int(context.materialized_count or 0)
        # If neither is populated, treat as vacuously passing — the
        # runner just didn't wire the counts through (e.g. a legacy
        # scenario). Assertions that need coverage should combine this
        # with a PolicyContainsAction / MustTouch to force a floor.
        if sel == 0 and mat == 0:
            return AssertionResult(
                name=self.name,
                passed=True,
                detail="selection_count and materialized_count both 0 (untracked)",
                expected=f"materialized >= selected (tol={self.tolerance})",
                observed="selected=0, materialized=0",
            )
        gap = sel - mat
        ok = gap <= int(self.tolerance)
        return AssertionResult(
            name=self.name,
            passed=ok,
            detail=(
                f"selected={sel}, materialized={mat}, gap={gap} "
                f"(tolerance={self.tolerance})"
            ),
            expected=f"materialized >= selected - {self.tolerance}",
            observed=f"selected={sel}, materialized={mat}",
        )


@dataclass
class HarvesterChainNearCell(Assertion):
    """Assert at least one harvester drop/step lands within Chebyshev
    distance ``max_distance`` of ``target``.

    Softer than :class:`HarvesterChainHits` — some scenarios test that
    the pilot "raced *toward*" a target seam without requiring the
    chain to plant on the exact pure cell (e.g. redsign fuzzes the
    exact seam; the correct doctrine is "get adjacent within one
    move" per §4.11).

    Reads the wire-format move grammar (``MoveTag`` in
    :mod:`sea_of_colours.game.policy`): a "chain" is a drop followed
    by a sequence of step moves. We reuse :func:`_harvester_path` so
    the geometry matches :class:`HarvesterChainHits` byte-for-byte.
    """
    target: XY = (0, 0)
    max_distance: int = 1
    unit_id: Optional[str] = None
    name: str = "HarvesterChainNearCell"

    def describe(self) -> str:
        return (
            f"a harvester drop/step is within Chebyshev distance "
            f"{self.max_distance} of {tuple(self.target)}"
        )

    def evaluate(self, policy, *, context):
        tx, ty = int(self.target[0]), int(self.target[1])
        # Filter by unit_id when scoped — mirrors the shape of the wire
        # ``drop``/``step`` moves which both carry ``unit``.
        if self.unit_id:
            scoped = [m for m in policy if str(m.get("unit") or "") == self.unit_id]
        else:
            scoped = list(policy)
        path = _harvester_path(scoped)
        if not path:
            return AssertionResult(
                name=self.name,
                passed=False,
                detail="no harvester drop/step in policy",
                expected=(
                    f"chain step within d={self.max_distance} of "
                    f"({tx},{ty})"
                ),
                observed="no chain",
            )
        best = None
        for x, y in path:
            d = max(abs(x - tx), abs(y - ty))
            if best is None or d < best[0]:
                best = (d, (x, y))
            if d <= int(self.max_distance):
                return AssertionResult(
                    name=self.name,
                    passed=True,
                    detail=(
                        f"step ({x},{y}) at Chebyshev distance {d} from "
                        f"target ({tx},{ty})"
                    ),
                    expected=(
                        f"chain step within d={self.max_distance} of "
                        f"({tx},{ty})"
                    ),
                    observed=f"step ({x},{y}) d={d}",
                )
        return AssertionResult(
            name=self.name,
            passed=False,
            detail=(
                f"closest step was {best[1]} at Chebyshev distance {best[0]} "
                f"(needed <= {self.max_distance})"
            ),
            expected=(
                f"chain step within d={self.max_distance} of ({tx},{ty})"
            ),
            observed=f"closest {best[1]} d={best[0]}",
        )
