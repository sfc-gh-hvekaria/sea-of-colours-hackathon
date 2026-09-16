"""The nine redsign battles, as data.

Every board here is a night where a PURE was on the table and the
decision was hard. They were captured from real seasons, analysed one at
a time, and each carries the canonical play written in prose plus the
predicates that check it. That analysis is the expensive part — the
boards took a season of play and a lot of reading to produce, and it is
reproduced faithfully here rather than summarised.

**Why these are constructed rather than restored.** The originals were
frozen as Snowflake ``SOC_GAME_SESSION`` rows. That made them exact, and
it made them useless for a hackathon: you cannot run them without an
account, and you cannot vary them at all. A frozen blob has exactly one
difficulty. Since the whole point here is to ask "does your agent still
find the play when the opponent has two harvesters committed and a chaff
in the rack", the board has to be something we can rebuild with the
dials moved — so it is built from its geometry by ``WorldBuilder``, runs
on ``SOC_BACKEND=memory``, and needs nothing but Python.

The trade is honest: these are the documented SHAPE of each battle, not
a byte-exact restoration. Every predicate in ``expect`` is a shape
predicate for the same reason — it reads engine truth and describes what
the play must DO, so a correct-but-different line still passes.

**Seats are normalised to p1.** The originals were captured from
whichever seat happened to own the seam (``own_seam_three_pures`` was
p2, ``own_seam_late_3opp`` was p4). That was an accident of capture, and
carrying it forward would mean every consumer has to ask which seat it
is scoring. Here "we" are always p1 and the geometry is preserved.

To add a board: copy the closest entry, change the geometry, write the
canonical in prose FIRST and only then reduce it to predicates. A
canonical you cannot state in a sentence is one you do not understand
yet, and a predicate written before the prose tends to encode the
implementation you happen to have rather than the play you want.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

# (x, y) and (x, y, purity)
XY = tuple[int, int]
XYP = tuple[int, int, int]

# Purity bands, from RULEBOOK §2.2. Named so the boards below read as
# terrain rather than as magic numbers.
PURE = 255
MASS_HI = 240
MASS = 200
MASS_LO = 160
VEIN_HI = 140
VEIN = 105
VEIN_LO = 60
TRACE = 30


@dataclass(frozen=True)
class Board:
    """One night, one hard decision.

    ``question`` and ``canonical`` are load-bearing documentation, not
    decoration: they are what a person (or an agent) reads to understand
    why a run failed, and they are printed alongside every result. The
    predicates alone tell you THAT the play was wrong; only the prose
    tells you what the right one was.
    """

    id: str
    day: int
    shape: str
    question: str
    canonical: str

    # Geometry. ``pures`` are the jackpots; everything else is the
    # terrain that makes the decision interesting.
    pures: Sequence[XY] = ()
    red: Sequence[XYP] = ()
    green: Sequence[XY] = ()

    # Who owns the beacon. "ours" means we found the pure and the sign
    # is on everyone's board; "rival" means somebody else did and we are
    # attacking. The distinction changes the whole night.
    redsign_center: XY | None = None
    redsign_owner: str = "ours"

    # Is the pure drop-legal right now, or is it an ECHO we have to
    # light with a probe first? Fogged pures cost an hour and a probe,
    # which is the difference between a 2-hour smash and a 3-hour one.
    pure_is_fogged: bool = False

    # Our kit at the top of the night.
    harvesters: int = 2
    probes_in_stock: int = 4
    our_probes: Sequence[XY] = ()

    # The opposition as captured. The ladder in ``ladder.py`` moves
    # these; this is the baseline the analysis was written against.
    rival_probes: Sequence[XY] = ()
    rival_harvesters: Sequence[XY] = ()

    season_day_cap: int = 7
    expect: Mapping[str, object] = field(default_factory=dict)
    observation: str = ""
    baseline: str = ""

    @property
    def is_final_night(self) -> bool:
        return self.day >= self.season_day_cap


# ── the corpus ──────────────────────────────────────────────────────
#
# Ordered roughly by how much is going on, not by capture date: a reader
# meeting these for the first time should start at the quiet end.

BOARDS: tuple[Board, ...] = (

    Board(
        id="early_solo_seam",
        day=2,
        shape="own redsign, day 2, one opponent, nothing watching",
        question=(
            "Day 2. Our pure is LIVE at (15,22) with mass around it. One "
            "harvester, two probes. The one opponent's probes are a day old "
            "and nowhere near the seam: no weapons, no rival vision on the "
            "pure. This is the lowest-risk board in the set. Does the single "
            "harvester take the whole seam, or does it flinch?"
        ),
        canonical=(
            "TAKE EVERYTHING. Nothing on this board justifies a short chain. "
            "Drop ON the pure at (15,22) — landing auto-harvests it — then "
            "walk the mass at (15,23) and (14,23) and push into the fog "
            "around the seam. On a one-harvester night the sweep must open "
            "on the pure. A pure-only grab is the correct play under danger "
            "and the wrong play here: the extension is nearly free, so "
            "stopping short is the failure, not the safe choice."
        ),
        pures=[(15, 22)],
        red=[(15, 23, MASS), (14, 23, MASS_LO), (16, 24, MASS_LO),
             (15, 21, VEIN), (14, 22, VEIN_LO), (16, 22, TRACE)],
        redsign_center=(15, 22),
        harvesters=1,
        probes_in_stock=2,
        rival_probes=[(34, 19), (19, 17)],
        expect={
            "take_pure": True,
            "no_green": True,
            "deploy_all": True,
            "min_steps_past_pure": 3,
            "compiler_clean": True,
            "note": (
                "lowest-risk board: one opponent, no vision, no weapons. "
                "The extension is nearly free, so a SHORT grab is the failure"
            ),
        },
        baseline=(
            "V12 landed on a vein at (15,21) and reached the pure at hour 2 — "
            "~1052 of a possible ~1369 on the safest board in the suite. Its "
            "own reasoning described the right play; the menu had no option "
            "that did pure-first-then-mass."
        ),
    ),

    Board(
        id="no_probes_two_units",
        day=3,
        shape="own redsign, early, one opponent, ZERO probes",
        question=(
            "Day 3 of 7. Our pure is LIVE at (31,18) with mass at (31,17) "
            "and (32,17). A rival seam sits far west at ~(14,22). Two "
            "harvesters and ZERO probes in stock. Does it get both units on "
            "the board with no vision to buy, and does it go inward (mine "
            "our own seam out) or outward (attack theirs)?"
        ),
        canonical=(
            "Both answers are acceptable and the choice is the interesting "
            "part. INWARD: smash the pure with one unit, mine the seam to "
            "exhaustion with the other. OUTWARD: take pure plus the two mass "
            "with one, send the other at the rival seam. With ONE opponent "
            "and NO chaff there is nothing to punish the extra exposure, so "
            "outward is slightly preferred — but a migration to inward here "
            "means something has made the agent timid, and that is a "
            "regression no per-option check would catch. What must not "
            "regress: dropping ON the pure rather than walking to it, and "
            "getting both harvesters out while holding zero probes."
        ),
        pures=[(31, 18)],
        red=[(31, 17, MASS), (32, 17, MASS), (32, 18, VEIN),
             (33, 18, TRACE), (14, 22, MASS_LO), (15, 22, VEIN)],
        redsign_center=(31, 18),
        harvesters=2,
        probes_in_stock=0,
        our_probes=[(31, 19)],
        rival_probes=[(14, 23)],
        expect={
            "no_green": True,
            "deploy_all": True,
            "no_wake_reentry": True,
            "max_probes": 0,
            "compiler_clean": True,
            "note": (
                "ZERO probes — score that both harvesters fly and the "
                "doctrine, never option ids"
            ),
        },
        baseline=(
            "V12 played outward and chose well. Zero compiler "
            "interventions, both plans intact."
        ),
    ),

    Board(
        id="echo_pure_blind_rival",
        day=4,
        shape="own echo pure, live rival seam, one opponent",
        question=(
            "Our pure at (31,18) is confirmed in ECHO — one night old, exact "
            "coordinate. A rival redsign at ~(17,6) is live and fully "
            "fogged, with their finder probe at (16,9). Two harvesters, "
            "three probes. Can it bank its own jackpot and blind the rival's "
            "eye in the same night?"
        ),
        canonical=(
            "Bank the echo pure and blind the rival finder at (16,9) "
            "simultaneously — but arrive FAST. The pure is public: the "
            "redsign told every seat, so this is a race and the whole "
            "question is speed of arrival. A four-step approach is too long. "
            "Either eat the green to take the shorter line (-100 to arrive a "
            "hop sooner is cheap against 765 lost to a rival) or hot-drop on "
            "the coordinate the echo already gives you. Do not hold probes "
            "'for tomorrow's frontier' on night 4 of 7 with two live seams."
        ),
        pures=[(31, 18)],
        red=[(31, 17, MASS), (32, 17, MASS_LO), (32, 18, VEIN),
             (30, 18, VEIN_LO), (17, 6, MASS), (17, 7, MASS_LO)],
        green=[(30, 17)],
        redsign_center=(31, 18),
        pure_is_fogged=True,
        harvesters=2,
        probes_in_stock=3,
        our_probes=[(33, 18)],
        rival_probes=[(16, 9), (18, 7)],
        expect={
            "take_pure": True,
            "no_green": True,
            "deploy_all": True,
            "no_wake_reentry": True,
            "compiler_clean": True,
            "note": (
                "the pure is an ECHO found by a harvester walk — do NOT "
                "require pure_first, the discovery order is the point"
            ),
        },
        baseline=(
            "V12 reasoned correctly about the echo pure, then the compiler "
            "capped both chains to two steps — cutting the pure itself — and "
            "spent two held probes far from either seam."
        ),
    ),

    Board(
        id="two_pures_poker",
        day=5,
        shape="own AND rival pure both live, mutual vision, one opponent",
        question=(
            "Day 5 of 7, fully resourced: two harvesters, four probes. TWO "
            "pures are live — ours at (31,18) surrounded by vein only, and "
            "the RIVAL's at (16,6) with mass beside it. Both sit under a "
            "rival probe, so they see both too. Does it take both, and does "
            "it take them FAST?"
        ),
        canonical=(
            "Redsign poker, symmetric — and the whole night is PURE AND PURE "
            "ONLY: no mass detours, no vein on the way in. DOUBLE SMASH: "
            "drop one harvester ON (16,6) and the other ON (31,18) at hour "
            "one, both auto-harvest on landing, lift both at hour two. Worst "
            "case they lift one out and crash us on the other and we still "
            "deny; there is no branch where they take both. The alternative "
            "is to blind their eye on one seam and double-drop the other, "
            "which has a higher ceiling and a lower floor. Either beats a "
            "vein-first walk that concedes the race by four hours to collect "
            "three vein on the way in."
        ),
        pures=[(31, 18), (16, 6)],
        red=[(32, 18, VEIN), (33, 18, VEIN_LO), (30, 18, VEIN),
             (16, 7, MASS_HI), (17, 7, MASS), (18, 8, VEIN)],
        redsign_center=(31, 18),
        harvesters=2,
        probes_in_stock=4,
        our_probes=[(14, 8), (32, 19)],
        rival_probes=[(16, 9), (32, 17)],
        expect={
            "take_pure": True,
            "pure_first": True,
            "no_green": True,
            "deploy_all": True,
            "max_steps_past_pure": 0,
            "no_wake_reentry": True,
            "compiler_clean": True,
            "note": (
                "PURE-ONLY tempo: each unit DROPS on a pure, no mass or "
                "vein detours"
            ),
        },
        baseline=(
            "V12 took both pures but reached the rival's at step four via a "
            "vein-first walk — hour five on a cell it could have landed on "
            "at hour one. Right content, wrong tempo."
        ),
    ),

    Board(
        id="race_the_watched_pure",
        day=5,
        shape="own echo pure inside a rival probe disk, sign two nights old",
        question=(
            "Our pure at (31,18) is known by ECHO only and sits INSIDE the "
            "rival's probe disk at (32,17) — they can land on it an hour "
            "before we can. A rival sign at ~(17,6) is also two nights old "
            "with two of their probes on it and none of ours. Two "
            "harvesters, four probes. Does it win the race, and does it "
            "route the second harvester somewhere the first has not been?"
        ),
        canonical=(
            "Take the shortest line to the pure that exists and accept you "
            "may arrive to a stripped cell — tempo is the whole board. Then "
            "INVERT THE RING for the second unit: drop ON (32,17), which "
            "auto-harvests the mass AND crushes their finder probe in the "
            "same move at hour one, then walk OUTWARD away from the first "
            "unit's wake. Value and denial both banked before anything can "
            "go wrong. Walking the ring the other way crosses two cells the "
            "first unit already stripped — two synthetic greens eaten at "
            "-100 each to reach a pure already in the hold. Passing on the "
            "rival seam is correct: a two-night-old sign with two of their "
            "probes on it has almost certainly been banked already."
        ),
        pures=[(31, 18)],
        red=[(32, 17, MASS_HI), (31, 17, MASS), (32, 16, VEIN),
             (31, 16, TRACE), (32, 18, VEIN), (17, 6, MASS)],
        redsign_center=(31, 18),
        pure_is_fogged=True,
        harvesters=2,
        probes_in_stock=4,
        our_probes=[(33, 19)],
        rival_probes=[(32, 17), (15, 7), (14, 5)],
        expect={
            "no_green": True,
            "deploy_all": True,
            "no_wake_reentry": True,
            "compiler_clean": True,
            "note": "core hygiene only — the canonical is still coarse here",
        },
        baseline=(
            "V12 picked the right shape but the offered ring ran backwards "
            "through its own wake, and the compiler spent two held probes on "
            "stacked cells two apart."
        ),
    ),

    Board(
        id="three_pures_one_unit",
        day=6,
        shape="three pures, one surviving harvester, chaff took the other",
        question=(
            "Three pures at (18,2) (19,3) (20,3). One surviving harvester — "
            "chaff killed the other. Night 6 of 7. The offered chains all "
            "open on (19,4), which is not a pure, and the long one lifts "
            "inside the same chaff window that killed the first unit. Does "
            "it reorder, or does it take the menu at face value?"
        ),
        canonical=(
            "Reorder the same cells so value comes first and the conflict "
            "disappears. Drop ON (18,2) at hour zero — that banks a pure on "
            "landing — then step (19,2), (19,3), (20,3) and lift at hour "
            "four. Three pures, clear of the hours where the chaff lands. It "
            "sheds two vein cells and beats the short chain by about 750 "
            "while being SAFER than the long one. Value-first ordering is "
            "what makes a chain safe to trim."
        ),
        pures=[(18, 2), (19, 3), (20, 3)],
        red=[(19, 2, VEIN), (19, 4, VEIN_HI), (18, 3, MASS_LO),
             (20, 2, TRACE), (21, 3, VEIN_LO)],
        redsign_center=(19, 3),
        harvesters=1,
        probes_in_stock=3,
        our_probes=[(20, 4)],
        rival_probes=[(22, 5)],
        rival_harvesters=[(23, 6)],
        expect={
            "take_pure": True,
            "pure_first": True,
            "no_green": True,
            "no_wake_reentry": True,
            "deploy_all": True,
            "max_steps_past_pure": 1,
            "compiler_clean": True,
            "note": (
                "three visible pures under mutual visibility: PURE-ONLY "
                "tempo, the landing itself must bank one"
            ),
        },
        baseline=(
            "V12 took the two-pure chain — left 841 red and a whole pure "
            "standing on the board."
        ),
    ),

    Board(
        id="blind_grab_rival_seam",
        day=6,
        shape="RIVAL redsign, pures fogged, late, one opponent",
        question=(
            "A rival redsign beacon at ~(18,3) with their finder probe at "
            "(20,3). The pures are NOT visible to us — we know a jackpot is "
            "in there and roughly where, and nothing more. Does it blind the "
            "finder, and does the harvester land inside the disk it just "
            "opened?"
        ),
        canonical=(
            "Blind ON the finder at (20,3) — that is the premise of the "
            "move, not a lucky side effect — then drop the harvester at "
            "(19,3) inside the quadrant the blind just opened and comb. Comb "
            "length follows rival vision: long if the blind removes all "
            "rival sight, short while a second rival probe still watches. "
            "Score the comb riding the smear gradient and the finder being "
            "blinded, never a fixed cell: the pure is invisible, so a "
            "canonical that names one is cheating."
        ),
        pures=[(18, 2), (19, 3), (20, 3)],
        red=[(19, 2, VEIN), (18, 3, MASS_LO), (19, 4, VEIN_HI),
             (17, 3, TRACE), (16, 3, TRACE)],
        redsign_center=(18, 3),
        redsign_owner="rival",
        pure_is_fogged=True,
        harvesters=1,
        probes_in_stock=4,
        rival_probes=[(20, 3)],
        expect={
            "comb_gradient": True,
            "no_green": True,
            "denial_probe": True,
            "max_probes": 4,
            "compiler_clean": True,
            "note": (
                "the pure is NOT visible — score the comb riding the smear "
                "gradient and the finder being blinded, never a fixed cell"
            ),
        },
        baseline=(
            "V12 blinded (20,3) correctly, then walked the harvester WEST "
            "away from the pures the blind had just uncovered."
        ),
    ),

    Board(
        id="crowded_echo_seam",
        day=6,
        shape="own echo pure, three opponents, five rival probes, no vision",
        question=(
            "Day 6 of 7, four seats. Our pure at (16,6) is ECHO — two nights "
            "stale and fogged, so it needs a hot-drop probe to light before "
            "we can land. FIVE rival probes cover the seam and we have none "
            "on it. Only one enemy disk actually reaches the pure, but "
            "theirs is a deterministic hour-one grab and ours is contingent "
            "at hour two. Does the second harvester stay on the seam?"
        ),
        canonical=(
            "WE GO ANYWAY — this is the commitment floor. Conceding costs "
            "the same as trying and wins nothing; if they fumble, it is the "
            "season. Tempo SIZES the commitment, it never cancels it. Probe "
            "(19,6) at hour one to light the cell — placed OFF the pure so "
            "it survives the landing and its disk makes the second wave "
            "legal off the same shot — drop ON (16,6) at hour two, lift at "
            "three. Second unit takes the mass DOWN THE COLUMN at (16,7) and "
            "(16,8). NOT (17,7)/(17,8): a rival stripped those last night "
            "and they are synthetic green now, -100 each. NOT a repeat of "
            "the pure: there is no chaff on this board and chaff is the only "
            "thing that denies an hour-one landing, so a second pass insures "
            "against nothing and just pays green. Probes AFTER the "
            "harvesters are away."
        ),
        pures=[(16, 6)],
        red=[(16, 7, MASS_HI), (16, 8, VEIN), (17, 6, TRACE),
             (17, 5, VEIN_LO), (24, 7, MASS_LO)],
        green=[(17, 7), (17, 8)],
        redsign_center=(16, 6),
        pure_is_fogged=True,
        harvesters=2,
        probes_in_stock=4,
        rival_probes=[(16, 9), (18, 5), (14, 7), (20, 8), (13, 4)],
        rival_harvesters=[(18, 9), (12, 6)],
        expect={
            "take_pure": True,
            "pure_first": True,
            "no_green": True,
            "no_wake_reentry": True,
            "no_repeat_pure": True,
            "deploy_all": True,
            "seam_commitment": 2,
            "probes_after_harvesters": True,
            "compiler_clean": True,
            "note": (
                "tempo sizes the commitment, never cancels it; no chaff on "
                "the board so a repeat of the pure insures against nothing"
            ),
        },
        baseline=(
            "V12 banked the pure, then the packager DELETED its second run "
            "('cell already has a drop') and the corrector dumped that "
            "harvester twenty cells away. The agent had diagnosed the defect "
            "in its own reasoning and had no way to express the fix."
        ),
    ),

    Board(
        id="final_night_ring",
        day=7,
        shape="final night, own seam, unwatched, one opponent",
        question=(
            "FINAL NIGHT. Own pure at (31,18) with mass at (31,17) and "
            "(32,17). Two harvesters, one probe. The seam is UNWATCHED — no "
            "rival probe reaches it. Last night it lost a race on the "
            "RIVAL's seam. Does it convert the night, or does it carry "
            "yesterday's fear onto a board where nobody can see it?"
        ),
        canonical=(
            "Smash the pure, then strip the ring with the SECOND unit. "
            "Denial is the reason, not danger: it is the last night and the "
            "redsign is public, so they will blind-grab our seam if we leave "
            "the mass standing. Drop ON (31,18), lift hour two. Second unit "
            "opens on the MASS at (31,17), steps (32,17), lifts — because "
            "the pure is already banked, a jam on this wave costs only ring "
            "mass. The probe goes to denial; final-night probes have no "
            "frontier value. This beats taking the pure alone by about 591 "
            "of swing, not of points: the same money either way, but the "
            "ring is 591 the rival cannot have."
        ),
        pures=[(31, 18)],
        red=[(31, 17, MASS_HI), (32, 17, MASS), (32, 16, VEIN_LO),
             (33, 17, TRACE), (30, 18, TRACE)],
        redsign_center=(31, 18),
        harvesters=2,
        probes_in_stock=1,
        our_probes=[(34, 19)],
        rival_probes=[(14, 23), (5, 15), (7, 8)],
        expect={
            "take_pure": True,
            "pure_first": True,
            "no_green": True,
            "no_wake_reentry": True,
            "no_repeat_pure": True,
            "deploy_all": True,
            "compiler_clean": True,
            "note": (
                "smash the pure, then a SEPARATE unit strips the ring — "
                "no_wake_reentry is the one that catches a second wave "
                "re-entering the cell the first just stripped"
            ),
        },
        baseline=(
            "V12 banked ~1321 and left ~706 by taking the pure alone on an "
            "unwatched seam, justifying the short night by citing contest on "
            "a seam nobody could see."
        ),
    ),

    # The odd one out, and deliberately so. Everything above is a
    # redsign night; this is the other 80% of a season.
    Board(
        id="plain_night_armed",
        day=4,
        shape="NO redsign, no pure — an ordinary working night, rack loaded",
        question=(
            "Day 4 of 7. There is no pure anywhere and no redsign on "
            "anybody's board: just a long vein-and-mass seam running "
            "north-east, two harvesters, three probes. One rival probe sits "
            "at (18,9) with live vision over the near half of our seam, and "
            "two rival harvesters are working their own ground at "
            "(9,20)/(10,20). Every other board in this suite asks what your "
            "agent does about a jackpot. This one asks the question the "
            "other 80% of a season actually asks: on an ordinary night, "
            "with ordnance in the rack and nothing dramatic to spend it on, "
            "does the agent still run a competent seam — and does it treat "
            "the rack as a tool or as decoration?"
        ),
        canonical=(
            "WORK THE SEAM, PROPERLY. Both harvesters out, both onto RED, "
            "long combs up the mass at (16,10)-(18,12), lift both. That is "
            "the whole scoring play and it is not sophisticated. "
            "\n\n"
            "The ordnance is the interesting part, and BOTH answers are "
            "defensible — which is why nothing here scores it. Firing is "
            "justifiable: their probe at (18,9) is the only eye on our seam, "
            "an EMP removes it for the night and a chaff can strand the "
            "committed pair on their lift. Holding is equally justifiable: "
            "a charge spent here is a charge not available the night a pure "
            "surfaces, and the hour it costs is an hour not walking ore. "
            "What is NOT defensible is never having considered it. Read the "
            "ORDNANCE line in the report against your own doctrine — if you "
            "told your agent 'weapons only for redsigns' then silence here "
            "is the correct result and you have just proved it; if you did "
            "not, silence means the rack is decoration."
        ),
        pures=[],
        red=[(15, 9, VEIN), (16, 10, MASS), (17, 10, MASS_HI),
             (18, 11, MASS), (17, 12, MASS_LO), (16, 12, VEIN_HI),
             (15, 11, VEIN), (19, 12, VEIN_LO), (14, 10, TRACE),
             (18, 13, VEIN), (19, 13, MASS_LO)],
        redsign_center=None,
        harvesters=2,
        probes_in_stock=3,
        # Last night's probe, still lit over the seam. Every other board
        # gets its drop legality from a pure; this one has none, and
        # without a live beacon the seat cannot legally land at all
        # (§3.9.7) — the board would be testing the drop rule rather
        # than the night. A standing eye is also just what an ordinary
        # day-4 seat actually has.
        our_probes=[(16, 11)],
        rival_probes=[(18, 9)],
        rival_harvesters=[(9, 20), (10, 20)],
        expect={
            "deploy_all": True,
            "no_green": True,
            "min_red_cells": 6,
            "probes_after_harvesters": True,
            "compiler_clean": True,
            "note": (
                "the control board. No pure, no sign, no drama — if an "
                "agent only performs on jackpot nights it is not actually "
                "good, and this is the board that says so. Weapon use is "
                "REPORTED here, never scored: doctrine on when to spend a "
                "charge is the attendee's call, not the suite's"
            ),
        },
        baseline=(
            "V12 works this board competently and has never fired a weapon "
            "on it. That is not a bug and not obviously a mistake — it is "
            "the shipped doctrine, which holds charges for redsign nights. "
            "It is here as the reference point for a fork that decides "
            "otherwise."
        ),
    ),
)


BY_ID: Mapping[str, Board] = {b.id: b for b in BOARDS}


def get(board_id: str) -> Board:
    """Look up a board, with a message that lists the alternatives.

    The reader is often an agent that guessed the name, so the error
    carries the whole menu rather than just saying no.
    """
    try:
        return BY_ID[board_id]
    except KeyError:
        raise KeyError(
            f"no board named {board_id!r}. Available: "
            + ", ".join(sorted(BY_ID))
        ) from None
