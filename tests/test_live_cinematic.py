"""The night cinematic plays once per seat, per night (v1.17).

Two multiplayer faults, one theme: whether you saw the night animate
depended on incidental timing rather than on the game.

1. **A backgrounded tab lost the night entirely.** ``pollLiveSync`` skips
   while ``document.hidden``, and treats its first sample as a baseline it
   never acts on. A player who submitted and switched tabs therefore had
   no baseline at all, so the first poll after they came back sampled the
   *already resolved* state, called that the baseline and returned. The
   board sat shimmering in the pre-night view while the composer happily
   let them plan the next turn.

2. **An incidental refresh stole the animation.** The trigger was "did
   this fetch bring frames newer than the ones we hold", so any plain
   ``pullAllMaps()`` — closing the orbit report being the usual one —
   loaded the opponent's new night first, and the real cinematic pull then
   saw nothing new and hard-cut to the end state. Two seats closing that
   report in a different order saw visibly different turns.

There is no JS test runner here, so these pin the invariants by reading
``app.js``, the same approach as ``tests/test_seat_links.py``. They are
deliberately about *structure*: each one fails if the specific mechanism
that caused a fault is reintroduced.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_APP_JS = Path(__file__).resolve().parents[1] / "server" / "static" / "app.js"
SRC = _APP_JS.read_text(encoding="utf-8")


def _body(header: str) -> str:
    """Source of one top-level function in the app.js IIFE.

    Everything in that file sits at two-space indent, so a closing ``\\n  }``
    is a reliable terminator.
    """
    m = re.search(header + r"(.*?)\n  \}", SRC, re.S)
    assert m, f"not found — was it renamed? /{header}/"
    return m.group(1)


# Destructured multi-line signature, so consume up to the real opening brace.
_PULL_ALL_MAPS = r"async function pullAllMaps\([\s\S]*?\) \{"


# ── fault 2: the animation must not be a side effect of fetch order ──

def test_the_replay_refresh_reports_the_day_it_holds():
    """``lastDay`` is the durable fact a caller can gate on; the frame diff
    is consumed by whoever fetches first."""
    assert "lastDay: incomingLastDay" in SRC
    # Both the no-frames and the error return paths must carry the key too,
    # or a caller reading info.lastDay silently gets undefined.
    assert SRC.count("lastDay: 0") >= 2


def test_the_cinematic_is_gated_on_the_day_not_the_frame_diff():
    body = _body(_PULL_ALL_MAPS)
    assert "_infoDay > _lastCinematicDay" in body
    assert "info.newDayLanded" not in body, (
        "gating on newDayLanded is the bug: any earlier refresh consumes it"
    )


def test_the_cinematic_starts_at_the_days_own_first_tick():
    """Starting from a stale tick count is meaningless once another fetch
    has already loaded the frames."""
    assert "_playNightCinematic(findFirstCinematicTickOfDay(" in SRC


def test_the_start_tick_includes_the_synthetic_dusk_beat():
    """DUSK is the orbital-resolve beat the night opens on, and it carries
    its day on the tick rather than on a frame."""
    body = _body(r"function findFirstCinematicTickOfDay\(day\) \{")
    assert 't.slot === "dusk"' in body
    assert "Number(t.day) === want" in body


def test_the_day_is_claimed_before_the_cinematic_is_awaited():
    """The play awaits; a poll landing mid-play must not queue a second run
    of the same night."""
    body = _body(_PULL_ALL_MAPS)
    assert "_lastCinematicDay = _infoDay;\n      await _playNightCinematic" in body


@pytest.mark.parametrize(
    "header",
    [
        r"async function loadWatcherSession\(id\) \{",
    ],
)
def test_load_paths_claim_the_night_they_find(header: str):
    """Opening an existing season must not animate a night that happened
    before you arrived."""
    assert "claimCinematic: true" in _body(header)


def test_joining_a_seat_claims_the_night_already_played():
    assert re.search(
        r"Joining an in-progress game.*?claimCinematic: true", SRC, re.S
    ), "the join path must claim, or a new seat gets an unrelated cinematic"


def test_closing_the_report_does_not_claim_the_night():
    """The regression that made the orbit report decide whether you got an
    animation. This refresh happens *during* live play, with a cinematic
    pending, so it must leave the night unclaimed."""
    body = _body(r"function closeReport\(\) \{")
    assert "void pullAllMaps();" in body
    assert "claimCinematic" not in body


# ── fault 1: a hidden tab must defer the night, not lose it ──────────

def test_live_sync_seeds_a_baseline_when_it_starts():
    assert "seedLiveSyncBaseline" in _body(r"function startLiveSync\(\) \{")


def test_the_seed_ignores_a_hidden_tab():
    """The whole point: the poll can't seed while hidden, which is exactly
    when the baseline is needed. Recording state paints nothing, so it is
    safe in a background tab."""
    body = _body(r"async function seedLiveSyncBaseline\(\) \{")
    assert "document.hidden" not in body
    assert "statusSignature(st)" in body


def test_the_seed_does_not_overwrite_a_real_poll_sample():
    """A poll that already seeded has seen a transition we must not erase."""
    body = _body(r"async function seedLiveSyncBaseline\(\) \{")
    assert 'if (liveSyncSig !== "") return;' in body


def test_returning_to_the_tab_polls_immediately():
    """Otherwise they wait out the remainder of a 2.5s tick staring at a
    board that hasn't advanced."""
    m = re.search(
        r'addEventListener\("visibilitychange".*?\}\);', SRC, re.S
    )
    assert m, "no visibilitychange handler"
    assert "pollLiveSync" in m.group(0)


# ── fault 3: both seats must watch the same orbit resolve ────────────

def test_the_orbit_night_opening_is_a_shared_beat():
    """It used to be inline in the submit handler, so only the seat whose
    submit *resolved* the phase saw the card and the sunset. Everyone who
    locked in first got a silent snap to the resolved board."""
    body = _body(r"async function playOrbitNightOpening\(day\) \{")
    assert "showMapTitleCard(" in body
    assert 'runHorizonSweep(mapPlayer, "sunset")' in body
    # Card first, then the sweep — the order the resolving seat always had.
    assert body.index("showMapTitleCard(") < body.index("runHorizonSweep(")


def test_the_submitting_seat_uses_the_shared_beat():
    m = re.search(
        r"orbital praxis closes the day(.*?)await pullAllMaps", SRC, re.S
    )
    assert m, "orbit submit handler not found"
    assert "await playOrbitNightOpening(body?.day);" in m.group(1)
    assert "showMapTitleCard" not in m.group(1), "inlining it re-splits the seats"


def test_the_waiting_seat_opens_the_night_too():
    """The fault the player reported: night never began on the seat that
    was waiting, so the map stayed shimmering in the pre-night view."""
    body = _body(r"async function pollLiveSync\(\) \{")
    assert "await playOrbitNightOpening(liveSyncDay);" in body


def test_the_waiting_seat_opens_the_night_only_on_an_orbit_resolve():
    """A night resolve is carried by the cinematic; running the day-closing
    beat there too would double up."""
    body = _body(r"async function pollLiveSync\(\) \{")
    assert 'prevPhase === "orbit" && liveSyncPhase !== "orbit"' in body


def test_the_waiting_seat_opens_the_night_before_it_repaints():
    """Same order as the resolving seat, or the board snaps to the resolved
    state and *then* announces the night."""
    body = _body(r"async function pollLiveSync\(\) \{")
    assert body.index("await playOrbitNightOpening") < body.index(
        "await pullAllMaps("
    )


def test_the_poll_still_skips_work_while_hidden():
    """Deferring is the fix, not polling harder: rAF is throttled in a
    background tab, so a cinematic started there would stall half-played."""
    assert "if (document.hidden) return;" in _body(
        r"async function pollLiveSync\(\) \{"
    )
