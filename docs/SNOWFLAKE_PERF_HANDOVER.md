# Handover: Snowflake turn latency — what changed, what to expect

**To:** the next agent working on Sea of Colours.
**Date:** 2026-09-02.
**Scope:** storage/latency work only. No game rules, no simulation, no agent
logic was modified.
**Full working detail:** `docs/SNOWFLAKE_LATENCY_BRIEF.md` §S–§V.

---

## 1. The one-paragraph version

A Snowflake turn cost **10.71s** against **0.07s** on the file backend. It is
now **1.19–1.40s** with heuristic seats. On a real 7-day LLM season the whole
thing is within **20%** of running locally. Nothing about the game changed;
the fix was to stop issuing so many separate SQL statements and to move the
hot tables to hybrid tables.

---

## 2. Why it was slow (this is the load-bearing fact)

Every store call on Snowflake is a separate HTTPS request that costs
**~300ms before it does any work at all**. Measured: `SELECT 1` is 27ms of
server time, 12.5ms of network round-trip, and **312ms** at the client, with
97.5% of that sitting in `poll_wait_for_socket` — blocked on the response.

So a turn costs roughly `statements x 300ms`, and the engine was issuing
11–14 of them. On the file backend the same calls are ~2ms each, which is why
the identical code is 150x faster locally. **Nothing was "slow" — the code was
just chatty, and chattiness is free locally and expensive remotely.**

Two consequences worth internalising:

- Payload size barely matters. A 4-row write and a 1120-row write cost about
  the same. Do not optimise payloads; optimise *statement count*.
- Warehouse size does not matter. It is an X-Small and that is fine;
  `queued_overload_time` is 0. Do not resize.

**Caveat I got wrong at first:** the ~300ms floor is not universal. Hybrid
table point lookups returning under 100KB come back in ~90ms — the documented
operational fast path genuinely beats it. The floor applies to standard tables
and to hybrid queries that miss that fast path (e.g. reading the 375KB
`json_state` blob, still ~540ms).

---

## 3. What changed

### 3.1 Six tables are now HYBRID TABLES — live, unconditional

`SOC_GAME_SESSION`, `SOC_GAME_LOG`, `SOC_REPLAY_FRAME`, `SOC_POLICY_QUEUE`,
`SOC_AGENT_INVOCATION`, `SOC_AGENT_MEMORY`.

**Zero code changes were needed.** The existing bound `MERGE` / `SELECT` /
`DELETE` statements work unaltered. Measured per-statement effect:

| statement | standard | hybrid |
| --- | --- | --- |
| `MERGE SOC_GAME_SESSION` (1.19MB blob) | 848ms | **243ms** |
| `MERGE SOC_AGENT_MEMORY` | 566ms | **142ms** |
| `MERGE SOC_POLICY_QUEUE` | 1048ms | **492ms** |
| `INSERT SOC_AGENT_INVOCATION` | 691ms | **427ms** |
| `INSERT SOC_GAME_LOG` | 658ms | **412ms** |
| `SELECT` agent memory by PK | 142ms | **90ms** |

Each has a `*_FDN_BAK` standard-table backup holding the pre-swap rows.
**Revert is two renames per table**, e.g.

```sql
ALTER TABLE SOC_GAME_SESSION RENAME TO SOC_GAME_SESSION_HT;
ALTER TABLE SOC_GAME_SESSION_FDN_BAK RENAME TO SOC_GAME_SESSION;
```

`snowflake/soc_schema.sql` still declares all six as standard tables. It is
`CREATE TABLE IF NOT EXISTS`, so re-running the deploy is a harmless no-op and
will **not** clobber the hybrid tables — but the file now under-describes
reality and should be updated.

### 3.2 `BufferedSocStore` — on by default since v1.43

`sea_of_colours/snowpark/buffered_store.py`. Wraps any `SocStore`, coalesces
writes, caches reads, flushes at explicit boundaries. **Disable with
`SOC_BUFFERED_STORE=0`**, which is the escape hatch if you suspect it and
want the unbuffered path back.

It shipped opt-in and was flipped on once the numbers below had been
reproduced on a real LLM season. Opt-in was the wrong default for a
performance fix nobody would find: the documented run command got you the
5.57s path, and the 1.2s path existed only for whoever had read this file.

Two durability tiers, because the consequences of loss differ:

| tier | contents | flushed by |
| --- | --- | --- |
| per **turn** | `json_state`, policy queue, parcel bundles | `flush_turn()`, called from `engine.save_session_full` |
| per **day** | `SOC_GAME_LOG`, `SOC_REPLAY_FRAME`, `SOC_AGENT_INVOCATION` | `flush()` on day rollover, season end, or `atexit` |

Game state is durable every turn — a crash can never corrupt or rewind a game.
A hard crash mid-day costs at most one day of LOG text and replay animation.
The day rollover is detected from the `day` on the session row, so **no caller
has to remember to flush.**

Per turn it removes: `load_session` 3 → 0 (write-through cache),
`save_session` 2 → 1 (coalesced), `append_log` 2–3 → deferred, frames and
invocations → deferred.

**Reads stay buffer-aware, so the deferral is invisible:**

- `load_session` is cached **only for sessions this process has written.**
  The cache never expires, so answering every read would pin a spectating
  process to the day it first saw — a server with a game open while
  `soc season` advances it elsewhere would show a board that never moved.
  Don't "optimise" that condition away.
- `list_log` merges pending entries and synthesises `seq` as
  `max(durable seq) + n` — exactly what the INSERT's folded `MAX(seq)+1`
  assigns, so displayed numbers match what lands.
- `day_index` adds pending frame counts, leaving `first/last_global_idx` null
  because those are assigned server-side and genuinely unknown until flush.
  `get_session_status` only sums `frame_count`.
- **Everything else falls through `__getattr__`, which flushes then
  delegates.** Any store method added later is therefore correct by default,
  merely slow. If you add a reader and it feels sluggish, that is why — give it
  an explicit buffer-aware override.

**Do not wrap attribute access in that `__getattr__`.** It passes
non-callables straight through, and `backend.snowpark_session_for` relies on
that: it does `getattr(store, "session", None)` to decide whether a game may
persist agent memory. A wrapped Snowflake store must still yield its Session;
a wrapped memory store must still yield `None`. Pinned by
`test_snowpark_session_for_still_resolves_through_the_wrapper`.

### 3.3 Two small engine/store fixes

- **`save_agent_rationale`** (`engine.py`) called `save_session_full` to
  persist one appended log line, which re-wrote both parcel tables with
  byte-identical data on every agent turn — 6 of 13 saves in a 6-turn season.
  Now writes only the authoritative row via `store.save_session`. `log_tail`
  semantics are unchanged.
- **`_replace_parcels`** (`snowpark_store.py`) issued a session-scoped DELETE
  even when replacing empty with empty. Hoards are empty for most of the early
  game, so a season spent ~8s on DELETEs matching no rows. Now skipped when
  this store instance has already emptied that exact
  `(table, session, owner-set)` scope. Kill-switch:
  `SOC_SKIP_EMPTY_PARCELS=0`.

### 3.4 The §7 ordering guarantee is now stronger

"The authoritative session row is written LAST" used to be a convention spread
across 8 `save_session_full` call sites. It is now **one line at the bottom of
`BufferedSocStore._emit()`**, in one place, and a caller cannot get it wrong.
Pinned by `test_session_row_is_written_last_on_every_flush`. Do not relax that
test — it guards the "day 3 repeats three times" replay bug.

### 3.5 Every season now self-checks

`scripts/soc.py season` runs `_season_integrity()` after every run and
**exits non-zero** on failure. It checks replay frames are contiguous and
unique per day, and that no log line or `seq` is duplicated. This is the only
detector for the §7 bug that works, because the race cannot reproduce on the
file or memory backends. Trust it over a green local test suite.

---

## 4. Numbers to expect

### Heuristic seats (`bench_store.py --backend snowflake --turns 3`)

| step | mean turn |
| --- | --- |
| before any of this | 10.71s |
| app-side fixes only | 10.59s |
| + `SOC_GAME_SESSION` hybrid | 7.27s |
| + 4 more tables hybrid | 5.57s |
| + buffered store | **1.19–1.40s** |

**Note the asymmetry:** the hybrid half is an account-side change, so a
fresh account deployed from `soc_schema.sql` gets standard tables and sits at
~10.6s no matter what the code does. The buffered half travels with the repo
and is now on by default, so out of the box you get the last row here on the
live account and roughly the 10.7 → buffered improvement on a fresh one.

### Real 7-day LLM season, `emp_harvest_test` vs `tabula_v12`, 26 turns

| | wall | per turn |
| --- | --- | --- |
| file (local) | **280.9s** | 10.80s |
| Snowflake (hybrid + buffered) | **336.3s** | 12.93s |
| storage cost | +55.4s | **+2.13s/turn** |

**Ratio 1.20x.** For comparison, the original brief measured a 7-day season
with *one* LLM seat at 158–167s file vs 698–721s Snowflake — a 4.3x ratio.
Storage is now ~16% of an LLM turn instead of the majority. Model time
dominates, which is the right shape.

Five concurrent 3-day seasons complete in **46s wall total**; a single 3-day
season used to take 127s.

### How to reproduce

```bash
# per-turn store timings. Buffering is the default now, so the interesting
# run is the one that turns it OFF — that is the 5.57s row.
PYTHONPATH=. python scripts/bench_store.py --backend snowflake --turns 3
SOC_BUFFERED_STORE=0 PYTHONPATH=. python scripts/bench_store.py \
    --backend snowflake --turns 3
PYTHONPATH=. python scripts/bench_store.py --backend file --turns 3

# a season
PYTHONPATH=. SOC_BACKEND=snowflake \
  python scripts/soc.py season --p1 red_harvest --p2 red_harvest \
  --backend snowflake --days 3 --seed 424 --name CHECK
```

Diagnostic probes left in `scripts/`: `probe_latency.py` (the 300ms floor and
MERGE/UPDATE/hybrid shape matrix), `probe_hybrid.py` (A/B of the real
`save_session` statement), `probe_staleness.py` (hybrid cross-session
consistency). They create and drop their own scratch objects.

---

## 5. Operational gotchas

1. **Hybrid primary keys are enforced.** A duplicate
   `(session_id, day, seq)` now raises instead of silently duplicating. That is
   an improvement — silent corruption became a loud failure — but a season can
   now fail where it used to quietly produce a bad replay.
2. **Do table renames with the web server down.** A server that starts during
   a rename window probes a table that momentarily does not exist, and the
   `multi` store does its documented degrade-to-local **and caches that
   decision**. Observed live: it served 19 file sessions and returned
   "session not found" for every Snowflake game. No data is affected; fix is
   `python run_web.py --port 8000 --replace --no-reload`.
3. **Hybrid consistency is session-based** — reads outside the writing session
   can be up to 100ms stale. Measured on this app: **zero stale reads in 160
   attempts**, because the client cannot issue a follow-up read sooner than
   269ms after a write returns (the ~300ms request floor), so the window has
   already closed. Parallel *seasons* have no exposure at all — separate
   processes, separate sessions, disjoint rows. If anyone ever sets
   `SOC_SF_CONNECTIONS>1`, set `READ_LATEST_WRITES = true` (measured cost:
   inside noise).
4. **DDL needs `ACCOUNTADMIN`.** `SYSADMIN` has no `CREATE TABLE` or
   `CREATE VIEW` on `SOC_HACKATHON_DB.SEA_OF_COLOURS`.
5. **Multi-statement requests do not help.** Measured warm at n=1/3/6, they are
   a wash with separate statements — Snowflake runs the batch sequentially and
   the driver polls per result set. Do not reach for `MULTI_STATEMENT_COUNT`
   expecting one round trip.
6. **`--seed` does not reproduce a season.** Three runs of identical code gave
   `344/2`, `694/344` and `0/0`, and fixing `PYTHONHASHSEED` does not help.
   You cannot regression-test by comparing outputs. Verify structural
   invariants instead. This also means every before/after wall-clock
   comparison in the brief is across *different games* — treat single-season
   timings as indicative, not precise.

---

## 6. Things that are correct as-is — please do not "fix" them

- **`except Exception: pass` around the agent-memory writes** (in
  `_v7/memory.py`, `frontier.py`, `hazard_memory.py`). I initially flagged the
  silence as a latent bug. It is not: the agent is sometimes fired on a human
  turn with no store or session at all, and the quiet skip is exactly what
  makes that work. Leave it.
- **Agent memory during a game on the file backend.** I also got this wrong
  first time. All four memory kinds work in-process on every backend —
  `_IN_MEMORY_STORE` (`arena:dayN`), `_CACHE` (`arena:hazard_cells`),
  `_BLUE_CACHE` (`arena:spent_blue`) and `_ENEMY_LANDINGS`
  (`enemy_probe_disk_history`) each hydrate on a cold cache and then accumulate
  in process. The only difference is **resuming a session in a new process**:
  Snowflake rehydrates, the file backend starts cold. That is by design.
- **The X-Small warehouse.** Not the bottleneck. See §2.

Verified for real: agent memory round-trips through the buffered store on the
hybrid table (write → clear the in-process dict → read back). The 7-day
Snowflake season wrote **20 `SOC_AGENT_MEMORY` rows**, 10 per seat, with
`enemy_probe_disk_history` showing genuine cross-day accumulation
(`"14,12": {count: 2, first_day: 2, last_day: 3}`) — which only appears if the
read-modify-write cycle is working end to end.

---

## 7. Final observation — for you, not for me

**Roughly half of every LLM season's turns run on a heuristic, and the
season's own reporting says zero.**

From both 7-day seasons, classified from the turn records and confirmed
against `SOC_AGENT_INVOCATION`:

| seat | agent | planning turns | orbit turns |
| --- | --- | --- | --- |
| p1 | `emp_harvest_test` | 7 — model reached (`fallback=False`) | 6 — `[orbit heuristic]` |
| p2 | `tabula_v12` | 7 — model reached (`fallback=False`) | 6 — `[fallback after harness_in_process orbit miss]` |

The audit trail agrees: for p2, `SOC_AGENT_INVOCATION` holds 6 rows under
`agent_id = 'RED_HARVEST'` — the heuristic recorded as the actor for those
orbit turns.

**The counts are identical on file and on Snowflake (6 and 6 on both), so this
is purely agent-side and nothing to do with the storage work.**

Two distinct things are happening and they should not be conflated:

- p1's `[orbit heuristic]` looks **deliberate** — a labelled orbit planner in
  the `emp_harvest_test` fork. If so, fine, but it means the fork's orbit play
  is not its model's.
- p2's `[fallback after ... orbit miss]` is the **miss path** at
  `sea_of_colours/orchestrator_2/runtime.py:180-191`. The harness is dispatched
  for the orbit phase, `dispatch.submitted_policy` comes back falsy,
  `_seat_has_pending` still shows nothing after a 200ms sleep, and the runtime
  lands a heuristic orbit plan so the night can resolve. It happens on **every
  single orbit turn**, 6 for 6, on both backends.

**Why this matters more than it looks:** `season.json` reports
`fallback_turns: 0` and `fell_back: False` on all 26 turns. So a season summary
tells you the model was reached every time, while 12 of 26 turns were actually
planned by `plan_orbit_actions`. Any eval that compares agents on season score
is currently scoring the heuristic for half of each season, silently.

Worth ruling out before reading anything into agent-vs-agent results:

1. Is the orbit prompt/tool actually reaching the model, or is the orbit
   dispatch returning early? `runtime.py:172` is where `dispatch_turn` is
   called for the orbit phase.
2. Is `dispatch.submitted_policy` simply not being set on the orbit path even
   when the harness *did* submit? The retry via `_seat_has_pending` reads
   `get_session_status(...)["pending"]`, which is derived from the session blob
   — and with the buffered store that read is served from the write-through
   cache, so it cannot be a visibility problem.
3. Should `fallback_turns` count orbit misses? Right now nothing surfaces them
   except reading rationales, which is how this went unnoticed.

I have deliberately not touched any of it — it is agent behaviour, it is your
active area, and I would be guessing at intent.
