"""v1.34 — the arsenal is public, and it has a ceiling (RULEBOOK §4.9.8).

Two claims, and the whole feature rests on them being true *in the
engine* rather than in the four surfaces that mirror them.

**Public.** Every seat's weaponised blue is broadcast exactly, on the
same station reading that fuzzes the hold and the fissile grade to a
word. That asymmetry is deliberate and it is the thing most likely to
be "tidied" by someone later who assumes a rival's readout should be
uniformly coarse — so it is pinned here, next to the fuzzing it
contradicts.

**Capped.** A build that would put a seat over the ceiling is refused
whole, above the debit, and costs nothing. The refusal, not just the
ceiling: the failure mode that matters is a seat paying 255 blue for a
flare it does not receive.

Everything downstream — the prompt block, the orbit policy's cap guard,
the buy panel's ``rack full`` note, the lab's rack validator — reads one
of these two. If this file passes and a fork still gets it wrong, the
bug is in that fork's copy, not here.
"""

from __future__ import annotations

import pytest

from sea_of_colours.game.session import GameSession, Phase
from sea_of_colours.game.weapons import (
    CHAFF_COST_BLUE_PURITY,
    EMP_COST_BLUE_PURITY,
    WEAPONISED_BLUE_CAP,
    decode_rack,
    weaponised_blue,
)


def _rich_session(seed: int = 41) -> GameSession:
    """A session where money is never the reason a build fails.

    Every test here is about the ceiling or the broadcast, so the wallet
    is taken out of the picture entirely — otherwise a refusal could be
    poverty wearing the cap's message.
    """
    sess = GameSession.new(20, 14, seed=seed)
    assert sess.phase == Phase.PLANNING
    for p in sess.players:
        sess.blue_bank[p] = 10_000
        sess.credits[p] = 100_000
    return sess


# ── the ceiling ──────────────────────────────────────────────────


def test_the_cap_is_a_price_not_a_count():
    """Denominated in blue, so a retune moves it instead of widening it."""
    assert weaponised_blue({"emp": 1, "chaff": 0}) == EMP_COST_BLUE_PURITY
    assert weaponised_blue({"emp": 0, "chaff": 1}) == CHAFF_COST_BLUE_PURITY
    assert weaponised_blue({"emp": 2, "chaff": 1}) == (
        2 * EMP_COST_BLUE_PURITY + CHAFF_COST_BLUE_PURITY
    )
    assert weaponised_blue(None) == 0
    assert weaponised_blue({}) == 0


def test_a_seat_may_fill_the_rack_exactly_to_the_line():
    sess = _rich_session()
    p = sess.players[0]
    for _ in range(WEAPONISED_BLUE_CAP // EMP_COST_BLUE_PURITY):
        ok, msg = sess.apply_build_emp(p, 1)
        assert ok, msg
    assert weaponised_blue(sess.weapon_stock[p]) <= WEAPONISED_BLUE_CAP


def test_the_build_over_the_line_is_refused_and_costs_nothing():
    sess = _rich_session()
    p = sess.players[0]
    while True:
        held = weaponised_blue(sess.weapon_stock.get(p))
        if held + EMP_COST_BLUE_PURITY > WEAPONISED_BLUE_CAP:
            break
        assert sess.apply_build_emp(p, 1)[0]

    blue_before = sess.blue_purity_available(p)
    credits_before = sess.credits[p]
    stock_before = dict(sess.weapon_stock[p])

    ok, msg = sess.apply_build_emp(p, 1)
    assert not ok
    assert "cap" in msg.lower()
    # The refusal has to say it was free, because the player's only
    # other reading of "refused" is that they were charged for nothing.
    assert "no blue or credits were spent" in msg.lower()
    assert sess.blue_purity_available(p) == blue_before
    assert sess.credits[p] == credits_before
    assert sess.weapon_stock[p] == stock_before


def test_a_batch_that_would_breach_the_cap_buys_none_of_itself():
    """No partial fill — the same guarantee the wallet checks give."""
    sess = _rich_session()
    p = sess.players[0]
    fits = WEAPONISED_BLUE_CAP // EMP_COST_BLUE_PURITY
    ok, msg = sess.apply_build_emp(p, fits + 1)
    assert not ok, msg
    assert not sess.weapon_stock.get(p, {}).get("emp")


def test_the_cap_counts_the_whole_rack_not_one_weapon_type():
    """Two EMP and a flare is 655, and 655 is over the line."""
    sess = _rich_session()
    p = sess.players[0]
    assert sess.apply_build_emp(p, 2)[0]
    ok, msg = sess.apply_build_chaff(p, 1)
    assert not ok, "a mixed rack must be measured in blue, not per slot"
    assert str(WEAPONISED_BLUE_CAP) in msg


def test_firing_a_weapon_makes_room_again():
    """The cap is on what you HOLD, not on what you have ever built."""
    sess = _rich_session()
    p = sess.players[0]
    fits = WEAPONISED_BLUE_CAP // EMP_COST_BLUE_PURITY
    assert sess.apply_build_emp(p, fits)[0]
    assert not sess.apply_build_emp(p, 1)[0]
    sess.weapon_stock[p]["emp"] -= 1          # as a launch would
    assert sess.apply_build_emp(p, 1)[0]


# ── the broadcast ────────────────────────────────────────────────


def test_a_rival_reads_the_rack_exactly_while_the_vault_stays_a_grade():
    sess = _rich_session()
    p = sess.players[0]
    assert sess.apply_build_emp(p, 1)[0]
    assert sess.apply_build_chaff(p, 1)[0]
    expected = EMP_COST_BLUE_PURITY + CHAFF_COST_BLUE_PURITY

    rival_reading = sess._station_observation(p, fuzzy=True)
    own_reading = sess._station_observation(p, fuzzy=False)

    for label, obs in (("rival", rival_reading), ("self", own_reading)):
        assert obs["arms"]["blue"] == expected, label
        assert obs["arms"]["cap"] == WEAPONISED_BLUE_CAP, label

    # The contrast that makes the rule worth having: the hold is a word
    # to a rival and a number to its owner; the arsenal is a number to
    # everyone. If a later tidy-up fuzzes `arms`, this is the line that
    # should stop it.
    assert "count" not in rival_reading["fullness"]
    assert "count" in own_reading["fullness"]


def test_an_empty_rack_broadcasts_zero_rather_than_nothing():
    """Absent and zero are different readings, and the bar draws both."""
    sess = _rich_session()
    obs = sess._station_observation(sess.players[1], fuzzy=True)
    assert obs["arms"]["blue"] == 0


def test_the_broadcast_survives_a_save_and_reload():
    sess = _rich_session()
    p = sess.players[0]
    assert sess.apply_build_emp(p, 2)[0]
    back = GameSession.from_dict(sess.to_dict())
    assert (back._station_observation(p, fuzzy=True)["arms"]["blue"]
            == 2 * EMP_COST_BLUE_PURITY)


# ── what an agent does with the number ───────────────────────────


def test_every_rack_is_somewhere_in_what_the_total_decodes_to():
    """An agent is handed blue, not counts. Decoding must never LOSE the
    real rack — that is the soundness property the forks depend on, and
    the only one they may depend on.

    It is emphatically not a uniqueness property (v1.36). At 200/300
    against a 600 cap, 600 blue is three EMP or two chaff, and a reader
    that assumes one answer will confidently name the wrong one. See
    the ambiguity test below for the half of the contract that says so.
    """
    for emp in range(4):
        for chaff in range(3):
            total = weaponised_blue({"emp": emp, "chaff": chaff})
            if total > WEAPONISED_BLUE_CAP:
                continue
            racks = decode_rack(total)
            assert (emp, chaff) in [(r["emp"], r["chaff"]) for r in racks], (
                f"{total} blue lost the rack that made it: {racks}"
            )


def test_the_public_total_is_an_exact_quantity_and_an_inexact_inventory():
    """v1.36 — the retune to 200/300 is what bought this ambiguity, and
    it is a feature: a rival reads how much ordnance you are holding,
    never which. Pinned so nobody "fixes" the prices back into a
    lookup table by accident.

    Kept as a property rather than a golden list of totals, because the
    third weapon lands into these same prices and should widen the
    ambiguity without needing this test rewritten.
    """
    collisions = {
        total: racks
        for total in range(0, WEAPONISED_BLUE_CAP + 1)
        for racks in [decode_rack(total)]
        if len(racks) > 1
    }
    assert collisions, (
        "no total under the cap is ambiguous — every public arsenal "
        "figure names exactly one loadout, which is the transparency "
        "the 1-2-3 pricing was meant to take away (§4.9.8)"
    )
    # And decoding must stay sound while being ambiguous: each candidate
    # it offers has to actually cost the total it was asked about.
    for total, racks in collisions.items():
        for rack in racks:
            assert weaponised_blue(rack) == total


def test_a_rack_is_priced_by_the_game_that_holds_it_not_by_todays_dials():
    """v1.36 — prices are stamped per game (§4.9.8). A season played at
    the old chaff price must keep reading at the old chaff price: the
    client prices fired weapons off this table to walk the bar between
    hours, so repricing an archived rack does not just mislabel it, it
    empties a bar that was never that full.
    """
    from sea_of_colours.game.weapons import LEGACY_BLUE_COST_BY_KIND

    sess = _rich_session()
    p = sess.players[0]
    assert sess.apply_build_chaff(p, 1)[0]
    fresh = sess._station_observation(p, fuzzy=True)["arms"]["blue"]
    assert fresh == CHAFF_COST_BLUE_PURITY

    # A save with no stamp predates the retune and loads at the prices
    # it was actually played under, NOT at today's.
    legacy_raw = sess.to_dict()
    legacy_raw.pop("weapon_blue_costs", None)
    legacy_raw.pop("weapon_blue_cap", None)
    legacy = GameSession.from_dict(legacy_raw)
    assert legacy.weapon_prices() == LEGACY_BLUE_COST_BY_KIND
    assert (legacy._station_observation(p, fuzzy=True)["arms"]["blue"]
            == LEGACY_BLUE_COST_BY_KIND["chaff"])

    # …and the view has to publish the same table it is being read at,
    # or the bar and the engine disagree about the same rack.
    pytest.importorskip("sea_of_colours.snowpark.view")
    from sea_of_colours.snowpark.view import _active_rules

    assert _active_rules(legacy)["weapon_blue_costs"] == LEGACY_BLUE_COST_BY_KIND
    assert _active_rules(sess)["weapon_blue_costs"] == sess.weapon_prices()


def test_the_view_tells_agents_the_ceiling_instead_of_making_them_know_it():
    """A fork reads the cap off `meta.rules`; nothing should hardcode 600."""
    pytest.importorskip("sea_of_colours.snowpark.view")
    from sea_of_colours.snowpark.view import _active_rules

    sess = _rich_session()
    assert _active_rules(sess)["weapon_blue_cap"] == WEAPONISED_BLUE_CAP


def test_the_view_publishes_the_prices_the_cap_is_denominated_in():
    """v1.35 — the station bars have to price a rack between the hours of
    a night, so that a fired EMP moves the bar when it launches rather
    than at the next dawn. They read the prices off `meta.rules`; a
    second copy of them in JS would survive exactly until the first
    retune, which is the drift this key exists to prevent."""
    pytest.importorskip("sea_of_colours.snowpark.view")
    from sea_of_colours.snowpark.view import _active_rules
    from sea_of_colours.game.weapons import BLUE_COST_BY_KIND

    published = _active_rules(_rich_session())["weapon_blue_costs"]
    assert published == BLUE_COST_BY_KIND
    # Every kind the cap can be spent on has to be priced, or the client
    # silently under-counts a rack it cannot see the whole of.
    assert set(published) == set(BLUE_COST_BY_KIND)
    assert all(v > 0 for v in published.values())


def test_a_rack_priced_off_the_published_list_matches_the_engine():
    """The client only ever multiplies counts by the published prices.
    Pin that this arrives at the same number `weaponised_blue` does, so
    a bar mid-night cannot disagree with the cap that refused a buy."""
    pytest.importorskip("sea_of_colours.snowpark.view")
    from sea_of_colours.snowpark.view import _active_rules
    from sea_of_colours.game.weapons import weaponised_blue

    prices = _active_rules(_rich_session())["weapon_blue_costs"]
    for stock in ({"emp": 3}, {"chaff": 2}, {"emp": 1, "chaff": 1}, {}):
        as_client_would = sum(prices[k] * n for k, n in stock.items())
        assert as_client_would == weaponised_blue(stock)
