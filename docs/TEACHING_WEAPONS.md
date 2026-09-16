# Worked example — teaching a fork to fire

Stock V12 buys weapons and never fires them. That is the largest single
scoring gap in the kit, and it is the one most teams pick. This document
is the whole job done once, on a real fork, with every file named and
every wrong turn kept in.

It is a companion to
[the four rungs](HACKATHON_AGENTS.md#the-four-rungs-of-firing-a-weapon),
not a replacement: the guide says what the four rungs *are*, this says
what each one cost to build.

> **Where the firing test lives now (v1.42).** `soc weapons` is
> **current** and unchanged — it is a static scan of your fork's source,
> it needs no model and no credentials, and it is still the first thing
> to run. It prints the same deprecation notice as the rest of the
> battles commands, which is a wrinkle rather than an instruction:
> nothing in the lab replaces a source scan, so ignore the notice on
> this one. What has changed is the other half. The battles suite
> (`soc suite`, `soc why`, `soc diff`) is **deprecated**, superseded by
> the **turn lab** (`turnlab/` — see its README). Instead of a
> constructed board at a difficulty rung with a loadout attached, you
> open a real turn out of a real season, hand a seat a rack, cast your
> fork into it and watch the night resolve in the ordinary game UI:
> `python run_web.py` then `/lab`, or `python scripts/soc.py lab` to see
> what is in there without a server.
>
> That matters here more than anywhere else in the docs, because this is
> the document about whether an agent *fires*. `soc weapons` proves the
> salvo can reach the engine; the lab is where you find out whether the
> model reaches for it on a night somebody really played. The battles
> commands still run and print a notice — nothing has been removed, and
> §6's findings below were measured on them.

The fork is `emp_harvest_test`, and it is **in the repo** — a real
minted agent with its own `agent.json`, registered the same way yours
will be, and every snippet below is quoted from it. Read it alongside
this.

That it exists as a fork rather than as edits to V12 is the whole shape
of the day: V12 is the control group, you mint your own directory beside
it, and the league at the end is a scan for `agent.json`. Nothing in
this document changes a shared file.

> **The headline finding, up front.** Building all four rungs took about
> a dozen files and a day's work, and at the end of it the agent still
> mostly does not fire. The menu offers the salvo, the doctrine argues
> for it, and the model picks a harvest chain instead. That is not a
> failure of the build — it is the point at which the problem stops
> being *plumbing* and becomes *persuasion*, and knowing which of those
> two you are working on is most of the skill.

---

## 0. Start with the diagnostic, not the code

```bash
python scripts/soc.py weapons --agent emp_harvest_test   # or your own fork
```

It reads your fork's source — no model, no credentials, instant — and
tells you which rung you are stuck on. Run it after every change. The
whole exercise below is just walking it from four FAILs to four PASSes.

This is one of the two jobs the turn lab does not do, which is why it is
still the right first command in v1.42. A source scan and a played night
answer different questions: the scan says the play is *possible*, the
lab says whether it *happened*, and a fork that fails the scan cannot be
diagnosed by watching it — the option was never on the menu to refuse.

The rungs must be built **in order**, because each one is invisible
until the one before it works. An option that emits a verb the schema
forbids does not error; it silently never appears.

---

## 1. Rung 1 — the agent must know it owns a rack

**Symptom:** the night phase never mentions weapons, because nothing
tells it there are any. Only the *buying* code reads `weapon_stock`.

**Files:** `scorch.py` (new), `prompt.py`.

The stock is already on the view at `orbit.weapon_stock`; it just was
not being read at night. One reader:

```python
def stock(agent_view):
    ws = (agent_view.get("orbit") or {}).get("weapon_stock") or {}
    return {"emp": int(ws.get("emp", 0) or 0),
            "chaff": int(ws.get("chaff", 0) or 0)}
```

and a block in the prompt saying what a charge actually does. **Read the
specs off the view, not out of constants** —
`orbit.weapon_specs.emp` carries radius, missiles and cloud hours, so a
retune of `game/weapons.py` reaches your agent's arithmetic *and* its
prose with no edit here. `scorch.specs()` does this, falling back to the
engine constants only for stripped test views.

---

## 2. Rung 2 — the play must be offerable AND compilable

This is where the time goes, because **three separate places have to
agree** and any one of them silently kills the play.

### 2a. The schema has to allow the verb

**File:** `chat_schema.py`. This was the hard blocker, and it is
invisible from the outside:

```python
"a": {"enum": [..., "emp_launch"]},
"at": {"type": "array"},   # widened: a salvo is a LIST of cells
```

Structured output means the model *physically cannot* emit
`emp_launch` while the enum omits it. No error, no warning — the verb
just never appears. Note the second line too: most verbs take one cell,
a salvo takes three, so the field had to widen as well.

### 2b. An option has to exist that emits it

**File:** `agency.py`. Four options were added, and the *interesting*
one is not the obvious one:

| id | what it does |
|---|---|
| `EMP_SCORCH` | hit rival probes, freshest first |
| `SCORCH_REDSIGN` | blanket a rival's smear |
| `BLIND_SCORCH` | **shaped** salvo leaving a hole, probe on the rim |
| `BLIND_SCORCH_T` | same, but the probe goes in the hole |

`SCORCH_REDSIGN` is the naive play and it is usually bad: blanketing a
seam denies it to *you* as much as to them, so you spend a charge and an
hour and end the night with nothing on the ground.

The shaped salvo is the one worth understanding. A cell is only darkened
when a missile lands within the blast radius of it — and *you* choose
where the three missiles land. Put every centre at `radius + 1` or more
from one chosen cell and that cell stays clear while everything round it
goes dark. Now the cloud is not a wall between you and the seam; it is a
wall **around a cell only you can use**. Probe it, drop into it, and for
eight hours no rival can contest the ground you are standing on.

```python
shape = scorch.shaped_salvo(region, radius=2, missiles=3)
# {'targets': [...3 centres...], 'hole': (10,10),
#  'covered': [...25 cells...], 'open_cells': [...honest leakage...]}
```

It returns `None` when the geometry does not fit. **That is a real
answer, not a failure** — on a tight smear the honest play is the plain
salvo, and an option that lies about its own geometry is worse than no
option.

### 2c. The packager has to survive it

**File:** `packager.py`. `_pack_emp` compiles the option to wire moves.
Three things it has to get right, all learned the hard way:

- **A salvo is ONE move and ONE hour**, not three. It consumes one of
  the seat's 21 slots (§3.10) and the seat does nothing else that hour.
- **Friendly fire is on.** Your own probes in your own cloud are
  *destroyed*; your own harvesters are *disabled*. The packager tracks a
  `scorched` map of cell → clear-hour and **warns without cutting** —
  the house rule is to price a bad play, not to forbid it.
- **Order matters.** `_order_for_emp_cloud` hoists salvos to hour 1,
  then puts plays clear of the blast ahead of plays inside it.

---

## 3. Rung 3 — the offer has to explain itself

A menu line of bare geometry is not a decision. Compare:

> `EMP_SCORCH: launch at (12,9) (8,11) (12,12)`

with what the fork actually emits:

> `BLIND_SCORCH: scorch (16,2) (19,5) (20,1) leaving (18,3) open, then
> take it — 22 cells of THEIR smear dark for 8h; you hold (18,3) inside
> it`

The second one can be reasoned about. `Option` has `detail` and
`rationale` fields sitting empty in stock V12 for exactly this.

Say the **cost** too, not just the benefit. Ours quotes the hour: *"the
BLUE was paid in orbit and is sunk, but the hour is being spent right
now against a harvester chain that would have banked RED with it."*

---

## 4. Rung 4 — doctrine: when, and at what tempo

**File:** `doctrine.py`. Prose telling the model when firing beats
harvesting. The shipped doctrine teaches *surviving* EMP and chaff and
never using them, so an option nobody is told to reach for stays
unreached.

Three rules that turned out to matter more than the geometry:

**Fire it first or do not fire it.** A salvo is worth its slot at hour 1
or 2 and close to nothing after. Probes die on *contact*, so an early
kill costs them a night of vision while a late one destroys a picture
they have already used; landings happen early; and a cloud lit at hour
15 of a 21-hour Nox loses most of its eight hours to Aurora. The
conclusion is the useful part: *if the only slot you can spare is late,
you do not have room for a salvo tonight — keep the charge.*

**A certain grab beats scorching your own sign.** If the redsign is
yours and a pure is reachable, take it. Denying ground you were about to
work is self-harm. This is expressed as doctrine, not as a filter — the
option stays on the menu, the agent is told why it is usually wrong.

**The cloud's eight hours are not your eight hours.** The mistake that
makes the whole play look bad: you get one action per hour across the
*whole fleet*, so a harvester holding a hole is one unit standing still
while every other unit works. A night built around a scorch must also
pick a full slate of chains elsewhere.

That last one needed code as well as prose, and it is the nicest part of
the build. `flush_deferred` **splices** the held walk in at the hour the
cloud lifts and pushes whatever was queued for those hours out behind
it:

```
h1  emp_launch          ← salvo
h2  probe (10,10)       ← the hole
h3  drop  harvester_1   ← into the hole
h4  drop  harvester_2   ┐
h5  step  harvester_2   │ the OTHER unit works
h6  step  harvester_2   │ while the cloud stands
h7  step  harvester_2   │
h8  step  harvester_2   ┘
h9  step  harvester_1   ← cloud lifts; the comb goes in
...
h16 step  harvester_2   ┐ and the other unit
h17 pickup harvester_2  ┘ finishes behind it
```

Zero `wait` moves. Padding with `wait` is the fallback, and when it
fires the log says the plan was too thin rather than silently stalling.
Walking in early is *not* an alternative: a step into a standing cloud
lands but banks nothing (§4.9.3) and the unit is disabled from the next
hour — you lose the hour *and* the cell.

---

## 5. Do not forget the orbit phase

You cannot fire what you never bought. The file is `orbit_policy.py`
**inside your own fork** — before v1.40 the buying policy was shared
code outside every harness, so this whole section would have been a
change to the kit that `soc push` refused. Your copy starts
behaviour-identical to V12's, so anything your agent buys differently is
something you decided.

Two bugs here, and the second is the kind that hides for a long time:

**Buy it early.** V12 gates an EMP behind 300 BLUE, or 250 and a coin
flip, with chaff ahead of it — so the first EMP lands around day four if
at all. The fork buys one the first day it can afford it.

**Ring-fence the credits.** An EMP costs **200 BLUE and 250 credits**.
Ordering weapons ahead of probes is not enough, because the 1500c
harvester build takes its bite first and can leave the rack empty with
the blue sitting right there. The fork withholds the EMP's credits
before any discretionary purchase and releases them at the buy. Repair
is deliberately outside the fence — a damaged harvester is a unit
already paid for and about to be lost.

**And check your fallback prices.** This fork shipped
`emp_credit_cost = 0` while the engine charged 250. Prices arrive on the
view and normally win, so the wrong constant only bit when a view came
through without them — which is exactly why it survived. The fix is to
stop copying:

```python
from sea_of_colours.game.weapons import EMP_COST_BLUE_PURITY, EMP_COST_CREDITS
```

A hardcoded literal in a fork is worse than one in the engine: forks are
copied wholesale, so it replicates into every agent in the room and
cannot be fixed centrally.

---

## 6. What actually happened when it ran

Four PASSes on `soc weapons`, ~1,250 tests green, and then:

**In three headless seasons against stock V12** the fork won all three
(4798–1851, 5269–1044, 3235–2536). It bought one to two EMPs a season
and fired between zero and three. **Do not read that as vindication** —
a handful of salvos across three seasons is far too thin to separate
from board variance, and the widest margin was the season where nothing
fired.

**In the combat suite it fired nothing at all** across three armed
battles with a full rack. *(That was the battles suite, deprecated since
v1.42. The same finding is now reached by opening a frozen turn in the
lab, arming the seat and casting the fork — and it is a better test of
this particular claim, because a constructed board can always be
accused of manufacturing the opportunity. Vetus Lantern · day 6 is the
board built for it: a late redsign race where the leader is discovered
and cannot cover itself.)* Following the debugging order from the guide —
*was the option offered, could the schema express it, did the packager
survive it, only then blame the model* — the recorded bake said:

```
BLIND_SCORCH in prompt: 5          ← offered, with a full rationale
options_chosen: ['UNBEATEN_FLANK', 'PR1', 'PR2', 'PR3']
```

The menu had it. The doctrine argued for it. The model took a harvest
chain and three probes instead, three times out of three. So all four
rungs are genuinely built, and the remaining problem is **persuasion**:
option ranking, how the menu is ordered, how the cost is framed. That is
a different job from the one this document describes, and you cannot
even start it until the four rungs are done.

**The real gate on firing was blue, not doctrine.** Across the three
seasons the orbit phase was blocked at `blue 50/200` six times and
`blue 197/200` once, while sitting on 1000+ credits every time. An agent
that wants to fight has to *bank blue early*, which is an economic
decision made nights before the salvo. Nothing in the weapons code can
fix that.

---

## 7. The checklist

```
[ ] soc weapons --agent <you>          — rung 1 PASS?
[ ] night phase reads orbit.weapon_stock
[ ] specs read off the view, not hardcoded
[ ] chat_schema enum includes the verb   ← the silent one
[ ] `at` widened if the verb takes a list
[ ] agency.py registers an option that emits it
[ ] packager compiles it; 1 salvo = 1 move = 1 hour
[ ] friendly fire warned, not cut
[ ] salvo sequenced to hour 1
[ ] option text says what it BUYS and what it COSTS
[ ] doctrine says when to reach for it, and when not to
[ ] orbit actually buys the thing, early, with credits reserved
[ ] prices imported from game/weapons.py, never copied
[ ] open a frozen turn in the lab, once unarmed and once with an EMP
    — does the fork actually fire it?
[ ] soc suite --loadout empty,emp  — LEGACY: does the ORDNANCE line change?
```

The last two lines are the only ones that matter. Everything above them
is plumbing; that is the question.

The lab is the way to ask it now. Opening a board lets you hand the seat
one of four racks — none, one chaff, one EMP, or one EMP and one chaff —
and the rack is a property of *opening* the board rather than of the
board, so two runs of the same night can differ by exactly that one
thing. One of each at most is deliberate: a second charge only lets a
fork look decisive by spending twice, and the question is whether it
fires at all.

Two things about the lab are worth knowing before you read a result as a
verdict on your fork. The lab **states the rival's arsenal** rather than
making it guessable — V12 infers a rack by watching a rival's blue band
drop between nights, and a frozen turn has no previous night to
difference against, so without this every agent read `emp: none
observed` and planned a night that was not the night. And the frozen V12
baseline you are diffed against was recorded **unarmed**, because stock
V12 cannot emit a weapon order at all: `emp_launch` and `chaff_flare` are
absent from its reply schema and its option menu. A rack changes what it
sees and never what it does. That gap is exactly the exercise this
document describes, and it is what a fork that has done the work gets
shown against.

`soc suite --loadout empty,emp` still runs and still answers, so the
legacy line is kept — but it asks the question on a board built to
provoke the answer, which is the weaker version of the test.
