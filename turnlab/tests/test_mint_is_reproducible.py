"""A board set has to be a fact you can hand someone.

This is the test that would have caught the expensive one. The three
`dd868733` boards were minted from "seed 4242", and when we later
needed to re-mint them — they were missing their replay frames — seed
4242 produced a completely different season. Not slightly different:
no redsign at all where the originals had a fight on night one. The
boards could not be regenerated, and the notes written about them
described a season that no longer existed anywhere.

The cause was not the map. `seed` pins the map fine. It was that the
heuristic seeds its own RNG off the *session id* — deliberately, so
two seats don't open identically — and the session id was a fresh
`uuid4` on every run. So the map was reproducible and the play was
not, which is the worst of the three possible arrangements because it
looks reproducible right up until you check.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile

import pytest

from sea_of_colours.snowpark.file_store import FileSocStore
from turnlab import mint

SEATS = {"p1": "red_harvest", "p2": "red_harvest_lite"}


def _mint(seed: int = 7):
    """One offline mint, quiet, into a store of its own."""
    store = FileSocStore(tempfile.mkdtemp())
    with contextlib.redirect_stdout(io.StringIO()):
        result = mint.mint(SEATS, store=store, days=2, seed=seed,
                           season_name="TEST_REPRO", prefix="LAB")
    return store, result


def _blob(store, sid):
    raw = (store.load_session(sid) or {}).get("json_state")
    while isinstance(raw, (str, bytes)):
        raw = json.loads(raw)
    return raw or {}


def _canonical(store, sid):
    return json.dumps(_blob(store, sid), sort_keys=True, default=str)


def test_minting_the_same_arguments_twice_gives_the_same_boards():
    a_store, a = _mint()
    b_store, b = _mint()

    assert a.session_id == b.session_id, (
        "the season id itself must be derived, not drawn — the heuristic "
        "seeds its RNG from it, so a random id makes the play random"
    )
    assert [f.snapshot_id for f in a.frozen] == [f.snapshot_id for f in b.frozen]
    assert a.frozen, "a two-day mint should freeze something"

    for frozen in a.frozen:
        assert _canonical(a_store, frozen.snapshot_id) == \
            _canonical(b_store, frozen.snapshot_id), (
                f"{frozen.snapshot_id} differs between two mints of one seed"
            )


def test_a_different_seed_is_still_a_different_season():
    """The guard above must not have been won by pinning everything."""
    a_store, a = _mint()
    b_store, b = _mint(8)
    assert a.session_id != b.session_id
    assert _canonical(a_store, a.frozen[0].snapshot_id) != \
        _canonical(b_store, b.frozen[0].snapshot_id)


def test_minting_leaves_uuid4_alone_afterwards():
    """Reproducibility is bought with a global patch; it must not leak.

    `uuid4` is patched process-wide for the duration of a mint, which
    is tolerable for an offline authoring step and catastrophic if it
    outlives one — every session id in the process would start
    repeating.
    """
    import uuid

    before = uuid.uuid4
    _mint()
    assert uuid.uuid4 is before
    assert len({uuid.uuid4() for _ in range(50)}) == 50


def test_a_board_carries_last_nights_replay():
    """The frames are why we went looking at all.

    `snapshot.take` copied replay frames through a SQL path that a file
    store does not have, and said nothing when it copied none. An agent
    on such a board is told "YOU ORDERED: (no orders on record)" about
    a night it played, and reasons from that.
    """
    store, result = _mint()
    second_day = [f for f in result.frozen if f.day == 2]
    if not second_day:
        pytest.skip("this seed did not reach a second day")
    for frozen in second_day:
        frames = store.list_replay_frames(frozen.snapshot_id, 1, 1)
        assert frames, (
            f"{frozen.snapshot_id} has no frames for night 1, so LAST NIGHT "
            "degrades on a board that certainly had one"
        )
