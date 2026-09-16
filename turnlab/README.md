# Turn Lab

One frozen night, opened in the real game interface.

Pick a board on `/lab`, press go, and you land in the ordinary command
centre on a clone of that turn — the real board, the real fog toggles,
the real hour transport, the real replay — sitting on a night that
already happened, with nobody's orders in yet. Open the **AGENT** tab
and invoke an agent into a seat to watch it plan. Invoke both seats and
the engine resolves the night and plays it out exactly as a live night
does.

```bash
python run_web.py            # then open /lab
```

No credentials, no `SOC_BACKEND`, no Snowflake. The lab keeps its own
data in `data/` and works entirely offline.

## What a board is

A **snapshot**: a complete, inert session the engine wrote just before a
seat planned. Not a reconstruction, not a replay frame — the state, as
the engine saved it. Cloning one and playing it *is* the engine playing
it.

Two earlier designs got this wrong in ways worth remembering, because
both look reasonable until you try to play them:

- **Hand-typed boards.** A human read a season's agent cards and typed
  the position back in. Half of it landed in an `inferred` dict because
  it was guesswork.
- **Replay frames.** Exact for *looking* at, and the lab still uses them
  for history. But a frame is rendered paint — `bg`/`ch`/`fg` — with no
  tile and no purity, so nothing can be played from one. Recovering the
  terrain meant re-running the season, or rolling the final grid back
  through the harvest log, and that road ends with the lab
  reimplementing engine rules it does not own.

Boards are discovered from the store rather than declared, so minting a
new set makes them appear with no code change.

## A board carries its own memory

A position is not the whole of what a seat knew. A V12 seat also has its
STRATEGY JOURNAL — the INTENT it set each night and the REFLECTION it
wrote the morning after — and a night-six agent that has been told it
kept no notes plans a visibly different turn.

That journal is frozen into the board as `journal.json`, cut at the
night before, and read from there. It used to be recovered from the
source season at invoke time, which worked on the machine that made the
board and nowhere else: the boards travel in the repo and their seasons
do not. It also made the turn depend on who you were — an attendee
without the account got an amnesiac agent *and* diffed it against a
baseline recorded here with the memory intact, so the divergence view
opened on a disagreement that was nobody's fork.

Refresh it from whichever machine can still see the seasons:

```bash
python -m turnlab.freeze          # what it would capture
python -m turnlab.freeze --write  # write it into the boards
```

Most of the library reports "no seat kept one", and that is the correct
answer rather than a failure: the minted boards were played by
RED_HARVEST, which keeps no diary. The file is still written, because an
empty journal is a *claim* the lab can make offline, where a missing
file sends it hunting for a season and reporting a fault.

## The two rules

**The lab renders nothing and simulates nothing.** Every board, every
animation and every hour of every night comes from the engine and the
ordinary UI. Previous versions drew their own board and their own hour
strip; the strip actively lied, showing the two seats acting in sequence
when a night resolves them together. If you are tempted to draw a game
in here, open the game instead. `tests/test_lab.py` fails if the page
grows a renderer again.

**The lab never touches a real game.** It has its own store (`data/`),
it plays only in throwaway clones, and the invoke route refuses any
session that is not one. Three things enforce it, and each exists
because something went wrong:

- `readonly.py` wraps a store and blocks every write-shaped call.
- `isolation.py` refuses to clear the harness caches inside the web
  server, where doing so would wipe a live agent's hazard memory with
  nothing in the logs to say why.
- `turn._rekey` corrects a bug in the shared snapshot helper:
  `clone_for_run` re-keys the session *row* but not the `session_id`
  inside the state blob, so the engine hydrates the clone under the
  original season's id and every subsequent save lands on **the source
  season**. Silent, and it corrupts real games. `scripts/replay_turn.py`
  still inherits it — worth fixing at source.

## Layout

| Path | What it is |
| --- | --- |
| `boards.py` | What a board is, and discovering them from the store |
| `turn.py` | Cloning a board and playing it — the only module that drives a night |
| `routes.py` | Every HTTP route, included by `server/app.py` in one line |
| `store.py` | The lab's own local store, and which session ids it owns |
| `cast.py` | Who can be cast, discovered so a fork appears by existing |
| `mint.py` | Playing a season to freeze a fresh set of boards |
| `rewalk.py` | Grabbing a day out of a season you already played |
| `recall.py` | Giving a frozen turn the journal the night actually had |
| `freeze.py` | Capturing that journal into the board, so it ships |
| `pack.py` | Trimming and gzipping the boards to fit in the repo |
| `isolation.py` | Clearing harness memory between agents, safely |
| `readonly.py` | A store handle that cannot write |
| `arms.py` | The racks a seat can be handed when a board is opened |
| `baseline.py` | Recording and loading stock V12's frozen answer per board |
| `static/lab.html` | The launcher. A picker and a button; nothing else |
| `static/diff.html` | The divergence view — a fork against what it forked |
| `static/diff.js` | Its diff and rendering. Loaded by the page, never fetches |
| `baseline/` | Those frozen V12 takes. **Checked in** — it is the reference |
| `data/` | The boards, and every clone ever made. Deletable |
| `probe.py` | Drives the whole loop in a real browser |
| `tests/` | Runs under a bare `pytest` |

## Outside this folder

Three things could not live here, and they are the places to look when
something breaks:

- **`server/app.py`** includes `routes.router`, and `_store_for` routes
  `LAB_*` / `LABRUN_*` sessions to this folder's store. That hook is
  what lets a clone open in the ordinary game UI.
- **`server/static/app.js`** carries the lab-only parts of the game UI:
  the `?lab=` flag, the invoke rows in the AGENT tab, and the plan
  overlay. They are inert without the flag.
- **`server/static/styles.css`** styles those, under `cc-lab-*` and
  `cell--lab-plan`.

The invoke control is a **restoration**, not a new feature. Live-play
invocation was removed from the UI in v1.1 (see the comment where it
used to be, in `index.html`) in favour of the CLI. That is right for a
real game — nobody should be able to hand somebody else's seat to a
model mid-season — so it comes back only for throwaway clones, and the
server checks that for itself rather than trusting the page.

## Minting boards

```bash
python -m turnlab.mint          # plays a season, freezes a board per seat per night
```

Boards land in `data/` as `LAB_<season>_d<day>_<seat>`. The editorial
note about *why* a night is worth studying is the one hand-written part;
it lives in `boards.NOTES`, because it is the only thing a machine
cannot read off the board.

## The divergence view

`[ vs V12 ]` on any take opens a tab showing that agent against stock
V12 on the same board: issued orders hour by hour, the directive behind
them, the reasoning, and the prompts as sent — each one diffed.

V12's side is **frozen on disk**, not asked live. It is an LLM, so asking
again would answer differently and you would be diffing against a moving
target instead of against what you forked. One take per board per seat:

```bash
python -m turnlab.baseline          # anything missing
python -m turnlab.baseline --force  # all of it, again
```

That needs a PAT and takes about fifteen seconds a board.
`tests/test_lab.py` fails if a board has no baseline, because minting a
new set and forgetting to re-record leaves the view quietly broken.

Recorded **unarmed**, deliberately. Stock V12 cannot emit a weapon order
— `emp_launch` and `chaff_flare` are absent from its reply schema and
its option menu, appearing only in `last_night.py`, which narrates what
already happened. A rack changes what it sees and never what it does, so
one baseline per board is the honest number. That gap is the exercise: a
fork that learns to use the rack has something to show, and this is what
it gets shown against.

### What a rack does not test

**Use, not procurement.** A rack is stamped straight onto the clone's
`weapon_stock`, and `settle` submits empty orbit actions afterwards, so
the lab asks "given an EMP, does this agent fire it?" and never "would
it have bought one?". Deciding to spend blue on ordnance at dawn is a
different question and this is not the instrument for it.

That is also why a rack grants **no build fuel** (v1.43). It used to
hand over 200–455 blue purity to make more weapons with, which a night
can never spend — so it only sat in the hoard taking vault slots and
moving the score, and armed-versus-unarmed was quietly also
richer-versus-poorer. Arming now changes exactly one thing.

## Checking it still works

```bash
pytest turnlab/tests            # also runs under a bare pytest
python turnlab/probe.py         # drives the real browser, needs a server up
```

The probe exists because a page that renders nothing reports no error.
It opens a board, invokes both seats, and checks the plan overlay
painted and the night resolved — including that the turn did **not** run
away past its own night, which is the failure that started all this.
