# The situation library — design

**Status:** proposed, not built. Written 2026-09-01.
**Scope:** how an attendee learns *what to teach their agent*, as opposed
to learning whether this version scored higher than the last one.

Companion to `docs/AGENT_LOOP_PLAN.md` (the tooling and the round trip)
and `sea_of_colours/evals/battles/README.md` (the nine boards as built).
This document proposes the next layer on top of both.

> **The substrate this was designed on is deprecated (v1.42).** The
> battles suite — `soc suite`, `soc why`, `soc diff`, and the
> constructed boards underneath them — has been superseded by the **turn
> lab** (`turnlab/`), which is how a fork is tested now. Start at
> `turnlab/README.md`; `python run_web.py` then `/lab` (or the Turn Lab
> button on the landing page), or `python scripts/soc.py lab` to see the
> frozen turns and the castable forks without starting a server.
>
> The change goes to the root of §1's complaint, and mostly in the
> direction this document argued for. Battles built its boards: a
> `WorldBuilder` arranged a seam, a rival and a weapon rack into a
> situation, ran it over five difficulty rungs and four weapon loadouts,
> and returned a pass rate from predicates. The lab uses **frozen
> turns** — a real turn out of a real season, snapshotted the instant
> before a seat planned. You cast a fork into a seat, watch the night
> resolve in the ordinary game UI, and open the divergence view to diff
> your take against stock V12's frozen baseline on the same turn.
>
> Where it goes further than §1 asked: **the lab does not score at all.**
> This document's remedy for a bad scalar was a better readout —
> competences, foils, grouped results. The lab's remedy was to delete
> the scalar and show the difference instead, on the grounds that "how
> is my fork different from V12 here" is a question an attendee can act
> on and "74%" is not. Much of §3–§7 below is therefore still live as
> design thinking — the `teaches`/`foil` idea in particular is exactly
> what `turnlab/boards.py`'s `NOTES` carries, split into a checkable
> `state` and an editorial `why` — but the *predicates* it is built
> around are not coming back in the lab.
>
> Nothing is deleted. The battles commands still run and print a notice
> on stderr. `soc weapons` (a static scan of a fork's source, saying
> which firing rung it is stuck on) and `soc league` (run every
> submitted fork and rank them) have no lab equivalent yet, which is the
> only reason the suite is still in the tree — treat those two as
> current despite the notice they print.
>
> The rest of this document is left as written, including the parts the
> lab has overtaken. Where a section describes the suite as the thing
> being improved, read it as the reasoning that led to the lab.

---

## 1. The problem with the suite as built

*(`soc suite` is deprecated as of v1.42 — see the note at the top. The
diagnosis below is why, so it is kept in full.)*

`soc suite` answers one question well: *is this fork better or worse than
the last one?* It runs nine constructed redsign boards across a difficulty
ladder and returns a pass rate.

That is the wrong headline for a teaching tool. A pass rate is a
single scalar over a set of quite different competences, and a scalar
cannot tell you **what to change**. Three failure modes it hides:

- An agent that farms quiet boards perfectly and never fights scores
  respectably, because most boards are winnable without weapons.
- An agent that improves at tempo while *regressing* at denial shows a
  flat score, and the regression is invisible.
- An agent with a hardcoded constant that happens to suit six of nine
  boards looks like an agent that reads the board.

The attendee reads "74%", has no idea which lever to pull, and pulls the
one they were going to pull anyway.

**How v1.42 resolved this.** Not with the readout in §7, but by removing
the pass rate and putting a diff in its place: the lab shows a fork's
orders, directive, reasoning and prompt against stock V12's on the same
frozen turn, and says nothing about whether either is good. That answers
all three failure modes above by construction — there is no scalar to
average a quiet board into, no rate to stay flat while a competence
regresses, and a hardcoded constant shows up as a fork playing the same
way on six very different real nights. What it gives up is a verdict,
which is a real loss and a deliberate one: ranking is the league's job,
and the league should rank on wins over whole seasons (`soc season`)
rather than on predicates over one constructed night.

## 2. Three blockers found while checking this

Before any of the design below is worth building, three facts about the
current code have to change. All three were verified on 2026-09-01.

**2.1 — The fork does not own its buying policy.** ✅ **FIXED (v1.40).**
V12's `orbit.py` delegated to `plan_orbit_actions` in
`sea_of_colours/agent/heuristic_agent.py`, shared by every seat in the
room, so an attendee wanting their agent to buy weapons earlier had to
edit a shared file and `soc push` would correctly refuse the diff — the
single most requested lever was the one lever a fork could not pull.

The policy now lives in `tabula_v12/orbit_policy.py`, fork-local and
self-contained, with the thresholds hoisted into an `OrbitDials`
dataclass at the top of the file. `scripts/new_agent.py` rewrites the
import so a minted fork points at its own copy. It is behaviour-identical
to the shared planner at shipped dials — `tests/test_orbit_policy.py`
pins that with a differential test over 400 orbit states in both weapon
modes, plus a branch-coverage check so the sweep cannot silently stop
exercising a branch.

**2.2 — No intelligence runs in the orbit phase.** Still true. The orbit
path is the (now fork-local) heuristic plus one narrow gate
(`apply_economy_gate`, which rebuilds a harvester when the fleet is
short). The LLM never sees orbit, and the policy reads credits, BLUE and
fleet state but never the board. So *"buy an EMP when a redsign is live"*
has nowhere to live yet — the component that reads the board and the
component that spends the credits are still different code paths that do
not talk. 2.1 was the precondition; making the policy board-aware is now
a change a team can actually make and push.

**2.3 — The suite only runs nights.** A buying decision happens in a
phase the suite never executes, so weapon *procurement* is unmeasurable
and only weapon *firing* can be scored — from stock the harness handed
out. **Fix:** short multi-night arcs, §5.

Still true in the lab (v1.42), and for the same reason: a frozen turn is
snapshotted the instant before a seat planned its night, and `arms.py`
stamps a rack onto the clone directly rather than playing an orbit
phase. So the lab asks "given a weapon, does this fork fire it?" and
still cannot ask "would it have bought one?". The racks it offers are
none, one of each weapon on its own, and one of everything — one of each
at most, deliberately, because handing out pairs turns the run into a
question about salvo economics when the question worth asking is
simpler. The list is derived from `arms.py`'s `KINDS`, so it grew a SNAP
rack in v1.36 without anyone editing it. Arcs remain the open idea for procurement; a headless season
(`soc season`) is the blunt way to see it today.

## 3. The design: situations, not boards

A **situation** is a board plus the competence it isolates plus the
mistake it is built to catch. The nine existing boards become situations;
the library grows past them.

*(v1.42 — the half of this that survived. The lab's board library is ten
named frozen nights: Vanilla Opener · day 1, Early Redsign Battle · day
2 · dual discovery, Beaten to the Seam · day 3, Second Wind · day 4,
After the Gold Rush · day 4, and Vetus Lantern · day 6 · late redsign
race — plus, from v1.40, the four nights that can hold a SNAP: Sighted
and Armed · day 6 · the blind chase, Both Eyes on the Same Pure · day 3
· weapon poker, Two Ghosts, One Seam · day 2 · nobody can see it now,
and The Late Reversal · day 6 · sight against the scoreboard. The four
were cut from V12-vs-V12 seasons played at the 1:2:3 price ladder, which
is what makes them the only boards whose economy prices the weapon; the
older six predate it and correctly refuse a SNAP rack. Each carries a
`tests` line saying what you would learn here that
you would not learn anywhere else, a `state` claim that must be
checkable against the saved session, and an editorial `why` — which is
`teaches` and `foil` under other names, split so that a wrong number is
a bug you can find rather than an opinion. What did not survive is
`expect`: there are no predicates. Boards are discovered from the store
rather than declared, so the library grows by minting or grabbing rather
than by editing a list.)*

Each situation declares four things:

| Field | Purpose |
|---|---|
| `competence` | Which capability this is evidence about (see §4) |
| `teaches` | One sentence an attendee reads before the result |
| `foil` | What a naive agent does here, and why it looks reasonable |
| `expect` | The predicates, as today |

The `foil` is the new and important one. It is the difference between
"you failed `pure_first`" and "you walked to the pure instead of landing
on it — which is correct on an empty board and fatal here, because a
rival has H1 vision on the cell." A failure that does not explain the
temptation does not teach.

## 4. Competences, and situations that isolate them

Situations are grouped so that within a group, **one thing dominates**.
An agent can be strong at retrieval and hopeless at combat, and the
readout should show exactly that split.

### Retrieval — can it convert red into score

| Situation | Isolates | Foil it catches |
|---|---|---|
| `lone_seam` | Floor test: bank anything at all | Overthinking an unopposed 5-step walk |
| `hold_cap_greed` | Stop at the 6-parcel hold | Riding a long seam past the cap for nothing |
| `green_minefield` | Route around synthetic green | Straight-lining through −100 cells |
| `two_pures_poker` | Tempo: land on, don't walk to | Walking to a pure it could have landed on |
| `thin_board_blue` | Send the 2nd unit for blue | Chasing trace red because red is the habit |
| `rich_board_no_blue` | *Paired with above:* don't | Going for blue on reflex, ignoring the juice |

### Combat — can it use and survive weapons

| Situation | Isolates | Foil it catches |
|---|---|---|
| `emp_the_probe` | Deny the eye that found your pure | Hoarding EMP for a better moment that never comes |
| `chaff_the_lift` | Strand a loaded rival | Treating chaff as throwaway interdiction |
| `contested_pure_both_see` | Secure fast under observation | Riding the seam with a jackpot aboard |
| `under_emp` | Read a cloud and re-time | Dropping into an active cloud |
| `smash_and_grab` | Land on, lift at h2 | Maximising the chain and losing the pure |
| `blind_and_grab` | Kill the finder, comb the sign | Ignoring a redsign it cannot see into |

### Economy — does it fund the plan (arcs, §5)

| Situation | Isolates | Foil it catches |
|---|---|---|
| `arm_up` | Buy a weapon *and* fire it | Buying weapons it never uses |
| `fleet_recovery` | Rebuild over speculative probes | Nibbling credits on probes after a loss |
| `blue_pressure` | Fund weapons before they're needed | Discovering it cannot afford EMP on the night it needs one |

## 5. Paired situations and arcs

Two structural additions, both needed because the interesting policies
are conditional or cross-phase.

**Paired situations** test conditionality. *"Seek blue with the second
harvester unless there's juice on the board"* cannot be evaluated on one
board — an agent that always seeks blue passes it. The pair
`thin_board_blue` / `rich_board_no_blue` is scored on whether the
behaviour **differs**, so a constant fails both halves regardless of which
one it happens to suit. This is the only way to distinguish a policy from
a coincidence, and no scalar score can express it.

**Arcs** test cross-phase policy. *"Buy an EMP, then use it to deny the
pure"* spans orbit → night → resolution. An arc is a 3-night run where
the agent plays every phase, and the predicate reads the whole loop: did
it buy, did it fire, did the firing change the outcome. Arcs are slower
(three nights, both phases) so they run as a separate, smaller set.

## 6. Cards, saved by default

*(v1.42 — the lab takes a different route to the same end. It does not
write cards; it keeps every clone it ever made under `turnlab/data/`,
and a take is re-opened in the game UI rather than read as text. The
divergence view is where the card's contents surface — the orders hour
by hour, the directive behind them, the reasoning, and the prompt as
sent, each diffed against V12's frozen take on the same turn. The
paragraph below still describes what `soc suite` does.)*

`soc suite --cards DIR` exists. It should be **on by default**, writing
to `reports/cards/<agent>/<situation>/`, because the card is the artefact
that turns a red check into an understood mistake.

What matters is that the card is keyed by situation, not by run index. An
attendee looking at a red `chaff_the_lift` wants that decision's card in
one hop — the option menu it was offered, which options it selected, the
directive it produced, and the moves that came out. All of that is
already in the harness envelope's `extras`; it needs organising, not
generating.

Diffing two runs of the same situation across an edit is the highest-value
follow-on, and is the one item §5 of `AGENT_LOOP_PLAN.md` still lists as
unbuilt. **Built in v1.42, against V12 rather than against your own last
run** — which is the more useful axis for a fork, because it tells you
what you changed *about the thing you forked* rather than what you
changed since lunch.

## 7. The readout

*(Not built, and v1.42 went the other way: the lab's "readout" is the
night itself, played out in the game UI, plus the divergence view. The
sketch below is kept because the instinct in its last line — always name
the next command — is right regardless of what is being reported.)*

Grouped by competence, worst first, with the foil quoted:

```
REDWATCH_REAPER — 24 situations, 3 runs each, 41s

RETRIEVAL   ▓▓▓▓▓▓▓░   6/6 clean
COMBAT      ▓▓░░░░░░   2/6 clean
  chaff_the_lift        0/3   never fired chaff; rival lifted a pure at h3
    "chaff is not throwaway interdiction — cancelling a loaded
     rival's lift strands the cargo. This is the cheapest pure
     you will ever deny."
  under_emp             1/3   dropped into a live cloud on 2 of 3 runs
ECONOMY     ▓▓▓░░░░░   1/3 clean
  arm_up                0/3   bought 2 EMP across 3 nights, fired 0

PAIRS
  blue conditionality   FAIL  identical behaviour on thin and rich boards
                              (2nd harvester sought blue in both)

Cards: reports/cards/redwatch_reaper/
Start with: soc why chaff_the_lift redwatch_reaper
```

The last line matters as much as the rest. The report should always end
by naming the single next command.

## 8. A map of the levers

The fork is ~6,000 lines: `doctrine.py` (783), `option_economics.py`
(1,380), `seam_control.py` (2,900) and the rest. Neither an attendee nor
their coding assistant will find the right lever by reading it. Every
minted fork gets a `PLAYBOOK.md` indexing levers by intent:

| You want it to… | Edit |
|---|---|
| Buy weapons earlier, or at all | `orbit.py` — the economy gate |
| Contest pures it does not own | `doctrine.py` → `DOCTRINE_REDSIGN_POKER` |
| Value mass vs trace differently | `value_pyramid.py` |
| Express a plan shape it currently cannot | `option_economics.py` — the option menu |
| Send the 2nd harvester for blue | `orbit.py` + `doctrine.py` → `DOCTRINE_BLUE` |
| React to chaff / EMP threat | `doctrine.py` → `DOCTRINE_BEWARE_*` |

Written for the assistant as much as the human — a table an agent can
grep is worth more than a chapter it has to summarise.

## 9. Build order

*(Steps 2–6 were overtaken by the turn lab in v1.42 and are unlikely to
be built as written: they all assume boards with predicates on them.
Step 7, `PLAYBOOK.md`, is untouched by the change and is still the
highest-value item left here — the lab shows a fork what it did
differently, and a lever index is what turns that into an edit.)*

1. ~~**Fork owns `plan_orbit_actions`** (§2.1).~~ ✅ done, v1.40.
2. **`competence` / `teaches` / `foil` on the nine existing boards.** No
   new boards yet — prove the readout on what exists.
3. **Report grouped by competence** (§7), with the next command named.
4. **Cards on by default, keyed by situation** (§6).
5. **The retrieval and combat situations** that do not yet exist (§4).
6. **Paired situations** and pair scoring (§5).
7. **`PLAYBOOK.md`** in every minted fork (§8).
8. **Arcs** (§5) — last, because they are the slowest to run and the
   most work to stage.

Steps 1–4 are the ones that change what an attendee sees. Steps 5–8
deepen it.

## 10. Deliberately not proposed

- **A legality resolver that filters plays.** Same reasoning as the
  ORDERS panel: annotate, do not remove. An agent that proposes an
  illegal play should be told, not silently corrected.
- **Weighting the competences into one number.** The temptation is a
  weighted score for the league table. The league can keep its pass
  rate; the *teaching* readout must not collapse.
- **More boards before the readout is right.** Nine boards with a good
  readout teach more than thirty with a bad one.

## 11. Open questions

- **How many runs per situation?** Three shows flakiness; with an LLM
  that is 72 model calls for the full library. `--runs 1` for the inner
  loop and 3 for the league is probably right, but wants measuring.
- **Do pairs need their own rung ladder?** Probably not — conditionality
  is interesting at one difficulty. Worth confirming.
- **Should arcs use a real opponent agent or a scripted one?** A scripted
  opponent is reproducible; a real one is honest. Leaning scripted for
  the library and real for the league.
