"""v0.9 — Weapons + WAIT tuning constants.

Every numeric balance dial for the interdiction layer (EMP warheads,
orbital chaff flares, the WAIT command) lives here. Engine, UI, and
tests all import from this module so balance work is a one-file edit.

The plan deliberately keeps the *mechanics* generalised even when the
current launch values look like "three missiles a salvo" or "1-hour
chaff" — bumping ``EMP_MISSILES_PER_LAUNCH`` or ``CHAFF_DURATION_HOURS``
should never require an engine change.

v1.31 — the caltrop mine's dials were the third block here and are
gone. The slot is vacant; EMP and chaff are the shipped weapons.

See RULEBOOK §5 (v0.9) for the canonical prose; the values below
are the SHIPPED defaults at v0.9.0.
"""

from __future__ import annotations

from typing import Dict, List, Mapping


# ── EMP warhead blast ────────────────────────────────────────────
# Launch-cost: 200 blue-purity + 250 credits. A single launch fires
# THREE simultaneous missiles (EMP_MISSILES_PER_LAUNCH); each forms
# its own Manhattan radius-2 cloud at its target cell at the launch
# hour. Every harvester sitting in any cloud is disabled for the
# next 8 hours of the same night, and any PROBE caught in a cloud is
# destroyed. Friendly fire IS in scope — the launcher's own
# units are not immune. Open action: all seats see the launch frame
# + cloud cells.
EMP_COST_BLUE_PURITY: int = 200
EMP_COST_CREDITS: int = 250
EMP_RADIUS: int = 2
"""Manhattan radius; each cloud covers ``2*r*(r+1)+1`` cells (13 at r=2)."""
EMP_MISSILES_PER_LAUNCH: int = 3
"""Missiles fired per launch / stock consumed. One launch = up to this
many simultaneous target cells, each spawning its own EMP cloud."""
EMP_CLOUD_HOURS: int = 8
"""Cloud lifetime in PRAXIS hours. Clamped to remaining night."""
EMP_VISIBILITY: str = "open"
"""``"open"`` | ``"hidden_until_probed"`` — only ``"open"`` is wired
in v0.9.0; the field is here so a future stealth EMP is a flip."""


# ── Caltrop mines — RETIRED v1.31 ────────────────────────────────
# The third weapon slot is deliberately empty. Mines were retired to
# free it for a replacement, not because the slot was a mistake, so
# the machinery around it (schema columns, replay frames, the FX
# path) was left standing on purpose — archived seasons still play
# back, and a new weapon plugs into the same holes.
#
# Its dials lived here: cost in blue purity and credits, how many
# tiles one order armed, the batch shape, and visibility.


# ── Orbital chaff flare ──────────────────────────────────────────
# Launch-cost: 300 blue-purity, 0 credits. At the hour the chaff
# resolves, every OTHER seat's action that hour is cancelled
# (probes, drops, steps, pickups, weapon launches, other chaffs).
# The triggerer's chaff itself succeeds. Multi-hour chaff is a
# constant bump away: the simulator tracks ``chaff_until_hour``
# (computed as ``hour + CHAFF_DURATION_HOURS - 1``) so raising
# the duration is one line.
#
# v1.36 — was 255. The retune puts every weapon on a whole number of
# arsenal pips (100 each, six to the cap): a chaff used to render 2.55
# of them, which is a partial block that means nothing to look at.
CHAFF_COST_BLUE_PURITY: int = 300
CHAFF_COST_CREDITS: int = 0
CHAFF_DURATION_HOURS: int = 3
"""Number of consecutive hours the chaff smothers other moves,
starting at the hour the chaff resolves."""


# ── SNAP — single-square tempo denial ────────────────────────────
# v1.36. Launch-cost: 100 blue-purity + 250 credits. One missile, one
# cell, a cloud that is gone by the next hour.
#
# The dials are the least interesting thing about it. What SNAP is, is
# a weapon that resolves ABOVE the hour-start vision snapshot: it kills
# the probe before the engine records what anybody could see, so the
# drop that beacon was going to validate is refused. Every other effect
# in the game is judged against that snapshot and therefore cannot deny
# a landing in the hour it happens (§3.9.7). SNAP is the exception, and
# the exception is the product.
#
# Against a harvester it is a maiming, not a kill: the unit takes the
# ordinary ``damaged`` flag (§3.6.1, 500c to repair) and neither
# harvests nor auto-harvests that turn. The window is the whole hour —
# a harvester standing on the cell at hour start, and a harvester that
# walks or drops onto it during the hour, are both hit. That second
# half is what lets a SNAP guard a pure square you have guessed
# somebody is coming for.
#
# Cheapest weapon in the game and the only one that costs more credits
# than blue. That asymmetry is on purpose: blue is the scarce thing, so
# SNAP is the weapon a poor seat can still field, and the credits are
# what stop it being free to spam.
SNAP_COST_BLUE_PURITY: int = 100
SNAP_COST_CREDITS: int = 250
SNAP_RADIUS: int = 0
"""Manhattan radius. ``0`` is one cell — the ``2*r*(r+1)+1`` formula
the EMP cloud uses gives exactly that, so nothing special-cases it."""
SNAP_MISSILES_PER_LAUNCH: int = 1
SNAP_CLOUD_HOURS: int = 1
"""One hour: the cloud is scenery for the hour it lands and gone by the
next. SNAP denies a beat, not a stretch of night."""
SNAP_VISIBILITY: str = "open"
SNAP_MISSILE_SPEED: float = 1.5
"""Flight-time multiplier for the FX only — the missile arrives 1.5x
faster than a probe or an EMP. Published on ``weapon_specs`` so the
client does not keep its own copy; the engine is hour-granular and does
not care how long a sprite took to cross the screen."""


# ── The arsenal ceiling ──────────────────────────────────────────
# v1.34 — a seat may hold at most this much blue-worth of ordnance at
# once. Denominated in BUILD COST rather than unit count, which is the
# whole point: a price retune moves the ceiling with it instead of
# silently widening it, and a third weapon inherits the cap for free.
#
# v1.36 — the prices divide the cap into six pips of 100, and the three
# weapons are one, two and three of them. 23 racks fit under the
# ceiling and only 7 totals distinguish them, so a public arsenal
# figure is an exact quantity of ordnance and a poor inventory of it.
# That is the point of the retune, not a side effect of it.
WEAPONISED_BLUE_CAP: int = 600

# The price list the cap is denominated in. Public (v1.35) because the
# view publishes it: the station bars have to price a rack mid-night,
# and the alternative — a second copy of these numbers in JS — is the
# kind of duplicate that survives exactly until the first retune.
#
# This table is the ONLY place a weapon's blue price is named. Nothing
# downstream may hardcode a kind: ``weaponised_blue`` and
# ``decode_rack`` both iterate it, so withdrawing a weapon (§4.9.4) is
# a deleted entry rather than a code change.
BLUE_COST_BY_KIND: Dict[str, int] = {
    "emp": EMP_COST_BLUE_PURITY,
    "chaff": CHAFF_COST_BLUE_PURITY,
    "snap": SNAP_COST_BLUE_PURITY,
}

# The credit half of the same purchase. Deliberately a SEPARATE table
# from the blue one, because only blue is stamped per game: blue is what
# the arsenal cap is denominated in and therefore what an archived
# season has to be repriced against, while credits are a live dial. Do
# not merge them — a single table would invite stamping both, and a
# season whose credit prices are frozen cannot be rebalanced.
#
# v1.36 — added when SNAP arrived, because the view was reading a
# hardcoded ``{"emp": ..., "chaff": ...}`` literal and silently
# published SNAP at 0 credits. A weapon's price now has exactly one
# home per currency.
CREDIT_COST_BY_KIND: Dict[str, int] = {
    "emp": EMP_COST_CREDITS,
    "chaff": CHAFF_COST_CREDITS,
    "snap": SNAP_COST_CREDITS,
}

# What each weapon DOES, in the shape the view publishes and the client
# reads. Same rule as the price tables: named once here, never restated
# downstream, so a retune or a withdrawal is an edit to this file only.
# Keys are per-weapon by design — the three have genuinely different
# mechanics and a common schema would be mostly empty columns.
SPEC_BY_KIND: Dict[str, Dict[str, object]] = {
    "emp": {
        "radius": EMP_RADIUS,
        "missiles_per_launch": EMP_MISSILES_PER_LAUNCH,
        "cloud_hours": EMP_CLOUD_HOURS,
        "destroys": ["probe"],
    },
    "chaff": {"duration_hours": CHAFF_DURATION_HOURS},
    "snap": {
        "radius": SNAP_RADIUS,
        "missiles_per_launch": SNAP_MISSILES_PER_LAUNCH,
        "cloud_hours": SNAP_CLOUD_HOURS,
        "destroys": ["probe"],
        "damages": ["harvester"],
        # The two facts that make SNAP a different weapon rather than a
        # small EMP, both published because a seat cannot plan against
        # them otherwise (§4.9.4).
        "resolves_before_vision": True,
        "missile_speed": SNAP_MISSILE_SPEED,
    },
}

# v1.36 — the economy in force BEFORE the retune, kept so a save that
# predates the per-game stamp can be loaded at the prices it was
# actually played under. A finished season whose arsenal silently
# reprices itself is worse than one that will not open: the numbers
# look plausible and are wrong. Never edit these; they are history.
# ``GameSession.from_dict`` falls back to them, and only to them.
LEGACY_BLUE_COST_BY_KIND: Dict[str, int] = {"emp": 200, "chaff": 255}
LEGACY_WEAPONISED_BLUE_CAP: int = 600


def weaponised_blue(
    stock: Mapping[str, int] | None,
    costs: Mapping[str, int] | None = None,
) -> int:
    """Blue-worth of the ordnance a seat is holding (RULEBOOK §4.9.8).

    This is the number every other seat can read. It is derived from
    ``weapon_stock`` rather than from anything transactional, so a rack
    handed to a seat by the turn lab reads exactly like one it bought —
    there is no "how did you get it" channel to disagree with.

    ``costs`` is the price table to value the rack at, and a game that
    carries its own (v1.36) must pass it: an archived season priced
    against today's constants reports an arsenal it never held. Callers
    inside the engine should reach for ``GameSession.arsenal_blue``
    rather than this, which resolves the stamp for them.
    """
    if not stock:
        return 0
    table = costs if costs is not None else BLUE_COST_BY_KIND
    return sum(
        int(stock.get(kind, 0) or 0) * int(cost)
        for kind, cost in table.items()
    )


def decode_rack(
    total: int,
    costs: Mapping[str, int] | None = None,
) -> List[Dict[str, int]]:
    """Every loadout that could account for ``total`` weaponised blue.

    Enumerates against whatever price table it is given rather than
    naming the weapons, so adding or withdrawing a kind changes the
    answers without changing this function — the property that lets a
    weapon be retired as a deleted dict entry (§4.9.4).

    **Ambiguity is a property of the prices, not of the rule.** At
    100/200/300 against a 600 cap most totals admit several loadouts,
    so a public arsenal figure is an exact quantity of ordnance and an
    inexact inventory of it. Callers fold the result into min/max
    bounds and degrade to a range on their own; none of them may assume
    a single candidate. Sorted for determinism.
    """
    total = int(total or 0)
    table = dict(costs) if costs is not None else dict(BLUE_COST_BY_KIND)
    # A zero- or negative-priced kind would admit infinitely many racks.
    # It is excluded from the search and reported as zero rather than
    # crashing, because a half-configured price table should degrade to
    # a narrower answer, not to an exception in the middle of a turn.
    priced = sorted(k for k, c in table.items() if int(c) > 0)
    blank = {k: 0 for k in table}
    out: List[Dict[str, int]] = []
    if total < 0:
        return out

    def walk(i: int, rest: int, acc: Dict[str, int]) -> None:
        if i == len(priced):
            if rest == 0:
                out.append({**blank, **acc})
            return
        kind = priced[i]
        cost = int(table[kind])
        for n in range(rest // cost + 1):
            acc[kind] = n
            walk(i + 1, rest - n * cost, acc)
        acc[kind] = 0

    walk(0, total, {})
    out.sort(key=lambda rack: tuple(rack[k] for k in sorted(rack)))
    return out


# ── WAIT command ─────────────────────────────────────────────────
# Currently no tunables. Kept here for symmetry; future caps on
# consecutive waits or forced-action rules would land here.
# (A wait consumes one of the 21 hour slots but does nothing.)


__all__ = [
    "EMP_COST_BLUE_PURITY",
    "EMP_COST_CREDITS",
    "EMP_RADIUS",
    "EMP_MISSILES_PER_LAUNCH",
    "EMP_CLOUD_HOURS",
    "EMP_VISIBILITY",
    "CHAFF_COST_BLUE_PURITY",
    "CHAFF_COST_CREDITS",
    "CHAFF_DURATION_HOURS",
    "SNAP_COST_BLUE_PURITY",
    "SNAP_COST_CREDITS",
    "SNAP_RADIUS",
    "SNAP_MISSILES_PER_LAUNCH",
    "SNAP_CLOUD_HOURS",
    "SNAP_VISIBILITY",
    "SNAP_MISSILE_SPEED",
    "WEAPONISED_BLUE_CAP",
    "BLUE_COST_BY_KIND",
    "CREDIT_COST_BY_KIND",
    "SPEC_BY_KIND",
    "SNAP_MISSILE_SPEED",
    "LEGACY_BLUE_COST_BY_KIND",
    "LEGACY_WEAPONISED_BLUE_CAP",
    "weaponised_blue",
    "decode_rack",
]
