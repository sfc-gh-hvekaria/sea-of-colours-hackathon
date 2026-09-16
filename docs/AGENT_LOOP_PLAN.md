# The agentic improvement loop — plan

**Status:** partly built (2026-08-31). Written 2026-08-30.
**Scope:** what an attendee does between "I forked V12" and "my agent is
better than it was", and what we should build so that round trip is
minutes rather than an afternoon.

This is the design document for Phase 7.5 + Phase 8 in
`docs/HACKATHON_BUILD_PLAN.md`. It supersedes the sketch there; that
table should point here.

> **Partly superseded (v1.42) — read this before acting on anything below.**
> The verdict mechanism this document is built around — the battles
> suite in `sea_of_colours/evals/battles/`, reached through `soc suite`,
> `soc why` and `soc diff` — is **deprecated**. A fork is tested in the
> **turn lab** (`turnlab/`) now. Start at `turnlab/README.md`.
>
> The difference is what a board *is*. Battles constructed its boards:
> a `WorldBuilder` arranged a seam, a rival and a weapon rack into a
> situation, then ran it across five difficulty rungs and four weapon
> loadouts and returned a score from predicates. The lab uses **frozen
> turns** — a real turn out of a real season, snapshotted the instant
> before a seat planned. You cast any fork into any seat, watch the
> night resolve in the ordinary game UI, and open the divergence view to
> diff your take against stock V12's frozen baseline on the same board.
>
> ```bash
> python run_web.py                # then open /lab, or the Turn Lab button on the landing page
> python scripts/soc.py lab        # the frozen turns and the forks, without a server
> ```
>
> Two consequences that matter for the reasoning below. **The lab adds
> no scoring at all** — deliberately, because §1 of this document is
> about time-to-verdict and §3 already suspects the score is the wrong
> headline; the lab's question is "how is my fork different from V12",
> not "what mark did it get". And **the lab writes only to
> `turnlab/data/`**, never to `seasons/` and never to Snowflake, so a
> lab run cannot touch a live game.
>
> Nothing has been deleted. Every battles command still runs and prints
> a deprecation notice on stderr, because `soc suite` is quoted in the
> attendee docs and in every fork's minting output. Two jobs have no lab
> equivalent, and that is the only reason the battles code is still
> here: `soc weapons` (a static source scan saying which firing rung a
> fork is stuck on) and `soc league` (run every submitted fork and rank
> them). Both currently print the notice along with the rest, which
> overstates the case — there is nowhere else to send someone for either
> of them yet.
>
> Everything below is kept as written. Where a section presents battles
> as the current answer, read it as the reasoning that led here rather
> than as instructions.

---

## What is built, as of 2026-08-31

The attendee-facing guide is **`docs/HACKATHON_AGENTS.md`** — mint,
improve, score, publish, league. Read that first; this document is the
reasoning behind it.

| Piece | Where | State |
|---|---|---|
| One front door | `scripts/soc.py` | built — `new`, `lab`, `season`, `doctor`, `push`, plus the deprecated battles commands |
| Fork registration without conflicts | `orchestrator_2/agent_manifest.py` | built — directory discovery over `agent.json` |
| **Where a fork is tested** | **`turnlab/`** | **built (v1.42) — frozen turns, cast a fork into a seat, watch the night in the real UI, diff against V12. This is the current answer** |
| The scenario suite | `evals/battles/` | **deprecated (v1.42)** — built, still runs. 10 constructed boards x 5 rungs x 4 loadouts, offline. Nine redsign nights plus `plain_night_armed`, the no-jackpot control |
| The V12 baseline | `evals/battles/baseline/` | deprecated with the suite. The lab's equivalent is `turnlab/baseline/`, also frozen and checked in |
| Verdict after an edit | `soc suite` | deprecated (v1.42) — built, 45 turns in ~1s against the heuristic. Use the lab |
| "Why did it do that" | `soc why <board> [agent]` | deprecated (v1.42) — the lab's divergence view answers it against a turn that was actually played |
| Per-run cards | `soc suite --cards DIR` | built, deprecated with the suite |
| Submission | `soc push` | built — enforces agent-only diffs, and (v1.44) pushes to the attendee's own fork, refusing if `origin` is upstream |
| Collation | `soc collect` | built (v1.44) — gathers each fork's agent into a separate staging checkout; needed once the day moved to forks, since the league stopped being a scan of one tree |
| League | `soc league` | built — entrants are discovered, fallback runs flagged. **No lab equivalent yet**, so this and `soc weapons` are why battles survives |

**Time-to-verdict, measured:** ~1s for the whole suite against the
heuristic, so the target in §1 is met for the offline case. An LLM agent
is bounded by its own latency, which is why `--board` and `--up-to`
exist — the common inner-loop command is one board at one rung.

The lab reaches the same target differently, and the difference is worth
naming: it does not run a ladder at all. One frozen turn, one seat, one
model call, and the answer you read is a diff rather than a rate — so
the cost is bounded by the model rather than by how many boards you
chose, and there is no temptation to run forty-five turns to move a
number by two per cent.

### Three things the build changed about the plan

1. **Registration had to move before anything else.** The plan assumed
   forks register in `binding_registry.py`. With forty teams that makes
   every push conflict with every other push and turns collation into
   forty merges. Discovery over `agent.json` came first because `soc
   push` and the league both depend on an agent being exactly one
   directory.
2. **The boards are constructed, not restored.** The nine documented
   redsign battles live in Snowflake snapshots that are not in git, so
   they cannot be restored offline — and a frozen blob has exactly one
   difficulty anyway. They are rebuilt from their documented geometry
   via `WorldBuilder`, which is what makes the difficulty ladder
   possible at all. The trade: documented shape, not byte-exact state.

   **Overturned in v1.42**, and this is the finding that produced the
   lab. A finished season does not have to be *read back* — it can be
   **re-walked**. The seed, both seats' night orders and the orbit
   settlements are archived, and the engine has no nondeterminism, so
   replaying them reproduces the season exactly. So real state is
   reachable offline after all, either by freezing a season as it plays
   (`turnlab/mint.py`, reproducible — same arguments, same boards, and
   `test_mint_is_reproducible.py` keeps it that way) or by re-walking a
   finished one to the day you want (`python -m turnlab grab <season>
   <day>`). The ladder is what you give up: a frozen turn has one
   difficulty, which is the difficulty it really had. The lab hands out
   a rack when a board is opened instead, and asks whether the fork
   fires it.
3. **Scoring a fallback as a score is a real hazard.** A V12 fork with
   no credentials scored 86% on its internal safety net and outranked
   the heuristic. Runs now record whether the model was reached, and
   both the report and the league say so loudly.

### Still open

- **The score is the wrong headline.** `soc suite` answers "better or
  worse", which does not tell an attendee *what to teach the agent*.
  `docs/SITUATION_LIBRARY.md` is the design for the layer that does —
  competence-scoped situations, saved cards, and paired boards for
  conditional policy. It also records three blockers found on
  2026-09-01, the worst being that a fork cannot edit its own buying
  policy because `plan_orbit_actions` is shared.

  **v1.42 answered this by taking the headline away rather than fixing
  it.** The lab does not score, at all. A run leaves you with the night
  it played and a diff against V12 on the same turn, and the attendee
  reads the difference instead of a rate. That is a bet worth stating
  plainly: it trades the comfort of a single number for the thing the
  number never gave anyone, which is a specific behaviour to argue
  with. Ranking is a league job, and it belongs on wins over whole
  seasons rather than on predicates over one night.
- **No LLM verification.** Everything above was exercised against the
  offline heuristic. The V12 path needs one real run with a PAT before
  the day.
 - **Tournament night is not built.** `soc season` plays one season;
   the end-of-day event runs every submitted agent and ranks them. It
   is a different job from the attendee loop — unattended, **Snowflake
   only**, and its output is a ranking and a post-mortem rather than a
   replay. Deferred deliberately (2026-09-01), but the shape is decided:

   **Format: four-seat melees into 1v1, eliminating each round.** Groups
   of four play a season, the field is cut, and it narrows to a 1v1
   final. Chosen over round-robin for two reasons: four-seat is how the
   game is actually played, and round-robin does not survive the clock.
   Every LLM seat calls a model every night, so a season is minutes —
   ten teams round-robin over three seeds is ~135 seasons against ~20
   for melees.

   **The analysis is the deliverable, not the table.** Wanted:

   | | |
   |---|---|
   | Head-to-head | who beat whom, and by how much |
   | What separated the winners | where the leaders' scores diverged from the field, and the turn that did it |
   | Best moves | highest-value single turns of the night, with their cards |
   | Biggest gaffes | the worst turns, same treatment — the fun half, and the most instructive |
   | Economy vs combat | who won on buying, who won on contesting pures |
   | Integrity flags | fell back to the heuristic, crashed, or timed out |

   Most of this is already reachable: `seasons.py` keeps a card per
   turn with phase, orders and reasoning, `SeasonResult` carries scores
   and fallback counts, and per-turn scoring deltas give the divergence
   and the best/worst turns without new engine work.

   **v1.42 settles what the league ranks on.** `soc league` as shipped
   ranks by battles pass rate, which is now the last thing in the kit
   still doing that; the intent is that it ranks by **actual wins in
   headless seasons** (`soc season`) instead. That follows from the same
   argument as dropping the lab's score: a pass rate over constructed
   nights measures agreement with predicates somebody wrote, while a
   season measures the game. Until that lands, `soc league` stays as it
   is, because a league nobody can run is worse than one ranked on the
   wrong thing.

Closed since:

- `two_seams_choose_one` is green (2026-09-01) — the scenario now clears
  generator noise within the harvester's step budget, derived from
  `HARVESTER_HOLD_CAPACITY` rather than a hardcoded 5, so the board
  offers only the two seams it is named for.
- **The "where do I see what it did" gap is closed** (2026-09-01).
  `soc suite --record` freezes each turn and the battle room replays it
  beside its card — see the suite's README. This also subsumes the
  prompt-diff idea in §5, less precisely but more usefully: bakes
  accumulate, so the same board can be opened before and after a change
  and compared by eye. **Superseded by the lab (v1.42)**, which closes
  the same gap better by not building a viewer: a turn opens in the
  ordinary command centre, with the real fog toggles, the real hour
  transport and the real replay, so what you are watching is what the
  engine did rather than a second rendering of it.
 - **Headless seasons are built** (2026-09-01). `soc season` puts any
   agent in any seat — forks, stock V12, the heuristics — plays the
   season to the end, and writes it through the same store the live
   server reads, so it opens in the normal replay UI. A Markdown card
   per turn lands under `reports/seasons/`, orbit and night separately,
   and the AGENT tab grew a `[ .MD ]` button that pulls the same thing
   for any season including ones played by hand. Two things fell out of
   building it: agent dispatch is now one function
   (`evals/dispatch.py`) instead of a copy per runner, and the AGENT
   panel's grid had three rows for four children so the seat tabs were
   eating the feed's height.
 - **Boards no longer carry undeclared pures** (2026-09-01). Found within
  minutes of the room existing, which is the argument for it: staging
  painted declared terrain over the noise generator without clearing it,
  so a "one pure" board shipped four and an agent could be marked down
  for taking a real one. `stage.py` now blanks RED first, and
  `test_every_board_stages_at_every_rung` asserts a board contains
  exactly the pures it declares.

---

## 1. The thing we are actually designing for

An attendee's day, honestly described:

| Time | What they do | What they need |
|------|--------------|----------------|
| 0:00 | Install, run the server, play the tutorial | Already built |
| 0:30 | Watch V12 play a night | Already built (AGENT panel, replay) |
| 0:45 | `new_agent.py --team x --name y` | Already built |
| 0:50 | **"Why did it do that?"** | Cards exist — but you have to know to set an env var |
| 1:00 | Change a doctrine string / a gate / add a weapon option | The two gaps are documented |
| 1:05 | **"Is it better?"** | ← **this is where the day is won or lost** |
| … | Repeat 1:00–1:05 as many times as possible | |
| 6:00 | Submit, compete | Nothing exists |

Everything hinges on the cost of the 1:00 → 1:05 round trip. If it is
five minutes, an attendee gets maybe forty iterations in a day and
learns something. If it is fifteen, they get a dozen, most of which they
will not bother to run, and they will spend the day reading code and
guessing.

**So the design goal is one number: time-to-verdict after an edit.**
Target: under 30 seconds for the common case.

## 2. What already exists (and is good)

I inventoried the repo before proposing anything. There is more here
than a first read suggests:

- **Minting works.** `scripts/new_agent.py --team redwatch --name reaper`
  copies the harness, rewrites `tabula_v12` → `redwatch_reaper` through
  imports/identity/env-var namespaces, and writes an `agent.json`
  declaring it. (This inventory predates v1.39: it used to insert two
  entries into `binding_registry.py`, and moving off that shared file is
  the first of the three changes listed above.) It rolls back on failure. The fork appears in
  the New Game modal on the next server start with no frontend edit,
  because the modal reads `/api/meta/agents` off `selectable_agents()`.
- **The card is excellent.** `SOC_CARD_DUMP_DIR=/tmp/cards` writes one
  file per turn containing the full prompt, the option menu, the THINK
  prose, the PLAN JSON, the packager trace, the sanitiser trace and the
  final moves. This is a complete autopsy of a decision. Everything else
  in this document orbits it.
- **Evaluation exists at three scales**: ~56 scenarios in
  `sea_of_colours/evals/` (24 wired into the orchestrator_2 CLI, 7 pinned
  as a must-pass heuristic set in CI); `scripts/turn_suite.py` for pinned
  turns; `scripts/run_matchup_v12.py` for full head-to-head seasons.
- **The harness contract is clean.** One function,
  `run(*, store, session_id, player, view) -> dict`, and
  `submit=False` gives you the whole pipeline with no engine write —
  which is what the in-game advisor already uses.
- **The gaps are real and well chosen.** Gap 1 (buys weapons, never
  fires) needs edits in four independent places, which is a genuine
  systems exercise rather than a prompt tweak. Gap 2 (BLUE is an
  afterthought) has five levers where loosening one alone does nothing.

## 3. Where the loop actually breaks

Six problems, roughly in order of how much they cost:

1. **There is no answer to "is it better?" that runs in under a minute.**
   A matchup season is 5–15 minutes of LLM calls. The scenario suite is
   faster but still tens of seconds per scenario with a real model, and
   it reports pass/fail against fixed assertions rather than
   *better/worse than what I had*. **(v1.42: the lab answers a
   deliberately different question — "how is my fork different from
   V12 on a turn that was really played" — for one model call. Note
   that this is not the same question, and the diagnosis in this list
   assumed the answer had to be a comparison of scores.)**
2. **Six scripts, no front door.** `new_agent.py`, `run_evals.py`,
   `run_battery.py`, `run_matchup_v12.py`, `turn_suite.py`,
   `advise_v12.py`, `dump_suite_cards.py`, `replay_turn.py`. Each has
   its own flags. An attendee has to learn the toolchain before they can
   use it, and the toolchain is not the point of the day.
3. **Cards are opt-in and undiscoverable.** They are the best artifact we
   have and they are behind an environment variable that is documented
   in a harness README.
4. **Nothing compares two cards.** The single most useful question —
   "I changed my doctrine, what did that actually do to the decision?" —
   has no tool. You diff two 30KB text files by eye.
5. **The advisor is hardcoded to V12.** An attendee cannot ask *their own
   agent* for advice in-game, which is the most enjoyable way to
   understand what it thinks.
6. **There is no end to the day.** No submission format, no scoring, no
   leaderboard, no reason for the room to compete rather than forty
   people quietly tuning in parallel.

There is also one small correctness snag worth fixing early: the
orchestrator_2 eval CLI refuses to run Cortex configs unless
`SOC_BACKEND=snowflake` (`orchestrator_2/evals/cli.py:138-141`), while
`run_matchup_v12.py` runs the same agent fine on `memory`. That
inconsistency will cost somebody an hour.

## 4. The proposal: three loops at three speeds, behind one command

> **Read §4 as history (v1.42).** The command names sketched here were
> not all built as written, and the fast loop landed as the turn lab
> rather than as `soc turn`. The instinct in §4 was right — pin a turn,
> run one decision, compare it — and the lab is that instinct with real
> state under it instead of constructed boards. `soc season` is the slow
> confirmation loop this section calls `soc match`.

The insight is that "is it better?" has three different answers with
three different costs, and attendees should reach for the cheap one
almost always.

```
   soc turn      seconds     one pinned board, one decision, one card
   soc eval      minutes     the scenario suite, scored against your last run
   soc match     ~15 min     full seasons vs RED_HARVEST and vs stock V12
```

The fast loop is **pinned turns**, not seasons. A pinned turn is a frozen
board state (we already have these — `reports/turn_suite/suite.json`,
and `export_agent_guide_data.py` uses a frozen snapshot). Running your
agent on one is a single LLM call: 5–15 seconds. Running it on eight is
a minute, parallelised.

That is the round trip. Everything else is confirmation.

### 4.1 `soc` — one front door

A single entry point that wraps what exists. Not a rewrite: a façade over
the current scripts, so the scripts stay usable and nothing is
reimplemented.

```bash
soc new                      # interactive mint: team, name, colour, blurb
soc play                     # start the server and open a game vs your agent

soc turn                     # run your agent on the default pinned board
soc turn --board own_seam_d4 --repeat 3
soc diff                     # your last card vs your previous card
soc diff --against stock     # your last card vs stock V12 on the same board

soc eval                     # the scenario suite for your agent
soc eval --scenario tier_choice --card    # one scenario, dump the card

soc match                    # seasons vs RED_HARVEST, LITE and stock V12
soc submit                   # package + score + push to the leaderboard

soc card                     # pretty-print the most recent card
soc why                      # the last decision in one screen (see 4.3)
```

`soc` reads a `.soc-agent` file written by `soc new` so that every other
command already knows which fork you mean. No `--config my_team_my_name`
on every invocation — that repetition is small but it is paid forty
times an hour.

**Build cost:** small. This is argument plumbing over existing entry
points, plus the config file. A day.

### 4.2 The card diff — the highest-leverage single thing

**This is where it landed (v1.42): the lab's divergence view.** `[ vs
V12 ]` on any take opens the fork against stock V12 on the same frozen
turn — issued orders hour by hour, the directive behind them, the
reasoning, and the prompts as sent, each one diffed. One difference from
the sketch below is deliberate and worth keeping in mind: V12's side is
**frozen on disk** rather than asked live, because it is an LLM and
asking again would answer differently, which would leave you diffing
against a moving target instead of against what you forked.

Cards are ~30KB of text. Diffing them raw is useless: the board state,
timestamps and the whole rules block move around. What an attendee wants
is a *semantic* diff of the decision:

```
$ soc diff

board own_seam_d4 · seat p1 · day 4
  redwatch_reaper @ HEAD          vs   redwatch_reaper @ 3 edits ago

  MENU        41 options            42 options       +EMP_SALVO_1
  THINK       "hold the seam"       "contest the pure"
  PLAN        picked CH2, PR1       picked SMASH_GRAB, EMP_SALVO_1
  PACKAGER    5 moves, 0 repairs    7 moves, 1 repair (green avoid @ 12,9)
  MOVES       drop step×3 pickup    drop step×4 pickup + emp×3
  VALUE       est 210               est 604
```

Four sections, each one line unless it changed. This turns "I changed a
doctrine string" into a verdict in the time it takes to read six lines.

The pieces already exist: `card.py` renders the card, and the harness
already returns the option menu, the reasoning, the plan and the moves as
structured `extras`. What is missing is (a) persisting cards under a
content-addressed name so "previous" is well defined, and (b) the
comparator.

**This is the thing I would build first if I could only build one.**
`docs/HACKATHON_BUILD_PLAN.md` already reaches the same conclusion in
its Phase 7.5 note; this is agreement, not a new idea.

**Build cost:** two days, most of it deciding what counts as a
meaningful difference.

### 4.3 `soc why` — the decision on one screen

A card is complete, which makes it long. For the 0:50 moment ("why did it
do that?") the attendee wants one screen:

```
DAY 4 · SEAT p1 · redwatch_reaper

IT SAW      pure @ (12,9) [redsign, not yet contested]
            rival probe @ (14,8), 2 nights old
            vault 11/15 · 1750c · 50b

IT WAS OFFERED   CH1 seam@(4,3) est 180   CH2 seam@(11,9) est 240
                 SMASH_GRAB @(12,9) est 604   PR1..PR4   [no weapon options]

IT CHOSE    CH2 — "the pure is probably defended and I cannot
            afford to lose the hull this late"

IT DID      drop(11,9) step(11,10) step(12,10) pickup      est 240

⚠ 2 EMP in stock, 0 fired.  This is Gap 1 — see harness README §Gap 1.
```

That last line matters more than it looks. Pointing at the gap *at the
moment the attendee is looking at its consequence* is worth more than any
amount of README. The same treatment applies to Gap 2: flag unspent blue
against a vault that had room.

**Build cost:** a day on top of the card work — it is a different render
of the same structured data.

### 4.4 Make the advisor fork-aware

`GET/POST /api/game/{id}/advisor` already runs `harness.run(submit=False)`
and paints the suggestion on the board. It is hardcoded to V12. Making it
resolve through `binding_registry` like everything else means an attendee
can sit in a live game, press ASK, and watch *their own* agent's
reasoning appear over the board they are looking at.

This is the single most *enjoyable* item on the list, and enjoyment is
not a soft concern at a hackathon — it is what makes people iterate
forty times instead of twelve.

**Build cost:** small, if the advisor's V12 import is the only coupling.
Worth checking for hardcoded `tabula_v12` strings on the server side
before promising it.

### 4.5 A leaderboard, and therefore an end to the day

Without this the room is forty people tuning in parallel with no reason
to finish. With it, the day has a shape.

- `soc submit` runs a **fixed** battery: N seeds × seats, against
  RED_HARVEST, RED_HARVEST_LITE and stock V12, on pinned seeds so every
  entry faces the same boards.
- It writes a result row: agent label, team, score deltas, green-line
  count, weapons fired, wall-clock, token spend.
- A served page ranks them. `server/static/evals.html` already exists as
  an eval replay viewer and is the natural place to hang it.
- Every row links to a **replay** of one of its seasons. The leaderboard
  should be watchable, not just a table — this game is legible in replay
  and that is a large part of its charm.

Two design notes:
- **Rank on margin against a fixed opponent, not head-to-head Elo.**
  Elo needs many games; we will have a handful per team and a day.
  Margin-vs-baseline on pinned seeds is fairer and computable from one
  battery run.
- **Report token spend and wall-clock next to score.** Otherwise the
  winning strategy is "call the model nine times a turn", which teaches
  the wrong lesson and empties somebody's Snowflake credits.

**Build cost:** two to three days including the page. This is the largest
item and the most cuttable — the loop works without it, the *day* does
not.

### 4.6 A recipe book, not more reference docs

The harness README documents the two gaps well. What it does not have is
a worked example of a *small* change end to end. I would add three, each
one page, each following the same shape — change, command, verdict:

1. **Loosen a blue gate** (one number in `value_pyramid.py`) → `soc diff`
   → see the menu gain a blue option → `soc eval` → see two scenarios
   flip. The "you can do this in ninety seconds" example.
2. **Add an EMP option to the menu** (`agency.build_registry` +
   `packager` + move schema) → the real Gap 1 exercise, with the four
   gates named and the reference implementation in `heuristic_agent.py`
   ~2250–2410 cited.
3. **Rewrite a doctrine block** → `soc diff` showing the THINK prose
   change and the plan *not* changing, which is the most useful negative
   result in the whole exercise and the one nobody expects.

## 5. Build order

Ordered by (value to the attendee) ÷ (cost), with the constraint that
each step is useful on its own if we run out of time:

| # | Item | Cost | Why here |
|---|------|------|----------|
| 1 | Cards on by default + content-addressed storage | 0.5d | Prerequisite for everything; also fixes discoverability on its own |
| 2 | `soc` façade with `new` / `turn` / `card` | 1d | The front door; makes cards reachable |
| 3 | `soc diff` | 2d | The verdict. The single highest-value item |
| 4 | `soc why` | 1d | Turns the card into understanding; carries the gap nudges |
| 5 | Fix the eval-CLI backend inconsistency | 0.5d | Small, and it will otherwise eat somebody's morning |
| 6 | `soc eval` + `soc match` wrappers | 1d | Confirmation loops |
| 7 | Fork-aware advisor | 1d | Enjoyment, and the best explanation surface we have |
| 8 | Recipe book (3 worked changes) | 1d | Turns the tooling into a taught skill |
| 9 | `soc submit` + leaderboard page | 3d | Gives the day an ending |

Items 1–4 are the loop. If only those ship, the hackathon works.
Items 5–8 make it pleasant. Item 9 makes it a competition.

## 6. What I would deliberately not build

- **A GUI agent editor.** Attendees are here to write Python. A visual
  editor would take a week and produce worse agents.
- **Elo / a full tournament bracket.** Too few games, too much
  machinery. See 4.5.
- **A new eval framework.** There are ~56 scenarios and a working runner.
  The problem is not the scenarios, it is that nothing tells you whether
  *your* run beat *your last* run.

  **Honesty check (v1.42): the turn lab is arguably the thing this
  bullet forbids.** The defence is that it is not an eval framework — it
  scores nothing, asserts nothing and has no runner. It reuses the real
  UI and the real night simulator rather than drawing its own, so the
  only new code is the part that freezes a turn and clones it. But the
  warning still applies to whatever comes next: the moment the lab grows
  predicates, it has become the thing this bullet says not to build.
- **Auto-tuning / a meta-agent that edits the agent.** Tempting, and
  exactly the sort of thing that eats the whole day and teaches nobody
  anything.
- **Anything that requires a Snowflake account to iterate.** The fast
  loop must work against `memory` with one PAT. Cards, diffs and pinned
  turns all can.

## 7. Risks

- **Token cost.** Forty attendees × forty iterations × 3 LLM calls is a
  lot of inference. The pinned-turn loop is the mitigation (1 call, not a
  season), and `soc` should print a running token count so the cost is
  visible rather than discovered.
- **The card format is not stable.** `soc diff` couples to it. Either
  freeze the structured `extras` contract or have the diff work off the
  harness return value rather than the rendered text — the latter is
  better and is what I would do.
- **Fork drift.** `new_agent.py` copies V12 wholesale, so a bug fixed in
  V12 after minting never reaches the forks. Acceptable for one day; the
  README should say so plainly rather than let people discover it.
- **`soc` becoming a second toolchain.** If it wraps the scripts it is a
  façade; if it starts owning logic it is a rewrite. Keep the scripts
  working and callable directly.

## 8. Open questions

1. Is the advisor's V12 coupling only the import, or does the server hold
   other `tabula_v12` strings? (Determines whether 4.4 is one day or
   three.)
2. Which boards go in the pinned set for `soc turn`? It wants ~8 turns
   covering: an uncontested seam, a contested pure, a blue decision, a
   final night, a damaged hull, an EMP opportunity. Some exist in
   `reports/turn_suite/suite.json`; the set should be chosen so that a
   good change moves at least one and a bad change moves the wrong one.

   **Answered (v1.42), and by a smaller set than this expected.** The
   lab ships six named nights: *Vanilla Opener · day 1*, *Early Redsign
   Battle · day 2 · dual discovery*, *Beaten to the Seam · day 3*,
   *Second Wind · day 4*, *After the Gold Rush · day 4* and *Vetus
   Lantern · day 6 · late redsign race*. They were chosen the way this
   question asks for — each one isolates something (a cold open with no
   history, a symmetric race, being behind with a second chance on the
   table, an asymmetric fleet, a night with no jackpot at all, a late
   race where the leader is discovered and weapons exist) — but they are
   real turns, so what a fork is being asked is what a seat was actually
   asked. `turnlab/boards.py` holds the editorial note for each, which
   is the one hand-written part of a board; everything else is read off
   the saved session.

   **Four more in v1.40, for the same reason and by the same method.**
   None of the six could hold a SNAP — they were frozen before the
   weapon was priced, so the lab correctly refuses the rack on them, and
   a doctrine nobody can test is a doctrine nobody should trust. Three
   V12-vs-V12 seasons were played at the 1:2:3 ladder and four of their
   twenty-one nights kept: *Sighted and Armed · day 6 · the blind
   chase*, *Both Eyes on the Same Pure · day 3 · weapon poker*, *Two
   Ghosts, One Seam · day 2 · nobody can see it now* and *The Late
   Reversal · day 6 · sight against the scoreboard*. Each isolates a
   different shape of the only question a one-square denial answers —
   who can see the seam, and who knows what the other could fire: sight
   and the weapon on one side, both on both sides, neither on either,
   and the two split across the seats. The seventeen that were not kept
   were deleted, because a library is a set of nights somebody chose.
3. Do we score the leaderboard on one battery or best-of-N submissions?
   Best-of-N rewards resubmitting; one battery rewards being ready.
4. `two_seams_choose_one` currently fails in CI (a known regression, see
   `docs/OUTSTANDING_ISSUES.md` #27). The must-pass heuristic set should
   be green before we ask attendees to trust scenario results as a
   verdict.
