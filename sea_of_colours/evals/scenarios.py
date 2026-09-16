"""Built-in eval scenarios.

Each scenario is a small dataclass returned by a factory function. The
factory takes no arguments — its job is to construct a deterministic
fixture via :class:`~sea_of_colours.evals.builder.WorldBuilder` and
declare the assertions that gate a "pass".

Adding a new scenario:

1. Write a factory ``def my_scenario() -> Scenario`` that returns a
   :class:`Scenario` with ``name``, ``summary``, ``build`` (a no-arg
   callable returning ``(store, session_id)``), ``assertions``, and
   ``player`` (the seat we're evaluating; defaults to ``"p1"``).
2. Append it to :data:`SCENARIOS` below. The CLI auto-discovers from
   this list; pytest parametrises over it directly.

Scenarios are intentionally small — the heavy lifting belongs in the
builder. If a fixture needs > 30 lines, it's probably either testing
too many things at once (split it) or the builder is missing a
mutator (extend the builder).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Sequence, Tuple

from sea_of_colours.evals.assertions import (
    Assertion,
    ActionOrder,
    DropsInDifferentRegions,
    EmpSalvoCoversCell,
    EndsWithPickup,
    HarvesterChainActionCount,
    HarvesterChainHits,
    HarvesterChainNearCell,
    MinExpectedValue,
    MinPureCellsTouched,
    MustAvoid,
    MustTouch,
    NoDropAtCells,
    NoHarvesterDeployment,
    NoSilentSelectionDrops,
    NoSyntheticGreenSteps,
    PlanLabelIn,
    PlanMatchesMaterialisedVerb,
    PolicyContainsAction,
    ProbeCount,
    ProbesInDistinctQuadrants,
    RationaleDoesNotMention,
    RationaleMentions,
)
from sea_of_colours.evals.builder import WorldBuilder
from sea_of_colours.game.session import HARVESTER_HOLD_CAPACITY
from sea_of_colours.snowpark.store import SocStore


@dataclass
class Scenario:
    """A named eval fixture + the assertions that gate its pass."""
    name: str
    summary: str
    build: Callable[[], Tuple[SocStore, str]]
    assertions: Sequence[Assertion]
    player: str = "p1"
    tags: Sequence[str] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# 1. User-listed: probes-only when no RED is visible
# ---------------------------------------------------------------------------
def probes_only() -> Scenario:
    """Day 2, all assets in orbit, zero RED visible — agent should probe, not blind-drop."""
    def build():
        return (
            WorldBuilder(seed=42, day=2, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .clear_visibility("p1")
            .build()
        )
    return Scenario(
        name="probes_only",
        summary=(
            "Day 2, full fog, harvester in orbit. Agent should submit probes "
            "(no blind harvester drop), spread across at least 2 quadrants."
        ),
        build=build,
        assertions=[
            ProbeCount(min=1, max=3),
            ProbesInDistinctQuadrants(min_quadrants=2),
            NoHarvesterDeployment(),
        ],
        tags=("vision", "probes"),
    )


# ---------------------------------------------------------------------------
# 2. User-listed: collision avoidance — pick safer seam
# ---------------------------------------------------------------------------
def collision_avoidance() -> Scenario:
    """Two visible RED seams; the highest-value one has an enemy harvester sitting on its entry tile."""
    def build():
        # Pure-tier seam (contested by enemy):
        contested_seam = [
            (12, 8, 255), (12, 9, 255), (13, 8, 250), (13, 9, 240),
        ]
        # Vein-tier seam (safe):
        safe_seam = [
            (30, 15, 140), (30, 16, 130), (31, 16, 120),
        ]
        all_seam_cells = [(x, y) for x, y, _ in contested_seam + safe_seam]
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            # Reveal both seams to p1
            .reveal_red(contested_seam)
            .reveal_red(safe_seam)
            # Anchor probes so the seams are in LIVE LOS — without this
            # the agent's navigation.best_red_visible will be empty.
            .grant_live_vision(all_seam_cells)
            # Drop the enemy harvester onto the pure seam's entry tile
            .place_enemy_harvester("harvester_p2", at=(12, 8))
            .build()
        )
    return Scenario(
        name="collision_avoidance",
        summary=(
            "Pure seam at (12,8) contested by enemy harvester; vein seam at "
            "(30,15) is clear. Agent should choose the safe seam (or avoid "
            "the contested entry tile entirely)."
        ),
        build=build,
        assertions=[
            MustAvoid(
                cells=[(12, 8)],
                reason="enemy harvester at (12,8) — collision = mutual damage",
                check_harvester=True,
                check_probes=False,
            ),
            HarvesterChainHits(
                region=[(30, 15), (30, 16), (31, 16), (29, 15), (32, 16)],
                min_hits=1,
            ),
        ],
        tags=("collision", "tactics"),
    )


# ---------------------------------------------------------------------------
# 3. User-listed: tier choice — don't settle for trace
# ---------------------------------------------------------------------------
def tier_choice() -> Scenario:
    """Three discrete RED clusters at different (distance, value) tradeoffs."""
    def build():
        # Trace cluster: very close (Manhattan ~ 2 from drop point (20,14)), value ~30
        trace = [(21, 14, 30), (22, 14, 25), (21, 15, 20)]
        # Vein cluster: medium (Manhattan ~ 5), value ~120
        vein = [(25, 14, 130), (26, 14, 120), (25, 15, 110)]
        # Mass cluster: far (Manhattan ~ 8), value ~200
        mass = [(28, 14, 210), (28, 15, 200), (29, 14, 190)]
        all_cells = [(x, y) for x, y, _ in trace + vein + mass]
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(trace + vein + mass)
            .grant_live_vision(all_cells)
            .build()
        )
    return Scenario(
        name="tier_choice",
        summary=(
            "Three discrete RED clusters visible (trace, vein, mass). A "
            "well-played turn reaches at least the vein cluster — settling "
            "for trace fails MinExpectedValue."
        ),
        build=build,
        assertions=[
            # Trace cluster alone yields ~75; vein chain ~360; mass chain ~600.
            MinExpectedValue(min_value=250),
            EndsWithPickup(),
        ],
        tags=("strategy", "tier"),
    )


# ---------------------------------------------------------------------------
# 4. Day 1, full fog, all assets in orbit
# ---------------------------------------------------------------------------
def blind_dawn() -> Scenario:
    """Day 1, total darkness — must use probes, never blind-drop."""
    def build():
        return (
            WorldBuilder(seed=99, day=1, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .clear_visibility("p1")
            .build()
        )
    return Scenario(
        name="blind_dawn",
        summary=(
            "Day 1, total fog. Agent has never seen any cell. Should "
            "submit ≥2 probes spread across the map; no blind harvester drop."
        ),
        build=build,
        assertions=[
            ProbeCount(min=2, max=3),
            ProbesInDistinctQuadrants(min_quadrants=2),
            NoHarvesterDeployment(),
        ],
        tags=("vision", "probes", "opening"),
    )


# ---------------------------------------------------------------------------
# 5. Enemy probe telegraphs intent
# ---------------------------------------------------------------------------
def enemy_telegraph() -> Scenario:
    """Two equal-value RED clusters; enemy launched a probe near one of them."""
    def build():
        cluster_a = [(12, 18, 200), (13, 18, 190)]
        cluster_b = [(35, 5, 200), (35, 6, 190)]
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(cluster_a + cluster_b)
            .grant_live_vision([(x, y) for x, y, _ in cluster_a + cluster_b])
            .enemy_probe_launched(at=(12, 18), day=2)
            .build()
        )
    return Scenario(
        name="enemy_telegraph",
        summary=(
            "Two equal-value RED clusters. Enemy launched a probe at "
            "(12,18) — they're going there. A good play takes the "
            "uncontested cluster at (35,5)."
        ),
        build=build,
        assertions=[
            HarvesterChainHits(
                region=[(35, 5), (35, 6), (34, 5), (36, 5)],
                min_hits=1,
            ),
        ],
        tags=("opponent", "strategy"),
    )


# ---------------------------------------------------------------------------
# 6. Enemy trail through your seam — don't re-walk it
# ---------------------------------------------------------------------------
def enemy_trail_in_seam() -> Scenario:
    """Fresh enemy trail through the seam ahead — their harvester already harvested it."""
    def build():
        # Original RED seam runs through (18,12)..(22,12)
        seam = [
            (18, 12, 130), (19, 12, 140), (20, 12, 150), (21, 12, 140), (22, 12, 130),
        ]
        # But (19,12), (20,12), (21,12) were harvested yesterday by p2 —
        # so they're now synthetic green. p2's trail passes through these cells.
        harvested = [(19, 12), (20, 12), (21, 12)]
        builder = (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(seam)
            # The mid-seam cells were harvested by p2 yesterday →
            # synthetic green attributed to p2.
            .reveal_green_synthetic(harvested, owner="p2")
            .enemy_trail(harvested, day=3)
        )
        # Give the player live LOS over the whole seam so the agent
        # sees both the still-RED edges and the synthetic-green span.
        builder = builder.grant_live_vision(
            [(x, y) for x, y, _ in seam] + harvested,
        )
        return builder.build()
    return Scenario(
        name="enemy_trail_in_seam",
        summary=(
            "Enemy harvested through cells (19,12), (20,12), (21,12) "
            "yesterday — now synthetic green (score=0). RED still visible "
            "at (18,12) and (22,12). Agent should harvest the still-RED "
            "edges, not walk over synthetic green."
        ),
        build=build,
        assertions=[
            NoSyntheticGreenSteps(),
            HarvesterChainHits(
                region=[(18, 12), (22, 12), (17, 12), (23, 12)],
                min_hits=1,
            ),
        ],
        tags=("opponent", "synthesis"),
    )


# ---------------------------------------------------------------------------
# 7. Vault pressure — pickup, don't keep harvesting
# ---------------------------------------------------------------------------
def vault_pressure() -> Scenario:
    """Harvester surface with 5/6 cargo, vault 47/50, surrounded by GREEN — should pickup."""
    def build():
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester(
                "harvester_p1",
                at=(20, 14),
                state="surface",
                cargo=5,
            )
            # Surround it with synthetic green so any step harvests a
            # score-0 parcel that displaces lower-tier RED in the vault.
            .reveal_green_synthetic([
                (19, 14), (21, 14), (20, 13), (20, 15),
            ])
            # Hoard near full; mostly RED so further GREEN forces tier 3
            # displacement of lowest BLUE then RED.
            .fill_hoard(player="p1", red=47, blue=0, green=0)
            # The harvester itself is at (20,14) so its plus-vision
            # already covers all four orthogonal neighbours — no extra
            # probe needed for this scenario.
            .build()
        )
    return Scenario(
        name="vault_pressure",
        summary=(
            "Harvester on surface (5/6 cargo); vault 47/50. Adjacent cells "
            "are all synthetic green. Agent should pickup, not step further."
        ),
        build=build,
        assertions=[
            NoSyntheticGreenSteps(),
            EndsWithPickup(),
        ],
        tags=("vault", "tactics"),
    )


# ---------------------------------------------------------------------------
# 8. Damaged harvester — only legal move is pickup
# ---------------------------------------------------------------------------
def damaged_harvester() -> Scenario:
    """Surface harvester, damaged=True. Only legal next move is pickup."""
    def build():
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester(
                "harvester_p1",
                at=(20, 14),
                state="surface",
                cargo=0,
                damaged=True,
            )
            # Tempt the agent with rich RED adjacent — should be ignored.
            .reveal_red([
                (19, 14, 240), (21, 14, 250), (20, 13, 230),
            ])
            # The harvester at (20,14) covers these via its plus-vision.
            .build()
        )
    return Scenario(
        name="damaged_harvester",
        summary=(
            "Harvester is damaged on the surface. Stepping is illegal "
            "(damaged units can't move). Only legal play is pickup."
        ),
        build=build,
        assertions=[
            EndsWithPickup(),
            # No step moves at all — the only valid policy action is pickup.
            MustAvoid(
                cells=[(19, 14), (21, 14), (20, 13), (20, 15)],
                reason="damaged harvesters cannot step",
                check_harvester=True,
                check_probes=False,
            ),
        ],
        tags=("damage", "legality"),
    )


# ---------------------------------------------------------------------------
# 9. Final night — no probes, all harvest
# ---------------------------------------------------------------------------
def final_night() -> Scenario:
    """Last night of season. Probes can't pay off — every action should be harvest."""
    def build():
        seam = [
            (25, 14, 200), (26, 14, 210), (27, 14, 190), (28, 14, 180),
        ]
        return (
            WorldBuilder(seed=42, day=7, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(seam)
            .grant_live_vision([(x, y) for x, y, _ in seam])
            .build()
        )
    return Scenario(
        name="final_night",
        summary=(
            "meta.day == season_day_cap. Probes won't pay off this season. "
            "Every action should go toward harvest."
        ),
        build=build,
        assertions=[
            ProbeCount(min=0, max=0),
            HarvesterChainHits(
                region=[(25, 14), (26, 14), (27, 14), (28, 14)],
                min_hits=2,
            ),
        ],
        tags=("endgame",),
    )


# ---------------------------------------------------------------------------
# 10. Probe collision risk — enemy probe at fog cluster centroid
# ---------------------------------------------------------------------------
def probe_collision_risk() -> Scenario:
    """Enemy probe at (15,10); agent's natural fog-probe target is the same cell."""
    def build():
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            # Enemy probe yesterday at the obvious fog centroid
            .place_enemy_probe(at=(15, 10))
            # Reveal a few cells around so the enemy probe is visible echo
            .reveal_to_player(
                [(14, 10), (15, 10), (16, 10), (15, 9), (15, 11)],
                player="p1",
                stale=True,
            )
            .enemy_probe_launched(at=(15, 10), day=2)
            .build()
        )
    return Scenario(
        name="probe_collision_risk",
        summary=(
            "Enemy probe sits at (15,10) — if our probe lands there too "
            "BOTH are destroyed (§3.16). Agent should probe near, not on, "
            "this cell."
        ),
        build=build,
        assertions=[
            MustAvoid(
                cells=[(15, 10)],
                reason="enemy probe present — §3.16 mutual destruction",
                check_probes=True,
                check_harvester=False,
            ),
        ],
        tags=("probes", "collision"),
    )


# ---------------------------------------------------------------------------
# 11. Detour-around-green — two valuable REDs separated by synthetic green
# ---------------------------------------------------------------------------
def green_detour() -> Scenario:
    """Two RED cells with a synthetic-green cell between them. Does the agent path AROUND?

    Geometry::

        . . . . .
        . . . . .   ← row 9  (clear detour corridor)
        R G R . .   ← row 10 (REDs at 15 & 17, synth-green at 16)
        . . . . .   ← row 11 (clear detour corridor)
                       (cols  15 16 17)

    Naive 2-step path (drop @15,10 → step 16,10 → step 17,10) banks
    two REDs *and* a worthless GREEN parcel — burning a hold slot for
    zero score. A competent agent detours one row to either side
    (drop @15,10 → step 15,9 → step 16,9 → step 17,9 → step 17,10) and
    banks two REDs with zero waste.
    """
    def build():
        red_a = (15, 10, 220)
        red_b = (17, 10, 230)
        green_between = (16, 10)
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red([red_a, red_b])
            # The cell between the two REDs is synthetic green —
            # already harvested by p1 last night.
            .reveal_green_synthetic([green_between])
            # Anchor probes TWO rows off the seam so their Euclidean
            # r=2 disks JUST graze the seam without sitting on any
            # detour cell. Probe at (16,7) covers row 7-9 cells around
            # col 14-18 plus (16,9). Probe at (16,13) covers row 11-13
            # cells. Crucially neither probe sits in cols 14-18 of
            # rows 9 or 11 — both detour corridors stay free for the
            # harvester to walk without crushing intel.
            .place_probe(at=(16, 7), owner="p1")
            .place_probe(at=(16, 13), owner="p1")
            # We still need direct LOS on the synth-green at (16,10)
            # and the two RED targets — the probes above only cover
            # rows 8-9 and 11-12, missing row 10. Drop one more probe
            # at (14,10) so its disk covers (14..18, 8..12) — the
            # whole seam + both detour rows — without occupying any
            # cell on the chain's expected path. The harvester drops
            # on (15,10), not (14,10), so the (14,10) probe is safely
            # off the chain.
            .place_probe(at=(14, 10), owner="p1")
            .build()
        )
    return Scenario(
        name="green_detour",
        summary=(
            "Two RED tiles at (15,10) and (17,10) separated by a "
            "synthetic-green cell at (16,10). Naive path through the "
            "green wastes a hold slot — agent should detour via row 9 "
            "or row 11 to harvest BOTH reds cleanly."
        ),
        build=build,
        assertions=[
            # Both REDs must end up on the harvester's path.
            MustTouch(cells=[(15, 10), (17, 10)]),
            # And the agent must NOT walk through the synthetic green
            # between them.
            NoSyntheticGreenSteps(),
            # Sanity: chain projects ≥ 400 RED value (both seams).
            MinExpectedValue(min_value=400),
            EndsWithPickup(),
        ],
        tags=("synthesis", "pathing"),
    )


# ---------------------------------------------------------------------------
# 12. Green corridor — 3 REDs split by 2 greens, must commit to a subset
# ---------------------------------------------------------------------------
def green_corridor() -> Scenario:
    """3 RED cells alternating with 2 synth-greens. No single 5-step chain hits all 3 cleanly.

    Geometry::

        . . . . .
        . . . . .   ← row 9  (clear detour)
        R G R G R   ← row 10 (REDs at 10/12/14, synth-greens at 11/13)
        . . . . .   ← row 11 (clear detour)
                       (cols 10 11 12 13 14)

    Naive 4-step path through row 10 banks ALL 3 REDs but also 2
    worthless GREENs → 5 hold slots used, 2 wasted (effective 60% of
    capacity is RED).

    Clean detour through row 9 can fit at most 2 REDs in the 5-step
    move budget — e.g. drop (10,10) → step 10,9 → 11,9 → 12,9 → 12,10
    (RED) → 13,9 (out of REDs) → pickup. Net: 2 REDs, 0 waste.

    This is a real tradeoff. We don't claim either is universally
    correct — we ONLY assert that whichever the agent picks, the
    chain is internally consistent: if it commits to the detour it
    should NOT crab a green anyway; if it commits to the straight
    line it should land all 3 REDs. We encode the "detour" branch as
    the eval target because that's the harder reasoning step
    (declining short-term throughput to preserve hold-slot
    efficiency).
    """
    def build():
        reds = [(10, 10, 220), (12, 10, 230), (14, 10, 220)]
        greens = [(11, 10), (13, 10)]
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(reds)
            .reveal_green_synthetic(greens)
            .grant_live_vision([
                # row 10 (the seam itself)
                (9, 10), (10, 10), (11, 10), (12, 10), (13, 10), (14, 10), (15, 10),
                # row 9 (detour corridor north)
                (10, 9), (11, 9), (12, 9), (13, 9), (14, 9),
                # row 11 (detour corridor south)
                (10, 11), (11, 11), (12, 11), (13, 11), (14, 11),
            ])
            .build()
        )
    return Scenario(
        name="green_corridor",
        summary=(
            "3 REDs at (10/12/14, 10) alternate with synthetic green at "
            "(11/13, 10). Detour corridors clear on rows 9 and 11. The "
            "5-step move budget means the agent can EITHER bank all 3 "
            "REDs through 2 green steps (3 RED + 2 waste) OR detour and "
            "bank 2 REDs cleanly (2 RED + 0 waste). We assert the "
            "no-waste branch — that's the harder reasoning step."
        ),
        build=build,
        assertions=[
            NoSyntheticGreenSteps(),
            HarvesterChainHits(
                region=[(10, 10), (12, 10), (14, 10)],
                min_hits=2,
            ),
            # 2 REDs at ~220 each ⇒ ≥ 400 projected.
            MinExpectedValue(min_value=400),
            EndsWithPickup(),
        ],
        tags=("synthesis", "pathing", "tradeoff"),
    )


# ---------------------------------------------------------------------------
# 13. Friendly probe in path — don't crush your own intel asset for zero gain
# ---------------------------------------------------------------------------
def friendly_probe_in_path() -> Scenario:
    """Two REDs flank an EMPTY cell carrying the agent's own probe.

    Geometry::

        . . . . .
        . . . . .   ← row 9  (clear detour)
        . R p R .   ← row 10 (REDs at 11 & 13, friendly probe at 12 on EMPTY)
        . . . . .   ← row 11 (clear detour)
                       (cols 10 11 12 13 14)

    Stepping on (12,10) crushes the probe (§3.16 — harvester-on-probe
    destroys the probe) for ZERO score (the cell is empty). A
    competent agent detours through row 9 or 11 to preserve the
    intel asset — the detour is one extra step but saves future
    vision.

    We grant the player ENOUGH probes already deployed elsewhere that
    losing this one would be a real (non-trivial) intel cost — i.e.
    the probe on (12,10) is in a meaningfully informative spot the
    agent should care about retaining.
    """
    def build():
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red([(11, 10, 230), (13, 10, 230)])
            # The middle cell is EMPTY — explicit so the noise generator
            # doesn't randomly put RED here.
            .reveal_empty([(12, 10)])
            # Drop the probe ON the empty cell — that's the cell the
            # agent must NOT step on.
            .place_probe(unit_id="probe_p1_blocker", at=(12, 10))
            .grant_live_vision([
                (11, 10), (12, 10), (13, 10),
                (10, 10), (14, 10),
                (10, 9), (11, 9), (12, 9), (13, 9), (14, 9),
                (10, 11), (11, 11), (12, 11), (13, 11), (14, 11),
            ])
            .build()
        )
    return Scenario(
        name="friendly_probe_in_path",
        summary=(
            "Two RED cells at (11,10) and (13,10) with the agent's "
            "OWN probe sitting on the empty cell (12,10) between them. "
            "Stepping on the probe crushes it for zero score. Agent "
            "should detour via row 9 or row 11 (one extra step, probe "
            "preserved)."
        ),
        build=build,
        assertions=[
            # Don't step on your own probe.
            MustAvoid(
                cells=[(12, 10)],
                reason="own probe at (12,10) — stepping destroys it for 0 value",
                check_harvester=True,
                check_probes=False,
            ),
            # Still bank both REDs.
            MustTouch(cells=[(11, 10), (13, 10)]),
            MinExpectedValue(min_value=400),
            EndsWithPickup(),
        ],
        tags=("probes", "pathing", "self-collision"),
    )


# ---------------------------------------------------------------------------
# 14. Multi-hop seam — curving RED chain that needs direction changes
# ---------------------------------------------------------------------------
def multi_hop_seam() -> Scenario:
    """Six-cell RED seam shaped like an L. Agent must chain 4+ steps with two turns.

    Geometry::

        . . . . . .
        . R . . . .   ← row 9   col 12   value 130
        . R . . . .   ← row 10  col 12   value 160
        . R R R . .   ← row 11  cols 12/13/14   values 190/210/230
        . . . R . .   ← row 12  col 14   value 250

    Drop on (12, 9), step S, S, E, E, S harvests all six cells in
    one outing — but only if the agent reads the seam's shape from
    the world view and commits to the *curving* path. A naive
    "step toward best_red_visible[0]" implementation that doesn't
    plan ahead will likely chase (14, 12) directly (drop+step+step
    south then east) and miss the four cells along the upper arm.

    This scenario is the headline spatial-reasoning differentiator
    for the Phase 1 orchestrator A/B: the grid view should make the
    L-shape immediately apparent (``grid[10][12]``, ``grid[11][12]``,
    ``grid[11][13]``, ``grid[11][14]``, ``grid[12][14]``), while the
    flat list view forces the agent to mentally project the layout.
    """
    seam = [
        (12, 9, 130),
        (12, 10, 160),
        (12, 11, 190),
        (13, 11, 210),
        (14, 11, 230),
        (14, 12, 250),
    ]
    seam_cells = [(x, y) for x, y, _ in seam]

    def build():
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(seam)
            # Reveal the seam plus a one-cell halo so the agent can
            # see "what's adjacent" without anchoring probes inside
            # the chain itself.
            .grant_live_vision(
                seam_cells
                + [
                    (11, 9), (13, 9),
                    (11, 10), (13, 10),
                    (11, 11), (15, 11),
                    (13, 12), (15, 12), (14, 13),
                ],
            )
            .build()
        )
    return Scenario(
        name="multi_hop_seam",
        summary=(
            "Six-cell RED seam in an L shape: (12,9)→(12,10)→(12,11)→"
            "(13,11)→(14,11)→(14,12). Agent must read the curve and "
            "chain at least four hops through it; a straight-line "
            "approach toward the deepest cell at (14,12) misses the "
            "high-value upper arm entirely."
        ),
        build=build,
        assertions=[
            # Bank at least four of the six seam cells — that's the
            # minimum chain that proves the agent followed the curve
            # rather than diving straight at the deepest tile.
            HarvesterChainHits(region=seam_cells, min_hits=4),
            # 4 mid-value cells (~130 + 160 + 190 + 210 = 690) is the
            # floor; full traversal banks ~1170.
            MinExpectedValue(min_value=600),
            EndsWithPickup(),
        ],
        tags=("spatial", "pathing", "phase1"),
    )


# ---------------------------------------------------------------------------
# 15. Enemy intercept — avoid the enemy harvester's projected path
# ---------------------------------------------------------------------------
def enemy_intercept() -> Scenario:
    """Enemy harvester moving east; rich RED is ahead — approach from the side, not the seam.

    Setup::

        . . . . . . . . . . .
        . . . . . . . . . . .   ← row 9
        T T E . R R R . . . .   ← row 10  (T=enemy trail, E=enemy, R=red)
        . . . . . . . . . . .   ← row 11
                       cols: 8 9 10 11 12 13 14

    The enemy harvester sat at (10, 10) tonight with a fresh trail
    coming in from (8, 10) and (9, 10). Their natural next step is
    (11, 10) — directly into the RED seam. A competent agent does
    NOT drop on (11, 10) (collision next night) and does NOT step
    east through (11, 10) on its way to (12, 10). Approaching the
    seam from the north (drop @ (12, 9) → step (12, 10) → (13, 10))
    or south (drop @ (12, 11)) banks the RED without sharing a
    cell with the enemy's projected step.
    """
    seam = [(12, 10, 220), (13, 10, 230), (14, 10, 210)]
    seam_cells = [(x, y) for x, y, _ in seam]

    def build():
        builder = (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(seam)
            # Live LOS over the seam and the rows above / below so
            # the agent can see the obvious side-approach cells.
            .grant_live_vision(
                seam_cells
                + [
                    (10, 10), (11, 10),
                    (11, 9), (12, 9), (13, 9), (14, 9),
                    (11, 11), (12, 11), (13, 11), (14, 11),
                ],
            )
            # Enemy harvester finished last night at (10, 10) — visible
            # via LOS we just granted. Their trail records the east-
            # bound run that brought them here.
            .place_enemy_harvester(at=(10, 10))
            .enemy_trail([(8, 10), (9, 10), (10, 10)], day=3)
        )
        return builder.build()

    return Scenario(
        name="enemy_intercept",
        summary=(
            "Enemy harvester sits at (10,10) with a fresh trail east "
            "from (8,10) and (9,10) — their next step is almost "
            "certainly (11,10). RED seam at (12-14, 10). Agent must "
            "approach the seam from the north or south (not via "
            "(11,10)) and avoid sharing the enemy's projected path."
        ),
        build=build,
        assertions=[
            # Enemy currently at (10,10); (11,10) is their projected
            # next step. Both are illegal collision risks.
            MustAvoid(
                cells=[(10, 10), (11, 10)],
                reason=(
                    "enemy harvester at (10,10) trailing east; "
                    "(11,10) is the obvious next step they'll take"
                ),
                check_harvester=True,
                check_probes=False,
            ),
            # Still bank from the seam — the play isn't to skip the
            # turn, it's to find the safe approach.
            HarvesterChainHits(region=seam_cells, min_hits=2),
            EndsWithPickup(),
        ],
        tags=("opponent", "spatial", "pathing", "phase1"),
    )


# ---------------------------------------------------------------------------
# 16. Two seams, choose one — reachable vs out-of-budget
# ---------------------------------------------------------------------------
def two_seams_choose_one() -> Scenario:
    """Surface harvester between two equal-value seams; only one is reachable in budget.

    Geometry::

        . . . . . . . . . . . . . . . . . . . . .
        R R R . . . . . . H . . . . R R R . . . .
        . . . . . . . . . . . . . . . . . . . . .
                  cols 1 2 3 ... 12 13 14 ...  16 17 18

    Surface harvester at (12, 13). Both seams contain three RED
    cells of value 200 (total 600 per seam, identical). The west
    seam starts 8 steps away (Manhattan distance to its nearest
    cell at (4, 13) is 8); the east seam starts 4 steps away (to
    (16, 13)). The per-harvester step cap is 5 — so the agent
    can reach two cells of the east seam in budget but cannot
    touch the west seam at all in this outing. A competent
    spatial reasoner picks the reachable side. The heuristic
    baseline picks closest-first which trivially does the right
    thing here; the eval value is asserting that a *Cortex* agent
    that reasons about budget arrives at the same answer.
    """
    seam_west = [(2, 13, 200), (3, 13, 200), (4, 13, 200)]
    seam_east = [(16, 13, 200), (17, 13, 200), (18, 13, 200)]
    seam_west_cells = [(x, y) for x, y, _ in seam_west]
    seam_east_cells = [(x, y) for x, y, _ in seam_east]
    # Clear the noise generator's RED out of everything the harvester
    # could reach or see, leaving only the two placed seams. Otherwise
    # this measures seed luck rather than the spatial reasoning it is
    # named for.
    #
    # This used to clear radius-2 disks around the harvester and each
    # seam cell, which left the corridor BETWEEN them speckled — about
    # thirty-five stray RED cells at seed 42, most of them trace. A
    # closest-first heuristic walks the nearest of those instead of
    # committing to either seam, and scores nothing: the run that
    # exposed this chased purity 9, 12 and 2 three cells from the start.
    #
    # The rule is now stated in terms of the thing that matters — the
    # step budget. Any cell the unit could plausibly walk to must be a
    # placed seam cell or empty, so the only RED decision available is
    # the one the scenario is asking about. The radius is the 5-step
    # cap plus margin, so a cell just outside the budget cannot bait it
    # either.
    harvester_at = (12, 13)
    reach = (HARVESTER_HOLD_CAPACITY - 1) + 4
    placed = set(seam_west_cells) | set(seam_east_cells)
    corridor_clear: List[Tuple[int, int]] = []
    for yy in range(28):
        for xx in range(40):
            if (xx, yy) in placed:
                continue
            near_start = (
                abs(xx - harvester_at[0]) + abs(yy - harvester_at[1]) <= reach
            )
            near_seam = any(
                (xx - sx) ** 2 + (yy - sy) ** 2 <= 4
                for sx, sy in placed
            )
            if near_start or near_seam:
                corridor_clear.append((xx, yy))

    def build():
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            # Surface harvester at the centre with empty cargo and
            # a full 5-step budget for the night.
            .place_harvester(
                "harvester_p1", at=(12, 13), state="surface", cargo=0,
            )
            .reveal_empty(corridor_clear)
            .reveal_red(seam_west + seam_east)
            # Vision over both seams; the harvester's own plus-vision
            # covers the immediate surroundings. We do NOT light up
            # the long corridor — it would also light up noise REDs.
            .grant_live_vision(seam_west_cells + seam_east_cells)
            .build()
        )

    return Scenario(
        name="two_seams_choose_one",
        summary=(
            "Surface harvester at (12,13) between two equal-value "
            "RED seams: west seam at cols 2-4 (8 steps away — out "
            "of the 5-step budget), east seam at cols 16-18 (4 "
            "steps to the first cell — fits comfortably). Agent "
            "must commit to the reachable seam and not start chasing "
            "the unreachable one."
        ),
        build=build,
        assertions=[
            # West seam is unreachable in one outing — stepping
            # toward it is a wasted budget.
            MustAvoid(
                cells=seam_west_cells,
                reason="west seam at distance 8 — out of 5-step budget",
                check_harvester=True,
                check_probes=False,
            ),
            # Touch at least one cell of the east seam — that's the
            # "commit to the reachable seam" win condition.
            HarvesterChainHits(region=seam_east_cells, min_hits=1),
            EndsWithPickup(),
        ],
        tags=("spatial", "budget", "phase1"),
    )


# ---------------------------------------------------------------------------
# Simple full-size single-drop probes (PILOT world-processing milestone).
# A full 40x28 board, mostly fog, ONE harvester, ONE clear RED seam in live
# vision. The point is to confirm an agent can read a complete grid and logic
# out a single good harvester drop + harvest chain — nothing fancier.
# ---------------------------------------------------------------------------
def solo_drop_orbit() -> Scenario:
    """Full-size board, orbital harvester, one obvious RED seam in view."""
    def build():
        # A compact, high-value horizontal seam around the board centre.
        seam = [(20, 14, 200), (21, 14, 210), (22, 14, 190), (23, 14, 170)]
        return (
            WorldBuilder(seed=11, day=3, season_day_cap=7, width=40, height=28)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(seam)
            .grant_live_vision([(x, y) for x, y, _ in seam])
            .build()
        )
    return Scenario(
        name="solo_drop_orbit",
        summary=(
            "Full-size 40x28 board, mostly fog, harvester in orbit, one clear "
            "RED seam (values 170-210) at (20-23,14). Agent should drop "
            "adjacent, harvest the seam, and pick up."
        ),
        build=build,
        assertions=[
            # Dropping adjacent + harvesting two of the four cells clears 200.
            MinExpectedValue(min_value=200),
            EndsWithPickup(),
            NoSyntheticGreenSteps(),
        ],
        tags=("pilot", "drop", "fullgrid"),
    )


def solo_drop_surface() -> Scenario:
    """Full-size board, harvester already surfaced just west of a RED seam."""
    def build():
        seam = [(20, 14, 200), (21, 14, 210), (22, 14, 190), (23, 14, 170)]
        return (
            WorldBuilder(seed=12, day=3, season_day_cap=7, width=40, height=28)
            .place_harvester("harvester_p1", at=(19, 14), state="surface")
            .reveal_red(seam)
            .grant_live_vision([(x, y) for x, y, _ in seam] + [(19, 14)])
            .build()
        )
    return Scenario(
        name="solo_drop_surface",
        summary=(
            "Full-size 40x28 board, harvester already on the surface at "
            "(19,14) one step west of a RED seam at (20-23,14). Agent should "
            "step east through the seam and pick up."
        ),
        build=build,
        assertions=[
            HarvesterChainHits(
                region=[(20, 14), (21, 14), (22, 14), (23, 14)],
                min_hits=2,
            ),
            EndsWithPickup(),
            NoSyntheticGreenSteps(),
        ],
        tags=("pilot", "drop", "fullgrid"),
    )


# ---------------------------------------------------------------------------
# 19. PURE-cell priority — pure cluster beats vein cluster
# ---------------------------------------------------------------------------
def pure_cluster_priority() -> Scenario:
    """A pure (purity 255) cluster AND a vein (~80) cluster both visible.

    Pure cells bank 255 each (vs ~80 for vein) — a 3-4x value lift per
    cell. Recommended_policy MUST seed the chain at the pure cluster.
    Regression for the day-6 bug where PILOT_V2 picked the vein cluster.
    """
    def build():
        # Vein cluster on the left (Manhattan ~10 from centre).
        vein = [(8, 14, 80), (9, 14, 75), (8, 15, 70)]
        # Pure cluster on the right.
        pure = [(28, 14, 255), (29, 14, 255), (28, 15, 255)]
        all_cells = [(x, y) for x, y, _ in vein + pure]
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(vein + pure)
            .grant_live_vision(all_cells)
            .build()
        )
    return Scenario(
        name="pure_cluster_priority",
        summary=(
            "Two RED clusters visible: vein (purity ~75) at (8-9, 14-15) and "
            "pure (purity 255) at (28-29, 14-15). Agent MUST chain through "
            "the pure cluster — pure cells bank 3-4x more value per slot."
        ),
        build=build,
        assertions=[
            MinPureCellsTouched(min_pure=2, purity_threshold=255),
            MinExpectedValue(min_value=500),
            EndsWithPickup(),
        ],
        tags=("strategy", "pure", "regression"),
    )


# ---------------------------------------------------------------------------
# 20. Multi-harvester sibling claim — two harvesters, two clusters
# ---------------------------------------------------------------------------
def two_harvesters_distinct_targets() -> Scenario:
    """Two orbit harvesters + two separated RED clusters. Each harvester
    should target a DIFFERENT cluster — not stack on the same one.

    Regression for the day-7 bug where both harvesters dropped at the
    same cell and the second banked zero (synthetic green).
    """
    def build():
        cluster_a = [(6, 14, 200), (7, 14, 190), (6, 15, 180)]
        cluster_b = [(30, 14, 200), (31, 14, 190), (30, 15, 180)]
        all_cells = [(x, y) for x, y, _ in cluster_a + cluster_b]
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
            .reveal_red(cluster_a + cluster_b)
            .grant_live_vision(all_cells)
            .build()
        )
    return Scenario(
        name="two_harvesters_distinct_targets",
        summary=(
            "Two orbit harvesters and two RED clusters 24 cells apart. "
            "Each harvester must drop on a different cluster (sibling-claim "
            "doctrine). Both stacking on one cluster is the regression bug."
        ),
        build=build,
        assertions=[
            DropsInDifferentRegions(min_manhattan=10),
            EndsWithPickup(),
        ],
        tags=("multi-harvester", "sibling-claim", "regression"),
    )


# ---------------------------------------------------------------------------
# 21. Echo-only — no live coverage, agent must not blind-drop
# ---------------------------------------------------------------------------
def echo_only_no_blind_drop() -> Scenario:
    """RED visible via STALE echo only (no live probe nearby). Under
    ``live_only`` drop mode (the default) the engine refuses harvester
    drops on echo cells. The agent must not queue such a drop.

    Regression for the day-7 bug where PILOT_V2 emitted a drop on (4,13)
    that had no live sensor beacon — the engine rejected it and the
    entire chain failed.
    """
    echo_cells = [(20, 14), (21, 14), (20, 15), (21, 15)]
    def build():
        # Reveal RED at the echo cells but do NOT grant live vision —
        # the visibility is purely from a stale memory tile.
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red([(20, 14, 200), (21, 14, 200), (20, 15, 200), (21, 15, 200)])
            .reveal_to_player(echo_cells, player="p1", stale=True)
            .build()
        )
    return Scenario(
        name="echo_only_no_blind_drop",
        summary=(
            "Echo-only RED visible at (20-21, 14-15). No live probe in range. "
            "Under live_only drop mode the engine refuses drops there. Agent "
            "must either probe FIRST (to seed live vision) or leave the "
            "harvester in orbit — NOT queue a doomed drop."
        ),
        build=build,
        assertions=[
            NoDropAtCells(
                cells=echo_cells,
                reason="echo-only cells — no live beacon, drop is illegal under live_only",
            ),
        ],
        tags=("drop-legality", "live-only", "regression"),
    )


# ---------------------------------------------------------------------------
# 22. EMP threat — front-load short chains
# ---------------------------------------------------------------------------
def emp_threat_front_load() -> Scenario:
    """Pure cluster reachable in a SHORT chain. Agent should front-load
    so the chain finishes before an EMP cloud could land mid-night.

    The doctrine is: under EMP threat (D-EMP-1) prefer chains with
    action_count ≤ 6. This scenario doesn't simulate the EMP signal
    itself — it just asserts the agent picks a fast chain, which a
    pure-priority + chain-length-aware compiler does naturally.
    """
    def build():
        pure_seam = [(20, 14, 255), (21, 14, 255), (20, 15, 240)]
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .reveal_red(pure_seam)
            .grant_live_vision([(x, y) for x, y, _ in pure_seam])
            .build()
        )
    return Scenario(
        name="emp_threat_front_load",
        summary=(
            "Pure cluster at (20-21, 14-15), all in live LOS. Agent should "
            "drop adjacent, walk a SHORT chain (≤6 actions), and pickup. "
            "Long chains leak score under EMP threat (D-EMP-1 doctrine)."
        ),
        build=build,
        assertions=[
            HarvesterChainActionCount(max_actions=6),
            MinPureCellsTouched(min_pure=1, purity_threshold=240),
            EndsWithPickup(),
        ],
        tags=("emp", "front-load"),
    )


# ---------------------------------------------------------------------------
# 23. Posture — when behind with low visible value, probe aggressively
# ---------------------------------------------------------------------------
def posture_aggressive_probes() -> Scenario:
    """Score gap heavily against p1 + little visible RED + mid-season.
    Aggressive posture says: deploy more probes, expand vision.

    Asserts ≥ 2 probes and they're spread across the map.
    """
    def build():
        # A tiny vein cluster — not enough to lift the score-gap. The
        # rest of the map is fog, so the obvious response is to probe.
        small_seam = [(20, 14, 60)]
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .set_score(p1=80, p2=420)  # p1 trailing by 340
            .reveal_red(small_seam)
            .grant_live_vision([(20, 14)])
            .build()
        )
    return Scenario(
        name="posture_aggressive_probes",
        summary=(
            "Day 3, p1 trailing 80 vs 420, only a single low-value RED "
            "visible. Aggressive posture: spend probes to expand vision "
            "rather than burn the night on a 60-point chain."
        ),
        build=build,
        assertions=[
            ProbeCount(min=2, max=3),
            ProbesInDistinctQuadrants(min_quadrants=2),
        ],
        tags=("posture", "aggressive", "behind"),
    )


# ---------------------------------------------------------------------------
# 24. Pure-under-pressure — opponent harvesters + probe contesting a pure
# ---------------------------------------------------------------------------
def pure_under_enemy_pressure() -> Scenario:
    """Pure cluster center contested by opponent: 2 enemy harvesters
    (one orbital threatening drop, one surface threatening hot-drop)
    plus an enemy probe granting them vision. Agent has a probe in
    range, a harvester, and BLUE/credits enough to fire an EMP.

    Multiple solution paths exist:
    * Drop adjacent + walk 2 cells onto the pure + pickup (3-4 actions,
      banks before any enemy hot-drop at hour 4-5).
    * Pre-emptively crush the enemy probe (denies their vision, lets
      our harvester land safely).
    * Hot-drop on the enemy probe cell to deny + harvest in one motion.

    The non-negotiable win condition is: the harvester chain TOUCHES
    the pure (255) cell AND ends with pickup AND the chain is short
    enough to beat any enemy hot-drop (≤ 5 actions).
    """
    def build():
        # Pure in the middle, RED veins around it.
        pure_centre = (20, 14, 255)
        red_ring = [
            (19, 14, 180), (21, 14, 200),
            (20, 13, 170), (20, 15, 190),
            (19, 13, 150), (21, 15, 160),
        ]
        all_red_cells = [(x, y) for x, y, _ in [pure_centre] + red_ring]
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            # Pre-armed EMP + buffer BLUE/credits so the agent has full
            # combat options (race vs pre-empt vs counter-build).
            .give_weapon_stock(emp=1)
            .give_blue_purity(purity_total=300)
            .reveal_red([pure_centre] + red_ring)
            # Our friendly probe gives live vision over the cluster.
            .grant_live_vision(all_red_cells, player="p1")
            # Enemy probe near the cluster — gives p2 vision on the
            # contested cells too (they see what we see here).
            .place_enemy_probe(at=(23, 14))
            # Stage the publicity echo per §3.15 so we ACTUALLY see the
            # probe (not just that the entity exists).
            .enemy_probe_launched(at=(23, 14), day=3)
            # Enemy harvester #1 orbital — threatens a drop hour 1.
            .place_enemy_harvester("harvester_p2", state="orbit")
            # Enemy harvester #2 surface — close enough to hot-drop in.
            .place_enemy_harvester(
                "harvester_p2_2", at=(24, 14), state="surface",
            )
            .build()
        )
    return Scenario(
        name="pure_under_enemy_pressure",
        summary=(
            "Pure cell at (20,14) contested by 2 enemy harvesters + an "
            "enemy probe at (23,14). Agent has live vision and must "
            "BANK THE PURE in a bounded chain (≤7 actions, ends in "
            "pickup) before any enemy hot-drop. EMP, blind-and-harvest, "
            "or race-to-pickup are all valid — touching the pure is "
            "non-negotiable."
        ),
        build=build,
        assertions=[
            MinPureCellsTouched(min_pure=1, purity_threshold=255),
            # 7 = drop + 5 steps + pickup, which is the engine's hard
            # cap on a per-harvester chain. A chain that touches the
            # pure plus surrounding veins is strictly better than a
            # shorter chain that grabs the pure alone.
            HarvesterChainActionCount(max_actions=7),
            EndsWithPickup(),
        ],
        tags=("contested", "pure", "emp", "endgame"),
    )


# ---------------------------------------------------------------------------
# emp_denies_corridor_walk
# ---------------------------------------------------------------------------
def emp_denies_corridor_walk() -> Scenario:
    """EMP-must-fire scenario: TWO enemy probes light a walking corridor
    that a supersede alone can't shut down. Only an EMP salvo denies
    both probes AND freezes the walking harvester.

    Board layout::

        MY CLUSTER  →  PROBE_A  →  PROBE_B  →  ENEMY HARVESTER (marching west)
        (17-19, 14)   (24, 14)    (28, 14)    (30, 14)

    Enemy strategy the scenario models (implicit):
      * probe_A gives them vision on the corridor east of our cluster.
      * probe_B gives them vision further east — enough to hot-drop a
        NEW harvester at (28, 14) TONIGHT.
      * Their surface harvester at (30, 14) walks 5 cells west toward
        our cluster this night.
      * With 2 probes lighting the corridor, they can march multiple
        harvesters west over subsequent nights.

    Why supersede FAILS:
      * A probe supersede on probe_A destroys ONE probe. Probe_B still
        illuminates the corridor and enables the enemy hot-drop.
      * Supersede on probe_B does the mirror thing — kills B, A survives.
      * Even a double supersede would eat both our probe slots this
        turn and doesn't freeze the SURFACE harvester already marching
        toward us.

    Why EMP salvo WINS:
      * ONE launch = THREE missiles + THREE radius-2 clouds for 8 hours.
      * A salvo targeting (24, 14) + (28, 14) + (30, 14) [or similar]:
        - Cloud 1 covers probe_A → freezes it, denies drops in its disk.
        - Cloud 2 covers probe_B → freezes it, denies hot-drop THIS
          turn AND blocks the walking corridor.
        - Cloud 3 covers the surface harvester → freezes its steps,
          preventing the march west.
      * Total effect: two enemy probes disabled, one enemy harvester
        frozen mid-march, corridor closed for 8 hours. Supersede
        can't reproduce this multi-asset denial.

    The scenario asserts:
      * `PolicyContainsAction(action="emp_launch")` — agent MUST fire
        an EMP salvo (not just a supersede).
      * `EmpSalvoCoversCell(target=(24, 14))` — salvo covers probe A.
      * `EmpSalvoCoversCell(target=(28, 14))` — salvo covers probe B
        AND/OR the walking corridor.
      * `EndsWithPickup()` — any harvester deployed must pickup.
    """
    def build():
        # Our RED cluster — safely west of the enemy corridor.
        pure_centre = (18, 14, 255)
        red_cluster = [
            pure_centre,
            (19, 14, 200),   # mass
            (17, 14, 170),   # vein
        ]
        cluster_cells = [(x, y) for x, y, _ in red_cluster]
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            # Pre-arm EMP and BLUE/credits so all combat options are
            # affordable. can_fire=true via `fires_from_stock`.
            .give_weapon_stock(emp=1)
            .give_blue_purity(purity_total=400)
            .reveal_red(red_cluster)
            # Live vision covers our cluster (P0-like probe already placed).
            .grant_live_vision(cluster_cells, player="p1")
            # ── ENEMY CORRIDOR ────────────────────────────────────────
            # Probe A lights the cell east of our cluster + reaches
            # into corridor. Its disk radius-4 covers (20-28, 10-18).
            .place_enemy_probe(at=(24, 14))
            .enemy_probe_launched(at=(24, 14), day=3)
            # Probe B is 4 cells east of A, lighting the outer corridor
            # + the enemy's surface harvester. Its disk covers
            # (24-32, 10-18) — overlaps with A's disk at the join.
            .place_enemy_probe(at=(28, 14))
            .enemy_probe_launched(at=(28, 14), day=3)
            # Enemy surface harvester marching west toward our cluster.
            # 5-step cap per night = reaches (25, 14) this turn; with
            # a second harvester hot-dropped at (28, 14) via probe B,
            # they push further west over subsequent nights.
            .place_enemy_harvester(
                "harvester_p2", at=(30, 14), state="surface",
            )
            # Enemy orbital harvester — threatens the hot-drop into
            # probe B's disk to reinforce the corridor.
            .place_enemy_harvester("harvester_p2_2", state="orbit")
            .build()
        )
    return Scenario(
        name="emp_denies_corridor_walk",
        summary=(
            "TWO enemy probes at (24,14) and (28,14) light a walking "
            "corridor for a marching surface harvester at (30,14). "
            "Supersede can only kill one probe — the other keeps the "
            "corridor open. Only an EMP salvo denies both probes AND "
            "freezes the marching harvester. Agent has 1 EMP in stock, "
            "400 BLUE, plenty of credits. Must fire the EMP with the "
            "salvo covering both probe cells."
        ),
        build=build,
        assertions=[
            # The compose decision under test: MUST fire an EMP.
            PolicyContainsAction(action="emp_launch", min_count=1),
            # The salvo must tile with intent — cover BOTH probe cells,
            # not just one (which would be equivalent to a supersede).
            EmpSalvoCoversCell(target=(24, 14), radius=2),
            EmpSalvoCoversCell(target=(28, 14), radius=2),
            EndsWithPickup(),
        ],
        tags=("combat", "emp", "corridor", "denial"),
    )


# ---------------------------------------------------------------------------
# harvest_then_emp_composed
# ---------------------------------------------------------------------------
def harvest_then_emp_composed() -> Scenario:
    """The workhorse composition: harvest chain FIRST, EMP fires AFTER
    pickup. Every slot earns — revenue then denial.

    Board::

        MY CLUSTER (17-19, 14)   ENEMY PROBES (24,14) (28,14)   ENEMY HARV (30,14)

    Expected default composition (from `recommended_policy`):
      drop → step ×5 → pickup → emp_launch → probe

    This scenario asserts the queue IS composed — not just "EMP fires"
    but "EMP fires AFTER pickup". Verifies the temporal order the
    harness now emits by default. The pickup-before-emp order means
    our harvester banks its cargo BEFORE any cells freeze; the EMP
    then denies enemy corridor for 8 hours.
    """
    def build():
        pure_centre = (18, 14, 255)
        red_cluster = [pure_centre, (19, 14, 200), (17, 14, 170)]
        cluster_cells = [(x, y) for x, y, _ in red_cluster]
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .give_weapon_stock(emp=1)
            .give_blue_purity(purity_total=400)
            .reveal_red(red_cluster)
            .grant_live_vision(cluster_cells, player="p1")
            .place_enemy_probe(at=(24, 14))
            .enemy_probe_launched(at=(24, 14), day=3)
            .place_enemy_probe(at=(28, 14))
            .enemy_probe_launched(at=(28, 14), day=3)
            .place_enemy_harvester(
                "harvester_p2", at=(30, 14), state="surface",
            )
            .place_enemy_harvester("harvester_p2_2", state="orbit")
            .build()
        )
    return Scenario(
        name="harvest_then_emp_composed",
        summary=(
            "Harvest chain executes first, banks the pure cell, then "
            "the EMP fires in a later slot to deny the enemy corridor "
            "for 8 hours. Tests that recommended_policy composes "
            "harvest AND emp in that temporal order."
        ),
        build=build,
        assertions=[
            MinPureCellsTouched(min_pure=1, purity_threshold=255),
            PolicyContainsAction(action="emp_launch", min_count=1),
            # The heart of the assertion: pickup completes BEFORE EMP.
            # Cargo safely banked, THEN cells freeze.
            ActionOrder(before="pickup", after="emp_launch"),
            EndsWithPickup(),
        ],
        tags=("combat", "emp", "composition", "harvest-then-emp"),
    )


# ---------------------------------------------------------------------------
# pickup_early_then_emp_short_chain
# ---------------------------------------------------------------------------
def pickup_early_then_emp_short_chain() -> Scenario:
    """Late-season short chain + early pickup + EMP seals the lead.

    Day 6 of 7. We're ahead. Only a small 2-cell RED cluster is visible.
    Enemy has 2 probes lighting a corridor. Right move: bank the small
    chain quickly, pickup at hour 4, then EMP hour 5 to prevent enemy
    from reaching us with anything meaningful in the final 2 nights.

    Emphasizes that a SHORT harvest chain (3 actions: drop + 1 step +
    pickup) still leaves 15+ spare slots for combat. Revenue and
    denial are complementary in every slot — even end-game short-chain
    turns should compose both.
    """
    def build():
        # Two-cell mass cluster: one is pure, one is mass.
        red = [(18, 14, 255), (19, 14, 200)]
        cells = [(x, y) for x, y, _ in red]
        # Scrub trace RED cells the seed=42 world generates around the
        # cluster — without this the walker greedily bridges to the
        # traces and inflates chain length past the 5-action budget.
        trace_scrub = [
            (14, 14), (15, 14), (15, 15), (15, 16), (16, 11),
            (16, 14), (17, 13), (17, 14), (20, 13), (20, 15),
            (18, 13), (18, 15), (19, 13), (19, 15),
        ]
        return (
            WorldBuilder(seed=42, day=6, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .give_weapon_stock(emp=1)
            .give_blue_purity(purity_total=400)
            .reveal_empty(trace_scrub)
            .reveal_red(red)
            .grant_live_vision(cells, player="p1")
            .place_enemy_probe(at=(24, 14))
            .enemy_probe_launched(at=(24, 14), day=5)
            .place_enemy_probe(at=(28, 14))
            .enemy_probe_launched(at=(28, 14), day=5)
            .place_enemy_harvester(
                "harvester_p2", at=(30, 14), state="surface",
            )
            .build()
        )
    return Scenario(
        name="pickup_early_then_emp_short_chain",
        summary=(
            "Day 6 late season. Small 2-cell RED cluster + 2 enemy "
            "probes + walking harvester. Right move: short harvest "
            "chain (3-4 actions) with early pickup, then EMP fires "
            "later slot to seal the lead. Both actions in one turn."
        ),
        build=build,
        assertions=[
            MinPureCellsTouched(min_pure=1, purity_threshold=255),
            PolicyContainsAction(action="emp_launch", min_count=1),
            ActionOrder(before="pickup", after="emp_launch"),
            # Chain is deliberately short — 4 actions max (drop+2step+pickup).
            HarvesterChainActionCount(max_actions=5),
            EndsWithPickup(),
        ],
        tags=("combat", "emp", "composition", "late-season", "short-chain"),
    )


# ---------------------------------------------------------------------------
# two_harvesters_plus_emp_denial
# ---------------------------------------------------------------------------
def two_harvesters_plus_emp_denial() -> Scenario:
    """Full-budget turn: TWO harvesters both deploy + EMP fires.

    Two separate RED clusters (west and east). Two orbital harvesters
    can each take one. Enemy has probes covering a corridor between
    them. Right composition: H1 harvests west cluster, H2 harvests
    east cluster (through their probe disks — dangerous), EMP fires
    to freeze enemy activity for the rest of the night. Full 20-21
    slot orchestration in a single turn.

    Showcases that even with two full harvest chains eating ~14 slots,
    there's room for EMP + probes. Revenue is maximized AND denial
    fires — the ideal Sea-of-Colours turn.
    """
    def build():
        # West cluster (safe side).
        west = [(10, 14, 255), (11, 14, 200), (10, 13, 170)]
        # East cluster (enemy-adjacent).
        east = [(24, 14, 255), (25, 14, 200)]
        all_red = west + east
        all_cells = [(x, y) for x, y, _ in all_red]
        return (
            WorldBuilder(seed=42, day=5, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
            .give_weapon_stock(emp=1)
            .give_blue_purity(purity_total=400)
            .reveal_red(all_red)
            .grant_live_vision(all_cells, player="p1")
            # Enemy corridor bracketing the east cluster.
            .place_enemy_probe(at=(28, 14))
            .enemy_probe_launched(at=(28, 14), day=4)
            .place_enemy_probe(at=(32, 14))
            .enemy_probe_launched(at=(32, 14), day=4)
            .place_enemy_harvester(
                "harvester_p2", at=(34, 14), state="surface",
            )
            .build()
        )
    return Scenario(
        name="two_harvesters_plus_emp_denial",
        summary=(
            "Two orbital harvesters + two RED clusters + enemy corridor. "
            "Full-budget turn: both harvesters deploy, EMP fires denial "
            "on enemy corridor. Total actions ≥ 15. Revenue AND denial "
            "in one turn."
        ),
        build=build,
        assertions=[
            # Both pure cells touched — each harvester banks one.
            MinPureCellsTouched(min_pure=2, purity_threshold=255),
            PolicyContainsAction(action="emp_launch", min_count=1),
            # Both harvesters deploy (2 drops).
            PolicyContainsAction(action="drop", min_count=2),
            EndsWithPickup(),
        ],
        tags=("combat", "emp", "composition", "multi-harvester", "full-budget"),
    )


# ---------------------------------------------------------------------------
# emp_covers_corridor_during_harvest
# ---------------------------------------------------------------------------
def emp_covers_corridor_during_harvest() -> Scenario:
    """EMP clouds enemy corridor on one side while harvester works
    the OTHER side, safely out of any cloud cell.

    Board layout::

        MY CLUSTER (5-7, 14)      ⋯      ENEMY CORRIDOR (24-30, 14)

    Our harvester works cells (5,14)–(7,14) — WAY west of the enemy
    corridor. EMP fires east at (24,14),(28,14),(30,14) — clouds are
    radius-2 (max reach ~32 east). Our harvester at column 5-7 is
    NEVER in a resulting cloud, so it operates entirely safely while
    the EMP does its denial work.

    Showcases geographic separation of harvest and denial: they don't
    have to interact spatially, just share the same turn's action
    budget. Composition without conflict.
    """
    def build():
        red = [(5, 14, 255), (6, 14, 200), (7, 14, 170)]
        cells = [(x, y) for x, y, _ in red]
        return (
            WorldBuilder(seed=42, day=4, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .give_weapon_stock(emp=1)
            .give_blue_purity(purity_total=400)
            .reveal_red(red)
            .grant_live_vision(cells, player="p1")
            .place_enemy_probe(at=(24, 14))
            .enemy_probe_launched(at=(24, 14), day=3)
            .place_enemy_probe(at=(28, 14))
            .enemy_probe_launched(at=(28, 14), day=3)
            .place_enemy_harvester(
                "harvester_p2", at=(30, 14), state="surface",
            )
            .build()
        )
    return Scenario(
        name="emp_covers_corridor_during_harvest",
        summary=(
            "Our RED cluster at (5-7, 14) is geographically separate "
            "from the enemy corridor at (24-30, 14). Composition: "
            "harvester works the west cluster while EMP fires east on "
            "the enemy corridor. Zero spatial conflict; both actions "
            "score their full value."
        ),
        build=build,
        assertions=[
            MinPureCellsTouched(min_pure=1, purity_threshold=255),
            PolicyContainsAction(action="emp_launch", min_count=1),
            # EMP salvo lands in the east — assert it covers the enemy
            # corridor probes, NOT our western harvest cluster.
            EmpSalvoCoversCell(target=(24, 14), radius=2),
            EmpSalvoCoversCell(target=(28, 14), radius=2),
            EndsWithPickup(),
        ],
        tags=("combat", "emp", "composition", "geographic-separation"),
    )


# ---------------------------------------------------------------------------
# Pilot-comprehension scenarios (v1.9)
#
# These scenarios verify the agent *understands* what it's seeing — the
# rationale + plan label are asserted alongside the policy queue. Each
# targets one doctrinal trigger the pilot_v4 harness is supposed to
# recognise but has empirically drifted on:
#
#   * REDSIGN beacons (§4.11)     — chase the seam, don't ignore it
#   * public pure witness         — pickup before the rival races over
#   * chaff-capable rival         — don't blind-drop into a chaff jam
#   * EMP cloud                   — don't land a drop inside a live cloud
#   * hoard shippability          — ship first when the hoard is heavy
#   * unaffordable orbit build    — refine/ship instead of pretending
#   * 2 harvesters + heavy blue   — blind-drop for area denial
#   * contested pure w/ own EMP   — deny the rival AND harvest
#   * chaff-defence orbit         — build chaff when the rival is emp-heavy
#   * orbit repair + ship         — repair BEFORE ship when both are needed
# ---------------------------------------------------------------------------


def redsign_seen_last_nox() -> Scenario:
    """A REDSIGN beacon appeared last night. Race to the seam."""
    def build():
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .clear_visibility("p1")
            .with_redsign(
                center=(10, 10),
                cells=[
                    (9, 10, 0.85), (10, 10, 0.95), (11, 10, 0.85),
                    (10, 9, 0.75), (10, 11, 0.75),
                ],
                minted_on_day=2, minted_by="p2", hour=22,
            )
            .build()
        )
    return Scenario(
        name="redsign_seen_last_nox",
        summary=(
            "Day 3 planning. A public REDSIGN beacon smears the pure seam "
            "around (10,10) — minted last night. Harvester still in orbit. "
            "Pilot should reason about the beacon and race there."
        ),
        build=build,
        assertions=[
            RationaleMentions(needles=("redsign", "red-sign", "seam")),
            PlanLabelIn(labels=(
                "chase_redsign", "redsign", "harvest_seam", "seam",
                "probe_seam", "harvest_pure",
                # v1.9 — probe_seed is a legitimate response to a fuzzy
                # redsign (probe first to lock LOS before committing a
                # harvester). Baseline v2 showed the pilot correctly
                # picked probe_seed under redsign and the assertion
                # falsely flagged it.
                "probe_seed",
                # harvest_mixed is doctrinally correct when the redsign
                # is present but the pure core isn't in immediate LOS.
                "harvest_mixed",
            )),
            PlanMatchesMaterialisedVerb(),
            NoSilentSelectionDrops(),
        ],
        tags=("comprehension", "redsign"),
    )


def i_witnessed_pure() -> Scenario:
    """My own harvester just walked pure cells — public witness. Pick up now."""
    def build():
        pure_cells = [(15, 15, 240), (14, 15, 250), (15, 14, 245), (13, 15, 230)]
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .reveal_red(pure_cells)
            .grant_live_vision([(x, y) for x, y, _ in pure_cells])
            .place_harvester("harvester_p1", state="surface", at=(15, 15), cargo=3)
            .build()
        )
    return Scenario(
        name="i_witnessed_pure",
        summary=(
            "Harvester sits on pure (15,15) with cargo=3 after crossing "
            "three more pure cells this walk. Trail is public so the "
            "rival will race. Pilot should pick up and NOT deploy a "
            "second harvester that further advertises the seam."
        ),
        build=build,
        assertions=[
            EndsWithPickup(),
            PolicyContainsAction(action="pickup"),
            NoHarvesterDeployment(),
            RationaleMentions(needles=("pickup", "cargo", "pure", "witness")),
            NoSilentSelectionDrops(),
        ],
        tags=("comprehension", "pickup", "public_witness"),
    )


def chaff_capable_rival_telegraphed() -> Scenario:
    """Rival fired chaff last night; likely more in stock. Probe, don't drop."""
    def build():
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .clear_visibility("p1")
            # Rival burnt one chaff yesterday — public per §5.1
            .record_last_night_combat(
                type="chaff", owner="p2", hours=[20, 21, 22],
                day=2,
            )
            # ...and jammed our defunct probe (proves the arsenal is live)
            .record_last_night_combat(
                type="chaff_jam", owner="p2", hours=[20], day=2,
                victim="p1", unit="probe_p1_1",
            )
            .build()
        )
    return Scenario(
        name="chaff_capable_rival_telegraphed",
        summary=(
            "Rival fired chaff last night AND jammed one of our probes — "
            "arsenal is live. Full fog, no visible red. Pilot must NOT "
            "blind-drop into a likely follow-up jam; probe first."
        ),
        build=build,
        assertions=[
            NoHarvesterDeployment(),
            ProbeCount(min=1, max=3),
            RationaleMentions(needles=("chaff", "jam")),
            PlanMatchesMaterialisedVerb(),
        ],
        tags=("comprehension", "chaff", "arsenal_tracker"),
    )


def emp_cloud_over_drop() -> Scenario:
    """Fresh EMP cloud sits over the highest-value drop tile. Avoid it."""
    def build():
        pure_cells = [(20, 20, 250), (20, 21, 240), (21, 20, 235)]
        cloud_cells = [(20, 20), (19, 20), (21, 20), (20, 19), (20, 21)]
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .reveal_red(pure_cells)
            .grant_live_vision([(x, y) for x, y, _ in pure_cells])
            .place_harvester("harvester_p1", state="orbit")
            .record_last_night_combat(
                type="emp", owner="p2", hours=list(range(0, 6)),
                targets=[(20, 20)], radius=1, cells=cloud_cells, day=3,
            )
            .build()
        )
    return Scenario(
        name="emp_cloud_over_drop",
        summary=(
            "Pure seam visible at (20,20) but a live EMP cloud (hours 0-5, "
            "cells 5x cross around the seam) blankets the whole target. "
            "Pilot must avoid dropping into the cloud."
        ),
        build=build,
        assertions=[
            NoDropAtCells(cells=[(20, 20), (19, 20), (21, 20), (20, 19), (20, 21)]),
            RationaleMentions(needles=("emp", "cloud", "jam")),
            PlanMatchesMaterialisedVerb(),
        ],
        tags=("comprehension", "emp", "drop_safety"),
    )


def hoard_full_shippable_orbit() -> Scenario:
    """Orbit phase, hoard is heavy with red. Ship before spending on anything else."""
    def build():
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .set_phase("orbit")
            .place_harvester("harvester_p1", state="orbit")
            .fill_hoard(player="p1", red=6, green=1)
            .set_credits(p1=300, p2=200)
            .build()
        )
    return Scenario(
        name="hoard_full_shippable_orbit",
        summary=(
            "Orbit turn. Hoard holds 6 pure-RED + 1 GREEN parcel and we "
            "have 300 credits. Pilot should refine and/or ship_catapult "
            "the hoard, not sink credits into new probes while cargo "
            "rots in orbit."
        ),
        build=build,
        assertions=[
            RationaleMentions(needles=("ship", "hoard", "refine", "vault")),
            # Either ship or refine is a valid vault-flush verb this turn.
            PlanLabelIn(labels=(
                "ship_hoard", "vault_flush", "vault_flush_orbit",
                "ship", "refine", "hoard_out",
            )),
            PlanMatchesMaterialisedVerb(),
            NoSilentSelectionDrops(),
        ],
        tags=("comprehension", "orbit", "shipping"),
    )


def fleet_rebuild_unaffordable_orbit() -> Scenario:
    """Orbit phase, credits too low for a build. Should NOT pretend to build."""
    def build():
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .set_phase("orbit")
            .place_harvester("harvester_p1", state="orbit")
            .set_credits(p1=10, p2=200)
            .fill_hoard(player="p1", red=2)
            .build()
        )
    return Scenario(
        name="fleet_rebuild_unaffordable_orbit",
        summary=(
            "Orbit turn with 10 credits — not enough for a probe (100) or "
            "harvester (300). Pilot must NOT emit a fleet_rebuild plan "
            "with build_probe as if it were affordable; refine or ship "
            "the small red hoard instead."
        ),
        build=build,
        assertions=[
            # Doctrinal acceptance: any of these plan-vocabulary tokens
            # is a defensible response to "orbit with cash < build cost".
            # Refining/shipping/repairing/refreshing is all valid; the
            # anti-pattern is a straight-faced fleet_rebuild claim.
            PlanLabelIn(labels=(
                "vault_flush_orbit", "ship_hoard", "ship", "refine",
                "hoard_out", "defensive_repair",
            )),
            # If the pilot did choose a plan we like, it should map to
            # a real orbit verb (ship_catapult / refine / repair /
            # solar_jettison). Vacuously passes when plan_label doesn't
            # match a mapping key.
            PlanMatchesMaterialisedVerb(),
            NoSilentSelectionDrops(),
        ],
        tags=("comprehension", "orbit", "affordability"),
    )


def two_harvesters_no_visible_red_hot_drop_blue() -> Scenario:
    """2 harvesters idle, heavy blue in the tank, zero red. Blind-drop for pressure."""
    def build():
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
            .give_blue_purity(player="p1", purity_total=800)
            .clear_visibility("p1")
            .build()
        )
    return Scenario(
        name="two_harvesters_no_visible_red_hot_drop_blue",
        summary=(
            "Two harvesters both in orbit, zero red visible, and the hoard "
            "holds 800 purity of BLUE. Doctrine (harness §multi_harvester) "
            "says convert idle assets into map pressure: hot-drop at least "
            "one harvester rather than let both sit — OR probe aggressively "
            "to find red for the harvester on the next turn."
        ),
        build=build,
        assertions=[
            # Either "blind-drop for pressure" (harvest_mixed → drop) or
            # "probe to find red first" (probe_seed → probe) is a
            # doctrinally defensible response to "no red visible". Reject
            # only pathological cases (idle probe, no plan).
            PlanLabelIn(labels=(
                "harvest_mixed", "probe_seed", "denial_dominant",
            )),
            # Rationale must at least mention that the pilot considered
            # the blind-drop route — even if it didn't pick it.
            RationaleMentions(needles=(
                "drop", "blind", "blue", "probe", "pressure",
            )),
            PlanMatchesMaterialisedVerb(),
            NoSilentSelectionDrops(),
        ],
        tags=("comprehension", "multi_harvester", "blue_drop"),
    )


def contested_pure_with_own_emp() -> Scenario:
    """Enemy harvester adjacent to a pure seam AND we have EMP. Deny + harvest."""
    def build():
        pure_cells = [(15, 15, 250), (15, 16, 245), (16, 15, 240)]
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .reveal_red(pure_cells)
            .grant_live_vision([(x, y) for x, y, _ in pure_cells] + [(14, 15)])
            .place_harvester("harvester_p1", state="surface", at=(13, 15))
            .place_enemy_harvester("harvester_p2", at=(14, 15))
            .give_weapon_stock(player="p1", emp=1)
            .give_blue_purity(player="p1", purity_total=255)
            .build()
        )
    return Scenario(
        name="contested_pure_with_own_emp",
        summary=(
            "Pure seam at (15,15)/(15,16)/(16,15). Enemy harvester at "
            "(14,15) is one step from us and one step from the seam. We "
            "hold 1 EMP + 255 BLUE. Correct play: EMP the enemy path so "
            "our chain can walk in unopposed."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="emp_launch"),
            EmpSalvoCoversCell(target=(14, 15), radius=2),
            HarvesterChainNearCell(target=(15, 15), max_distance=1),
            RationaleMentions(needles=("emp", "deny", "contest")),
            PlanMatchesMaterialisedVerb(),
        ],
        tags=("comprehension", "emp", "denial"),
    )


def contested_pure_build_own_chaff() -> Scenario:
    """Rival's EMP-heavy — build chaff defence before Nox."""
    def build():
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .set_phase("orbit")
            .place_harvester("harvester_p1", state="orbit")
            .record_last_night_combat(
                type="emp", owner="p2", hours=list(range(2, 6)),
                targets=[(12, 12)], radius=2,
                cells=[(11, 12), (12, 12), (13, 12), (12, 11), (12, 13)],
                day=2,
            )
            .record_last_night_combat(
                type="emp", owner="p2", hours=list(range(16, 20)),
                targets=[(30, 20)], radius=2,
                cells=[(29, 20), (30, 20), (31, 20), (30, 19), (30, 21)],
                day=2,
            )
            .give_blue_purity(player="p1", purity_total=600)
            .give_weapon_stock(player="p1", emp=0, chaff=0)
            .set_credits(p1=300, p2=100)
            .build()
        )
    return Scenario(
        name="contested_pure_build_own_chaff",
        summary=(
            "Rival fired TWO EMPs last night (public log). We hold 0 chaff, "
            "600 BLUE, 300 credits, and orbit is open. Correct play: build "
            "chaff so tomorrow's Nox can't be jammed at will."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="build_chaff"),
            RationaleMentions(needles=("chaff", "defence", "defense", "jam")),
            PlanMatchesMaterialisedVerb(),
            NoSilentSelectionDrops(),
        ],
        tags=("comprehension", "orbit", "chaff_defence"),
    )


def orbit_repair_before_ship() -> Scenario:
    """Damaged harvester + full hoard: repair AND ship in the same orbit."""
    def build():
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .set_phase("orbit")
            .place_harvester("harvester_p1", state="orbit", damaged=True)
            .fill_hoard(player="p1", red=5)
            # v1.9 — bumped from 400 → 800: REPAIR_COST is 500 in
            # pilot_v4/orbit.py, so 400 credits made the recommendation
            # composer silently drop the repair slot (it's unaffordable
            # alone) and cascade into probe+mine builds. 800 covers
            # repair (500) + a 5-parcel ship_catapult (25c/parcel = 125)
            # with margin for a probe on top.
            .set_credits(p1=800, p2=100)
            .build()
        )
    return Scenario(
        name="orbit_repair_before_ship",
        summary=(
            "Orbit turn. Our harvester is damaged and the hoard holds 5 "
            "pure-RED parcels. Credits (400) cover both a repair and a "
            "catapult ship. Pilot should schedule BOTH — a damaged asset "
            "wastes tomorrow's Nox, and unshipped cargo bleeds tempo."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="repair"),
            RationaleMentions(needles=("repair", "damaged", "damage")),
            PlanMatchesMaterialisedVerb(),
            NoSilentSelectionDrops(),
        ],
        tags=("comprehension", "orbit", "repair"),
    )


# ---------------------------------------------------------------------------
# Core-loop scenarios (v1.9)
#
# These test the VANILLA case — no chaff, no EMP threat, no redsign, no
# damage — just "harvester(s) in orbit, pure RED visible, execute the
# core loop". A pilot that fails these is fundamentally broken; a pilot
# that fails the doctrine scenarios but passes these is at least playable
# in a season. Season score is dominated by how many turns the pilot
# banks 500+ pt harvest chains, so these are the load-bearing tests.
# ---------------------------------------------------------------------------


def core_loop() -> Scenario:
    """Vanilla core-loop: harvester in orbit, pure RED visible, chain + pickup."""
    def build():
        # 4-cell pure-RED cluster (all pure-tier, purity ≥ 240 ⇒ 3.0× mult).
        pure_cluster = [
            (15, 15, 250), (16, 15, 245), (15, 16, 245), (16, 16, 240),
        ]
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .reveal_red(pure_cluster)
            .grant_live_vision([(x, y) for x, y, _ in pure_cluster])
            .place_harvester("harvester_p1", state="orbit")
            .build()
        )
    return Scenario(
        name="core_loop",
        summary=(
            "Day 3 planning, harvester in orbit, pure-RED 4-cell cluster "
            "at (15,15)/(16,15)/(15,16)/(16,16) all in fresh LOS. No "
            "chaff, no EMP, no redsign — nothing but a clean harvest. "
            "Pilot MUST drop, chain onto the pure cells, pick up."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="drop", min_count=1),
            PolicyContainsAction(action="pickup", min_count=1),
            HarvesterChainHits(
                region=[(15, 15), (16, 15), (15, 16), (16, 16)],
                min_hits=2,
            ),
            EndsWithPickup(),
            MinExpectedValue(min_value=400),
            PlanLabelIn(labels=(
                "harvest_pure", "harvest_mixed",
            )),
            PlanMatchesMaterialisedVerb(),
            NoSilentSelectionDrops(),
        ],
        tags=("core_loop", "comprehension"),
    )


def core_loop_two_harvesters() -> Scenario:
    """Two harvesters + two RED clusters in different regions — use both."""
    def build():
        cluster_a = [
            (10, 10, 250), (11, 10, 245), (10, 11, 240), (11, 11, 235),
        ]
        cluster_b = [
            (35, 25, 250), (36, 25, 245), (35, 26, 240), (36, 26, 235),
        ]
        all_cells = [(x, y) for x, y, _ in cluster_a + cluster_b]
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=7)
            .reveal_red(cluster_a)
            .reveal_red(cluster_b)
            .grant_live_vision(all_cells)
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
            .build()
        )
    return Scenario(
        name="core_loop_two_harvesters",
        summary=(
            "Day 3 planning, two harvesters in orbit, two pure-RED "
            "clusters in opposite quadrants (10,10) and (35,25). "
            "Multi-harvester doctrine: use both units, one per cluster, "
            "each ending in pickup."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="drop", min_count=2),
            PolicyContainsAction(action="pickup", min_count=2),
            DropsInDifferentRegions(min_manhattan=20),
            EndsWithPickup(),
            MinPureCellsTouched(min_pure=3, purity_threshold=200),
            PlanLabelIn(labels=(
                "harvest_pure", "harvest_mixed",
            )),
            PlanMatchesMaterialisedVerb(),
            NoSilentSelectionDrops(),
        ],
        tags=("core_loop", "comprehension", "multi_harvester"),
    )


def core_loop_two_harvesters_orbit_then_second_night() -> Scenario:
    """Day 2 orbit — 2 harvesters recovered from night 1, hoard has RED
    from night 1's harvests, visible pure seams remain from night 1's
    probe reveals. Pilot must handle the mid-season orbit correctly
    (ship what we have, refine or top up probes, don't idle) so that
    the NEXT night can execute two full chains.
    """
    def build():
        pure_cluster_a = [
            (10, 10, 250), (11, 10, 245), (10, 11, 240),
        ]
        pure_cluster_b = [
            (35, 25, 250), (36, 25, 245), (35, 26, 240),
        ]
        all_cells = [(x, y) for x, y, _ in pure_cluster_a + pure_cluster_b]
        return (
            WorldBuilder(seed=42, day=2, season_day_cap=7)
            .set_phase("orbit")
            # Both harvesters back in orbit from night 1.
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
            # RED still visible from night 1 probes (both clusters).
            .reveal_red(pure_cluster_a)
            .reveal_red(pure_cluster_b)
            .grant_live_vision(all_cells)
            # Hoard carries 3 RED parcels banked from night 1 pickups.
            .fill_hoard(player="p1", red=3)
            # Credits earned from the orbit-1 cycle + starting stipend.
            .set_credits(p1=600, p2=200)
            .build()
        )
    return Scenario(
        name="core_loop_two_harvesters_orbit_then_second_night",
        summary=(
            "Day 2 ORBIT phase, mid-season snapshot: both harvesters "
            "recovered from night 1, 3 RED parcels sitting in hoard, "
            "600 credits, and two pure-RED clusters still lit from "
            "night 1's probes. Pilot must prepare for the SECOND night: "
            "ship the hoard, top up probes if useful, keep both "
            "harvesters ready to redeploy. Anti-pattern: idle the "
            "hoard while building infra."
        ),
        build=build,
        assertions=[
            # The pilot must schedule at least one economy-shaping
            # orbit action — ship, refine, or a coherent build. An
            # empty policy would waste the orbit slot.
            PolicyContainsAction(action="ship_catapult", min_count=1),
            PlanLabelIn(labels=(
                "vault_flush_orbit", "fleet_rebuild",
                "defensive_repair", "refine",
            )),
            PlanMatchesMaterialisedVerb(),
            NoSilentSelectionDrops(),
        ],
        tags=("core_loop", "comprehension", "orbit", "multi_harvester"),
    )


# ---------------------------------------------------------------------------
# pilot_v6_arena phase-1 fixtures (v1.9) — renamed to Tabula 2026-07
#
# Three preseeded fixtures that model the arena's Day 1 / Day 2 / Day 3
# states as INDEPENDENT snapshots. Used for single-turn regression tests
# of the arena harness. A live 3-night arena run uses the engine's own
# progression (day 1 → orbit stub → day 2 → orbit stub → day 3) starting
# from the day-1 fixture; these standalone versions let us test each
# night's state in isolation without waiting for orbit resolution.
#
# Layout (kept to one quadrant so a pilot can plausibly discover all
# RED with 2 probes over 3 nights):
#
#   Pure trio at (12, 10)  — day 1 harvest target
#   Mass strip at (25, 15) — day 2 target for harvester_p1
#   Mass strip at (30, 5)  — day 2 target for harvester_p1_2
#   Trace scatter at (35, 20) — filler for day 3
# ---------------------------------------------------------------------------


_ARENA_PURE_TRIO = [(12, 10, 250), (13, 10, 245), (14, 10, 250)]
_ARENA_MASS_STRIP_A = [(25, 15, 210), (26, 15, 200), (27, 15, 190), (28, 15, 180)]
_ARENA_MASS_STRIP_B = [(30, 5, 200), (30, 6, 195), (31, 6, 185)]
_ARENA_TRACE = [(35, 20, 70), (36, 20, 65), (35, 21, 60)]


def arena_day1() -> Scenario:
    """Day 1 arena state: 1 harvester + 2 probes, pure trio in LOS."""
    def build():
        pure_cells = list(_ARENA_PURE_TRIO)
        return (
            WorldBuilder(seed=42, day=1, season_day_cap=3)
            .reveal_red(pure_cells)
            .grant_live_vision([(x, y) for x, y, _ in pure_cells])
            .place_harvester("harvester_p1", state="orbit")
            .build()
        )
    return Scenario(
        name="arena_day1",
        summary=(
            "Arena Day 1: 1 harvester in orbit, 2 probes available, pure "
            "trio visible at (12,10)/(13,10)/(14,10). Pilot must drop, "
            "chain the 3 pure cells, pickup. Probes optional but useful "
            "for day 2 vision. Success: banked >= 2000 pts."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="drop", min_count=1),
            PolicyContainsAction(action="pickup", min_count=1),
            HarvesterChainHits(region=[(12, 10), (13, 10), (14, 10)], min_hits=2),
            EndsWithPickup(),
            MinExpectedValue(min_value=400),
        ],
        tags=("arena", "phase1", "harvest_only"),
    )


def arena_day2() -> Scenario:
    """Day 2 arena state: 2 harvesters + 2 probes, day-1 seam is sg,
    two mass strips visible in different quadrants."""
    def build():
        # Simulate the day-1 harvest by marking the pure trio as synthetic-green,
        # then reveal the day-2 targets.
        return (
            WorldBuilder(seed=42, day=2, season_day_cap=3)
            .reveal_green_synthetic(
                [(x, y) for x, y, _ in _ARENA_PURE_TRIO], owner="p1",
            )
            .reveal_red(_ARENA_MASS_STRIP_A)
            .reveal_red(_ARENA_MASS_STRIP_B)
            .grant_live_vision(
                [(x, y) for x, y, _ in _ARENA_MASS_STRIP_A + _ARENA_MASS_STRIP_B]
            )
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
            .build()
        )
    return Scenario(
        name="arena_day2",
        summary=(
            "Arena Day 2: 2 harvesters in orbit, day-1 pure trio is now "
            "synthetic-green. Two mass strips visible in different "
            "quadrants. Pilot must deploy BOTH harvesters, one per "
            "cluster. Success: 2 drops, 2 pickups, banks >= 800 pts."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="drop", min_count=2),
            PolicyContainsAction(action="pickup", min_count=2),
            DropsInDifferentRegions(min_manhattan=10),
            EndsWithPickup(),
        ],
        tags=("arena", "phase1", "harvest_only", "multi_harvester"),
    )


def arena_day3() -> Scenario:
    """Day 3 arena state: 2 harvesters + 2 probes, day-1/2 seams sg,
    trace scatter is the remaining RED (low-value chain, still worth
    executing rather than passing)."""
    def build():
        prior_harvested = (
            [(x, y) for x, y, _ in _ARENA_PURE_TRIO]
            + [(x, y) for x, y, _ in _ARENA_MASS_STRIP_A]
            + [(x, y) for x, y, _ in _ARENA_MASS_STRIP_B]
        )
        return (
            WorldBuilder(seed=42, day=3, season_day_cap=3)
            .reveal_green_synthetic(prior_harvested, owner="p1")
            .reveal_red(_ARENA_TRACE)
            .grant_live_vision([(x, y) for x, y, _ in _ARENA_TRACE])
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
            .build()
        )
    return Scenario(
        name="arena_day3",
        summary=(
            "Arena Day 3 (final night): 2 harvesters, only trace-tier RED "
            "remains visible. Pilot should still deploy at least one "
            "harvester rather than pass — trace banks something, passing "
            "banks zero. Success: at least 1 drop + 1 pickup."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="drop", min_count=1),
            PolicyContainsAction(action="pickup", min_count=1),
            EndsWithPickup(),
        ],
        tags=("arena", "phase1", "harvest_only"),
    )


# ---------------------------------------------------------------------------
# Tabula v2 fog scenario — probes must be launched to unlock territory
# ---------------------------------------------------------------------------
# One friendly probe pre-placed at (10,10) reveals the pure trio at
# (12,10)-(14,10) — the agent can see and harvest those immediately.
# A SECOND red cluster sits at (28,14)-(30,14) hidden in fog. The only
# way to reach it is to launch another probe covering that area. Success
# criterion: the agent's policy contains BOTH a drop and a probe.

def arena_day1_fog() -> Scenario:
    """Day 1 arena state: 1 probe pre-placed reveals a pure trio; a
    second red cluster sits in fog. Agent must launch a probe to
    unlock it (or accept that it's leaving points on the board)."""
    def build():
        visible_trio = list(_ARENA_PURE_TRIO)  # revealed by pre-placed probe
        hidden_cluster = [(28, 14, 210), (29, 14, 200), (30, 14, 195)]
        return (
            WorldBuilder(seed=42, day=1, season_day_cap=3)
            .reveal_red(visible_trio)
            .reveal_red(hidden_cluster)
            .place_probe(at=(10, 10))  # covers (6..14, 6..14) — trio visible
            .place_harvester("harvester_p1", state="orbit")
            .build()
        )
    return Scenario(
        name="arena_day1_fog",
        summary=(
            "Arena Day 1 (fog variant): 1 harvester in orbit, 1 probe "
            "pre-placed at (10,10) revealing the pure trio at (12-14,10). "
            "A second red cluster sits in fog around (28-30,14). Pilot "
            "should probe to unlock the second cluster AND harvest the "
            "visible trio. Success: >=1 drop AND >=1 probe."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="drop", min_count=1),
            PolicyContainsAction(action="probe", min_count=1),
            PolicyContainsAction(action="pickup", min_count=1),
            EndsWithPickup(),
        ],
        tags=("arena", "phase1", "probe_intelligence"),
    )


# ---------------------------------------------------------------------------
# Tabula v2 persistent multi-night rig — arena_persistent
# ---------------------------------------------------------------------------
# A 4-night arena the driver script plays end-to-end (day 1 = probe-heavy
# setup night, days 2-4 = harvest with vision from prior nights).
#
# The board is preseeded with 4 clusters spread across the map, ALL in
# fog. The agent starts with 1 harvester in orbit but NO friendly probes
# already placed and NO grant_live_vision — so day 1 the only legal
# actions are probes (drops into fog get rejected). Night 1 vision then
# persists (probes live 3 nights) to enable harvest on days 2-4.
#
# Cluster placement is deliberately anti-clustered so a single probe
# disk (81 cells) cannot cover more than one cluster — the agent must
# spread probes.

_PERSIST_CLUSTER_NW = [(8, 6, 250), (9, 6, 245), (10, 6, 255)]      # pure trio (has p=255)
_PERSIST_CLUSTER_NE = [(30, 5, 210), (31, 5, 200), (30, 6, 195)]    # mass triangle
_PERSIST_CLUSTER_SE = [(32, 20, 180), (33, 20, 170), (33, 21, 160)]  # mass ridge
_PERSIST_CLUSTER_SW = [(6, 22, 120), (7, 22, 110), (6, 23, 100)]    # vein cluster


def arena_persistent() -> Scenario:
    """4-night persistent arena for Tabula v2.

    Board is preseeded with four spread-out RED clusters, entirely in
    fog on day 1. The agent starts with 1 harvester in orbit and no
    friendly probes. Night 1 the agent MUST probe (nothing legal to
    harvest); nights 2-4 the agent harvests what its probes revealed
    and lays additional probes as needed. season_day_cap=4 so the
    engine progresses day 1 -> day 2 -> day 3 -> day 4 -> SEASON_COMPLETE.
    """
    def build():
        cells = _PERSIST_CLUSTER_NW + _PERSIST_CLUSTER_NE \
              + _PERSIST_CLUSTER_SE + _PERSIST_CLUSTER_SW
        wb = (
            WorldBuilder(seed=42, day=1, season_day_cap=4)
            .reveal_red(cells)
            # NO grant_live_vision — the board is entirely fog on day 1.
            # NO place_probe — the agent must supply its own probes.
            .place_harvester("harvester_p1", state="orbit")
        )
        # Give p1 enough probes for the whole 4-night season (one per
        # night for 4 clusters is plenty). PROBE_INITIAL_STOCK is 2 by
        # default; we want the agent's strategy to be limited by hours,
        # not by artificial stock constraints during multi-night testing.
        sess = wb._ensure_session()
        sess.probe_stock["p1"] = 6
        return wb.build()
    return Scenario(
        name="arena_persistent",
        summary=(
            "Persistent 4-night arena. Board has 4 RED clusters spread "
            "across NW, NE, SE, SW quadrants, all in fog on day 1. "
            "Agent has 1 harvester in orbit and no friendly probes. "
            "Success: >=2 probes launched over the season AND >=1 "
            "successful drop-chain-pickup by day 4."
        ),
        build=build,
        assertions=[
            # These are best-effort assertions for the single-turn eval;
            # the multi-night driver runs its own richer end-state audit.
            PolicyContainsAction(action="probe", min_count=1),
        ],
        tags=("arena", "phase1", "probe_intelligence", "multi_night"),
    )


# ---------------------------------------------------------------------------
# Tabula v3 scenarios — blue fallback, hot drop, opponent
# ---------------------------------------------------------------------------

def arena_blue_fallback() -> Scenario:
    """2 harvesters, trace-only RED visible, blue cluster visible.

    Wishlist should compile ``grab_blue`` (blue is empty, harvesters >= 2)
    and the agent should split — one harvester on the pathetic trace RED,
    one on the blue cluster. Success: >=1 drop AND (blue banked > 0 OR
    both harvesters deployed).
    """
    def build():
        from sea_of_colours.generator import Cell as _Cell, Tile as _Tile
        trace_red = [(12, 10, 40), (13, 10, 35), (14, 10, 30)]  # all trace tier
        blue_cluster = [(28, 15, 180), (28, 16, 170), (29, 15, 160)]
        wb = (
            WorldBuilder(seed=42, day=1, season_day_cap=3)
            .reveal_red(trace_red)
            .grant_live_vision(
                [(x, y) for x, y, _ in trace_red]
                + [(x, y) for x, y, _ in blue_cluster]
            )
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
        )
        sess = wb._ensure_session()
        # Paint blue cells directly (WorldBuilder has no reveal_blue helper).
        for x, y, p in blue_cluster:
            sess.grid[y][x] = _Cell(tile=_Tile.BLUE, purity=int(p))
        return wb.build()
    return Scenario(
        name="arena_blue_fallback",
        summary=(
            "2 harvesters, ONLY trace-tier RED visible (~35 purity each), "
            "and a purity-170 BLUE cluster in the SE. Wishlist should say "
            "grab_blue; agent should deploy one harvester to blue. "
            "Success: >=1 drop and >=1 pickup."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="drop", min_count=1),
            PolicyContainsAction(action="pickup", min_count=1),
            EndsWithPickup(),
        ],
        tags=("arena", "phase1", "blue_fallback", "wishlist"),
    )





# ---------------------------------------------------------------------------
# Tabula v3 signal-driven test — arena_campaign
# ---------------------------------------------------------------------------
# A 4-night campaign designed to exercise the new signal-driven hot-drop
# logic:
#
#   * 4 spread RED clusters, one containing a pure(255) cell — when any
#     seat discovers it, the engine broadcasts a REDSIGN and both seats
#     see the coord in their agent_view. Racing to it is now doctrine.
#   * BLUE tiles preseeded at BRIGHT bluesign cells (intensity >= 0.6)
#     with mixed purities — some trace, some rich. Bluesign hot drop
#     should land on a real blue but the purity is a gamble (matches
#     the game's real bluesign semantics).
#   * Two seats (p1=tabula_v3, p2=heuristic). Both get 6 probes.
#   * Fog day 1 — bluesign is still visible (it's public static data).
#
# The (bright) bluesign coords below come from a snapshot of the seed=42
# world (see arena_persistent). Same seed → same coordinates.

_CAMPAIGN_REDS = _PERSIST_CLUSTER_NW + _PERSIST_CLUSTER_NE \
    + _PERSIST_CLUSTER_SE + _PERSIST_CLUSTER_SW  # includes (10, 6, 255)

# Bluesign cluster 3 (mid-left): plant a spread of purities on bright cells.
_CAMPAIGN_BLUES_C3 = [
    (19, 12, 210),  # brightest cell (0.945) → rich blue (worth chasing)
    (18, 11, 130),  # bright (0.736)         → vein blue
    (17, 12, 40),   # bright (0.708)         → trace blue (gamble loser)
]
# Bluesign cluster 4 (SE): another cluster to force a choice.
_CAMPAIGN_BLUES_C4 = [
    (36, 20, 220),  # brightest cell (0.99)  → rich blue
    (35, 20, 90),   # bright (0.833)         → vein
    (36, 21, 35),   # bright (0.795)         → trace
]


def arena_campaign() -> Scenario:
    """4-night arena_campaign for tabula_v3 signal-driven doctrine.

    Board is arena_persistent + preseeded BLUE tiles inside two natural
    bluesign clusters (so bluesign hot drop actually banks value) +
    active p2 seat (so competitor_intel populates).

    Night 1: no vision. Agent options — probe spread, or bluesign hot
    drop (blind-drop-and-crawl on a bright cluster). Doctrine says both
    are legitimate; the wishlist gates which is preferred.
    Nights 2-3: harvest revealed cells. If p1 or p2 walks onto the
    pure(255) at (10, 6), a REDSIGN broadcast fires and both seats
    can race the coord next turn.
    Night 4: settlement.

    Success criteria (loose — the multi-night driver runs its own audit):
      * >=2 probes launched over the season.
      * >=1 pickup submitted.
    """
    def build():
        from sea_of_colours.generator import Cell as _Cell, Tile as _Tile
        wb = (
            WorldBuilder(seed=42, day=1, season_day_cap=4)
            .reveal_red(_CAMPAIGN_REDS)
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
            .place_harvester("harvester_p1_3", state="orbit")
            .place_harvester("harvester_p2", state="orbit", owner="p2")
            .place_harvester("harvester_p2_2", state="orbit", owner="p2")
            .place_harvester("harvester_p2_3", state="orbit", owner="p2")
        )
        sess = wb._ensure_session()
        # Paint blue cells directly on the grid (WorldBuilder lacks a
        # reveal_blue helper for arbitrary purity mixes).
        for x, y, p in (_CAMPAIGN_BLUES_C3 + _CAMPAIGN_BLUES_C4):
            sess.grid[y][x] = _Cell(tile=_Tile.BLUE, purity=int(p))
        # Both seats need enough probes for the whole season.
        sess.probe_stock["p1"] = 6
        sess.probe_stock["p2"] = 6
        return wb.build()

    return Scenario(
        name="arena_campaign",
        summary=(
            "4-night signal-driven arena for tabula_v3. Same 4 RED clusters "
            "as arena_persistent (one contains pure(255) at (10,6) — will "
            "trigger REDSIGN when either seat discovers it). Two bluesign "
            "clusters have real BLUE preseeded with mixed purities (trace "
            "to rich). Two seats: p1=tabula, p2=heuristic, 6 probes each. "
            "Tests: bluesign blind-drop-and-crawl (night 1) and redsign "
            "race (when pure discovered)."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="probe", min_count=1),
        ],
        tags=("arena", "phase1", "signal_driven", "hot_drop", "multi_night"),
    )


def arena_solo_normal() -> Scenario:
    """Normal-defaults SOLO season — 7 nights, 1 harvester, 2 probes, no preseeds.

    Closest thing to a real game start:
      * Solo: only ``p1``. No opponent, no station_intel opponents.
      * 1 harvester in orbit (the engine default; heuristic orbit builds
        more each night up to the fleet cap).
      * 2 probes stock (engine's PROBE_INITIAL_STOCK — no override).
      * 7-night season (``season_day_cap=7``).
      * NO preseeded reds or blues — the natural seed=42 world with its
        own generator-produced RED / BLUE / GREEN distribution.
      * Fog of war (no ``grant_live_vision``).

    Contrast with :func:`arena_solo`:
      * arena_solo: 4 nights, 3 harvesters, 6 probes, preseeded pure(255)
        + bluesign blues. Designed for doctrine stress-testing.
      * arena_solo_normal: 7 nights, 1 harvester, 2 probes, natural world.
        Designed to measure the agent's real-game ceiling.
    """
    def build():
        wb = (
            WorldBuilder(seed=42, day=1, season_day_cap=7)
            .place_harvester("harvester_p1", state="orbit")
        )
        sess = wb._ensure_session()
        # Solo — trim p2 from every per-seat map so the engine runs
        # single-seat resolution.
        sess.players = ["p1"]
        for attr in ("credits", "probe_stock", "hoard_squares",
                     "harvest_log", "blue_bank", "weapon_stock",
                     "weapons_used", "player_names", "player_profiles"):
            m = getattr(sess, attr, None)
            if isinstance(m, dict) and "p2" in m:
                m.pop("p2", None)
        # Do NOT touch probe_stock["p1"] — it defaults to PROBE_INITIAL_STOCK
        # (currently 2) at session construction.
        return wb.build()

    return Scenario(
        name="arena_solo_normal",
        summary=(
            "SOLO 7-night normal-defaults season. p1 only, 1 harvester in "
            "orbit, 2 probes stock, natural seed=42 world (no preseeded "
            "reds or blues). Heuristic orbit builds new harvesters and "
            "probes each night. Baseline for the agent's real-game ceiling."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="probe", min_count=1),
        ],
        tags=("arena", "phase1", "solo", "normal", "multi_night"),
    )


def arena_solo() -> Scenario:
    """4-night SOLO campaign — tabula_v3 vs empty seat.

    Same board as ``arena_campaign`` (seed=42, same 4 RED clusters +
    pure(255) + preseeded bluesign blues), but p2 is REMOVED from the
    session. The engine runs with a single active seat — tabula_v3
    must maximize nomadic harvesting on its own with no opponent
    interference.

    Setup:
      * 3 harvesters in orbit for p1
      * 6 probes for p1
      * No p2 seat at all — season resolves after both phases each night
        with just p1's submissions

    Purpose:
      * Verify the nomadic doctrine actually maximizes harvest when the
        agent has no opponent to react to (weapons doctrine should
        stay dormant — no opponent = no station_intel opponents = no
        beware_* tags).
      * Baseline the achievable vault score with heuristic orbit
        (ships parcels, builds units, jettisons greens) so we can
        measure what opponent contention costs later.
    """
    def build():
        from sea_of_colours.generator import Cell as _Cell, Tile as _Tile
        wb = (
            WorldBuilder(seed=42, day=1, season_day_cap=4)
            .reveal_red(_CAMPAIGN_REDS)
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p1_2", state="orbit")
            .place_harvester("harvester_p1_3", state="orbit")
        )
        sess = wb._ensure_session()
        # Paint bluesign blue tiles (identical to arena_campaign).
        for x, y, p in (_CAMPAIGN_BLUES_C3 + _CAMPAIGN_BLUES_C4):
            sess.grid[y][x] = _Cell(tile=_Tile.BLUE, purity=int(p))
        sess.probe_stock["p1"] = 6
        # Remove p2 entirely — single-seat session. The engine's
        # ``both_ready`` / resolution logic reads ``sess.players`` so
        # trimming this list gives us a solo run.
        sess.players = ["p1"]
        # Also trim per-seat maps so downstream views don't see p2 rows.
        for attr in ("credits", "probe_stock", "hoard_squares",
                     "harvest_log", "blue_bank", "weapon_stock",
                     "weapons_used", "player_names", "player_profiles"):
            m = getattr(sess, attr, None)
            if isinstance(m, dict) and "p2" in m:
                m.pop("p2", None)
        return wb.build()

    return Scenario(
        name="arena_solo",
        summary=(
            "SOLO 4-night arena for tabula_v3. Same board as arena_campaign "
            "(4 RED clusters + pure(255) + preseeded bluesign blues), but "
            "p2 is removed entirely. Tests maximum nomadic harvest with "
            "zero opponent interference. Weapons doctrine stays dormant."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="probe", min_count=1),
        ],
        tags=("arena", "phase1", "solo", "nomadic", "multi_night"),
    )


def arena_persistent_opponent() -> Scenario:
    """arena_persistent variant with an active p2 (heuristic).

    Same board and units as arena_persistent, but the session has TWO
    seats so p2's actions populate competitor_intel over the season.
    Verifies the OPPONENT INTEL prompt block renders correctly across
    multi-night play with a real opponent making probe/drop moves.
    """
    def build():
        cells = _PERSIST_CLUSTER_NW + _PERSIST_CLUSTER_NE \
              + _PERSIST_CLUSTER_SE + _PERSIST_CLUSTER_SW
        wb = (
            WorldBuilder(seed=42, day=1, season_day_cap=4)
            .reveal_red(cells)
            .place_harvester("harvester_p1", state="orbit")
            .place_harvester("harvester_p2", state="orbit", owner="p2")
        )
        sess = wb._ensure_session()
        sess.probe_stock["p1"] = 6
        sess.probe_stock["p2"] = 6
        return wb.build()
    return Scenario(
        name="arena_persistent_opponent",
        summary=(
            "arena_persistent + active p2 opponent. Two seats each with "
            "1 harvester and 6 probes; all RED in fog day 1. Verifies "
            "OPPONENT INTEL block renders when p2 acts."
        ),
        build=build,
        assertions=[
            PolicyContainsAction(action="probe", min_count=1),
        ],
        tags=("arena", "phase1", "opponent_awareness", "multi_night"),
    )



# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
SCENARIOS: List[Scenario] = [
    solo_drop_orbit(),
    solo_drop_surface(),
    probes_only(),
    collision_avoidance(),
    tier_choice(),
    blind_dawn(),
    enemy_telegraph(),
    enemy_trail_in_seam(),
    vault_pressure(),
    damaged_harvester(),
    final_night(),
    probe_collision_risk(),
    green_detour(),
    green_corridor(),
    friendly_probe_in_path(),
    multi_hop_seam(),
    enemy_intercept(),
    two_seams_choose_one(),
    pure_cluster_priority(),
    two_harvesters_distinct_targets(),
    echo_only_no_blind_drop(),
    emp_threat_front_load(),
    posture_aggressive_probes(),
    pure_under_enemy_pressure(),
    emp_denies_corridor_walk(),
    harvest_then_emp_composed(),
    pickup_early_then_emp_short_chain(),
    two_harvesters_plus_emp_denial(),
    emp_covers_corridor_during_harvest(),
    # -- v1.9 pilot-comprehension scenarios --
    redsign_seen_last_nox(),
    i_witnessed_pure(),
    chaff_capable_rival_telegraphed(),
    emp_cloud_over_drop(),
    hoard_full_shippable_orbit(),
    fleet_rebuild_unaffordable_orbit(),
    two_harvesters_no_visible_red_hot_drop_blue(),
    contested_pure_with_own_emp(),
    contested_pure_build_own_chaff(),
    orbit_repair_before_ship(),
    # -- v1.9 core-loop scenarios --
    core_loop(),
    core_loop_two_harvesters(),
    core_loop_two_harvesters_orbit_then_second_night(),
    # -- v1.9 arena phase-1 scenarios (Tabula, formerly pilot_v6_arena) --
    arena_day1(),
    arena_day2(),
    arena_day3(),
    arena_day1_fog(),
    arena_persistent(),
    # -- Tabula v3 scenarios --
    arena_blue_fallback(),
    arena_persistent_opponent(),
    arena_campaign(),
    arena_solo(),
    arena_solo_normal(),
]


def get_scenario(name: str) -> Scenario:
    """Lookup a scenario by name. Raises :class:`KeyError` if missing."""
    for s in SCENARIOS:
        if s.name == name:
            return s
    raise KeyError(name)
