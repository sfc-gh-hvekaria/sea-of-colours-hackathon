# Brief: Snowflake turn latency (13s → target ~2s)

**For:** an engineer/agent who knows Snowflake well.
**Status:** diagnosed and measured, not fixed. No fix has been attempted
beyond one that was tried, measured, found useless, and turned off.
**Date:** 2026-09-01

---

## 1. The question, in one line

A game turn costs **13 seconds** against Snowflake and **0.08 seconds**
against a local file store, doing the *same 21 store calls*. We believe
the cause is per-statement latency times statement count. **Is that
right, and what is the correct way to fix it without breaking a
documented concurrency guarantee?**

---

## 2. What the app is (only what you need)

Turn-based strategy game. Pure-Python engine; storage is behind one
interface (`SocStore`) with three implementations:

| Impl | File | Notes |
| --- | --- | --- |
| memory | `sea_of_colours/snowpark/store.py` | dict-backed |
| file | `sea_of_colours/snowpark/file_store.py` | JSON on disk |
| **Snowflake** | `sea_of_colours/snowpark/snowpark_store.py` | Snowpark, the subject here |

Backend is chosen **per game**, not per process
(`sea_of_colours/snowpark/backend.py`). A `multi` mode read-merges
Snowflake + local.

The authoritative game state is a **single JSON blob**,
`SOC_GAME_SESSION.json_state`. `_hydrate_session`
(`engine.py:378`) reads *only* that row. Every other table
(`SOC_GRID_CELL`, `SOC_SQUARE_IDENTITY`, `SOC_ENTITY_STATE`,
`SOC_ASSET_RECORD`, `SOC_HOARD_PARCEL`, `SOC_SHIPPED_PARCEL`) is a
**write-only projection** — verified: no store class has a read method
for them, and `snowflake/soc_views.sql` and `soc_procedures.sql` never
reference them. They exist so a human can query the game in SQL.

---

## 3. The measurements

All numbers from `scripts/bench_store.py` (see §6) and the engine's own
`SOC_PERF=1` instrumentation. Board 40×28 = 1120 cells, heuristic
(non-LLM) seats so no model time is included.

### 3.1 Headline

| | file backend | Snowflake |
| --- | --- | --- |
| mean turn | **0.08s** | **13.07s** |
| store calls per turn | 21 | 21–24 |
| sum of call time ÷ wall | x1.9 | x1.5–1.7 |
| 7-day season (26 turns, 1 LLM seat) | **158–167s** | **698–721s** |

Season figures are three real Snowflake seasons and two file seasons,
same seeds and agents. Consistent to within 3%.

### 3.2 Per-call cost does not depend on payload

One turn, Snowflake:

```
store method                 calls   seconds   ms/call    rows
save_session                     2      3.41      1704      16
append_log                       2      2.53      1267       2
upsert_square_identity           2      2.49      1244    2240
upsert_grid_cells                2      2.44      1218    2240
replace_entity_state             2      2.42      1208      16
load_session                     3      2.30       767       0
upsert_asset_records             2      2.12      1061      16
replace_hoard_bundle             2      1.55       773       4
replace_shipped_bundle           2      1.44       721       4
append_agent_invocation          1      1.00      1003      10
list_log                         1      0.39       391       0
------------------------------------------------------------
WALL                            21     12.58
```

**A 4-row write costs the same as a 1120-row write.** `list_log` reads
nothing and costs 391ms.

### 3.3 Raw round-trip floor, and parallelism

With a 7-connection pool, warm:

```
7 concurrent "SELECT 1"  -> wall 0.32s  (each ~0.30s)
1          "SELECT 1"    -> wall 0.30s
```

So: **~0.30s floor per statement**, and **7 statements in parallel cost
the same as 1** when each has its own connection.

### 3.4 The save fan-out

`save_session_full` (`engine.py:238`) writes 7 tables across a
`ThreadPoolExecutor`, then writes the authoritative row last on the main
thread. `SOC_PERF=1` breakdown of one save:

```
save_session_full (parallel · wall ≈ max) = 1994ms
  ssf.upsert_square_identity   1994ms  (1120 rows)
  ssf.replace_entity_state     1965ms  (4 rows)
  ssf.upsert_grid_cells        1574ms  (1120 rows)
  ssf.save_session             1499ms
  ssf.upsert_asset_records     1443ms
  ssf.replace_hoard_bundle     1143ms
  ssf.replace_shipped_bundle    912ms
```

and one full turn's submit path:

```
POST /policy TOTAL=6128ms
  save_session_full            3176ms
  append_log                   1239ms
  upsert_policy                1074ms
  hydrate_session               641ms
```

### 3.5 Statements are already batched

`upsert_grid_cells` (`snowpark_store.py`) is **one** `MERGE` with all
1120 rows in a single `VALUES` list — already optimised in v0.9.6.
Hoard/shipped are bundled into one DELETE + one INSERT. So the
per-statement work is not obviously reducible.

---

## 4. What we ruled out

| Hypothesis | Verdict | Evidence |
| --- | --- | --- |
| Persistence overhead outside the turns | **No** | only 29s of a 719s season sits outside turn timings |
| Hundreds of chatty round-trips | **No** | 21–24 store calls per turn, counted |
| Huge payloads / row volume | **No** | 4 rows ≈ 2240 rows in cost (§3.2) |
| Writing the two big write-only projections | **No** | removing them barely moves wall time; phases are parallel and a 4-row phase is just as slow |
| Connector queuing on one shared session | **No** | a 7-connection pool changed 13.07s → 12.83s (noise), while 7 parallel `SELECT 1` = 0.32s |
| The LLM being slower on Snowflake | **No** | same seed, file vs Snowflake: 21.2s vs 56.3s per LLM turn — the delta is store calls inside the turn, model time is constant |

### The connection pool we built and disabled

`SnowparkSocStore.__init__` now accepts `session_factory` +
`max_connections` and has a borrow/return pool (`_borrow`,
`snowpark_store.py:126`). **Default is `max_connections=1`, i.e. off**,
set in `backend.py:254` (`_max_connections`, env
`SOC_SF_CONNECTIONS`). It is left in with the measurement written into
its docstring so the experiment is not repeated. It gave no gain and
cost ~2.8s per extra connection to open.

Note it was implemented as a pool, **not** thread-locals, because
`save_session_full` builds a *new* `ThreadPoolExecutor` per save — so
thread-affine sessions would open new connections on every save.

---

## 5. Our current theory and the proposed fix

**Theory:** cost ≈ (number of statements) × (0.3–1.7s each), and most of
the statements in a turn are **sequential by construction**, not
parallelisable. Payload and connections are irrelevant.

**Visible waste in a single turn:**

- `save_session`, `upsert_grid_cells`, `upsert_square_identity` each run
  **twice**. `save_session_full` has ~8 call sites in `engine.py`
  (lines 462, 757, 1098, 1223, 1300, 2578, plus
  `game/snowflake_store.py:38`) — a turn hits two of them, plausibly
  "policy submitted" and "night resolved".
- `load_session` runs **three times** (~767ms each) where one cached
  read would do.

**Estimated ceiling of that work:** ~13s → ~4–5s. Collapsing the
7-phase save into one multi-statement request could go further.

**What we want your judgement on** is whether the *right* answer is
instead one of:

1. **Multi-statement requests** — the Python connector supports
   `MULTI_STATEMENT_COUNT`; can the 7-phase save become one round-trip,
   and does that interact badly with the ordering guarantee in §7?
2. **Async / write-behind** — the projections in §2 are write-only.
   Could they be fire-and-forget, with only `json_state` synchronous?
3. **Warehouse configuration** — is a ~0.3s floor and ~1–1.7s DML
   normal for a small warehouse, and does size/auto-suspend/multi-cluster
   change it? We have not tried resizing. (We do not know the current
   warehouse size; config lives in `~/.ssh/sf_config`.)
4. **Something else entirely** — e.g. is `MERGE` the wrong shape here,
   would `INSERT OVERWRITE` / staged `COPY` / a single VARIANT column be
   dramatically cheaper?
5. **Hybrid tables** — would these tables be a fit, given the access
   pattern is small, frequent, point writes?

---

## 6. How to reproduce — the benchmark

```bash
# baseline, no credentials needed
python scripts/bench_store.py --backend file      --turns 3

# the subject
python scripts/bench_store.py --backend snowflake --turns 3

# engine's own per-phase save timings
SOC_BACKEND=snowflake SOC_PERF=1 python scripts/soc.py season \
    --p1 red_harvest --p2 red_harvest --backend snowflake --days 1
```

`scripts/bench_store.py` wraps the store in a timing proxy
(`TimingStore`) and plays real turns through the real engine. It changes
nothing in `sea_of_colours/`, so it measures the true code path. It
reports per-method calls, total seconds, ms/call and payload size, and
flags whether calls are overlapping or serialising.

A full 7-day season, for end-to-end confirmation:

```bash
python scripts/soc.py season --p1 tabula_v12 --p2 red_harvest \
    --backend snowflake --seed 11 --name PERF_TEST
```

Relevant env flags:

- `SOC_PERF=1` — engine per-phase timings to stderr
- `SOC_PARALLEL_SAVE=0` — disable the save fan-out (`engine.py:949`)
- `SOC_SF_CONNECTIONS=7` — re-enable the connection pool (default 1)
- `SOC_BACKEND` — `memory` | `file` | `multi` | `snowflake`

---

## 7. The risk — READ THIS BEFORE CHANGING WRITE ORDER

There is a **documented, previously-shipped bug** in this exact code.
From `engine.py:292`:

> the AUTHORITATIVE control-flow row (`SOC_GAME_SESSION.json_state`) is
> written SYNCHRONOUSLY on the main thread, NEVER in the parallel pool
> below. `_hydrate_session` reads this row EXCLUSIVELY, so if its MERGE
> shares the one Snowpark Session with 6 sibling writes on a thread
> pool, a nondeterministic interleave can let the very next
> `_hydrate_session` observe a PRE-resolution snapshot. That stale read
> makes the season runner re-pick an already-submitted seat /
> force-resolve again, so the night is simulated 2-3x and its replay
> frames + log lines are appended repeatedly (the "day 3 repeats three
> times" replay bug).

Consequences for any fix:

- The `json_state` write must remain **last** and must be **committed
  and visible** before the next `_hydrate_session`.
- **This class of bug cannot reproduce on the file or memory backend** —
  those writes are synchronous and instant, so the race window never
  opens. A green local test suite proves nothing here. Any change must
  be validated against a real Snowflake account, ideally by running
  multiple full seasons and checking for repeated days in the replay.
- `append_replay_frames` is documented as idempotent per
  (session, day) (`snowpark_store.py:787`), which is a mitigation, not
  a licence.

Other constraints:

- Backend is **per game**; code must never ask "is the process on
  Snowflake?" — it asks the store. `tests/test_per_game_backend.py`
  enforces this.
- The same `engine.py` also backs Snowflake **stored procedures**, so
  changes must remain valid in that execution context.
- `tests/test_snowpark_batching.py` pins the batching shape (asserts a
  single `MERGE INTO SOC_GRID_CELL`).

---

## 8. Why this matters

- **Live play**: every solo and multiplayer turn on Snowflake makes a
  human wait ~13s. This is the main motivation.
- **Headless seasons / tournament**: a 7-day season is 700s instead of
  160s, and we want to run many in parallel.
- The file backend proves the engine itself is not slow: 0.08s/turn.

Target: get a Snowflake turn under ~2s. Anything approaching the file
backend's 0.08s is a bonus, not expected.

---

## 9. Deliverable we would like

1. Confirm or correct the diagnosis in §5.
2. A recommended approach, with the ordering guarantee in §7 preserved.
3. If it involves warehouse/account configuration rather than code, say
   so — that is a perfectly good answer and we have not explored it.
4. A way to validate the fix on real Snowflake, since local tests
   cannot catch the regression we are most afraid of.

---

# Solution

**Status:** answered 2026-09-01, from server-side evidence. Not yet
implemented.

Evidence base: `information_schema.query_history` for
`SOC_HACKATHON_WH` + `SOC_WH`, 4,310 statements over 3 days. Region is
`AWS_EU_WEST_2`.

---

## S1. The diagnosis in §5 is half right — and the wrong half is the one being fixed

Server-side times:

| statement | n | total ms | **compile ms** | exec ms | **SQL text bytes** |
| --- | --- | --- | --- | --- | --- |
| `MERGE SOC_GAME_SESSION` | 67 | 1105 | **399** | 706 | **510,850** |
| `MERGE SOC_SQUARE_IDENTITY` | 229 | 949 | **479** | 292 | 74,118 |
| `MERGE SOC_GRID_CELL` | 67 | 736 | **454** | 282 | 58,345 |
| `MERGE SOC_ASSET_RECORD` | 229 | 688 | 254 | 258 | 2,599 |
| `INSERT SOC_ENTITY_STATE` | 229 | 316 | 125 | 192 | 1,448 |
| `INSERT SOC_REPLAY_FRAME` | 24 | **3177** | **1397** | 1780 | **1,174,369** |
| `SELECT ... SOC_GAME_SESSION` (load) | 613 | 123 | 76 | 47 | 150 |
| `SELECT 1` | 15 | **37** | 36 | 1 | 8 |

Summing one turn's statements from that table (2 saves × 7 phases, 3
loads, log, policy, agent invocation): **≈13.0s of server-side time.**
Wall clock is 13.07s.

Therefore:

- **There is no 0.3s network floor to blame.** `SELECT 1` is 37ms on the
  server. The 300ms client measurement in §3.3 is ~260ms of
  Snowpark/connector per-call overhead, and it is *not* the bottleneck —
  server time alone already accounts for essentially the whole turn.
  Region is `AWS_EU_WEST_2`, so it is not geography either.
- **"A 4-row write costs the same as a 1120-row write" (§3.2) is an
  artefact of the instrumentation.** Server-side: 4-row entity state =
  316ms, 1120-row square identity = 949ms. The client-side numbers
  flattened this because the parallel pool makes every phase report ≈
  the wall of the slowest one.
- **`queued_provisioning_time` is 0–180ms and `queued_overload_time` is
  0.** The XS warehouse is not starved. Resizing will not help
  (§5 option 3 is a no).
- The connection-pool result in §4 was correct, and is now explained:
  pooling cannot help when the cost is server-side work.

**The actual cost driver is `compilation_time`, and compilation time is
proportional to SQL text size.** Every literal is inlined. The
authoritative session MERGE ships **half a megabyte of JSON as a SQL
literal**; a replay-frame insert ships **1.17 MB**. Compilation is
400–1400ms of pure parse on generated text. Roughly 40% of a turn is
Snowflake parsing string literals.

§3.5 — "statements are already batched, so per-statement work is not
obviously reducible" — is where the reasoning went wrong. Batching into
one giant `VALUES` list traded round-trips for compile time, and compile
time turned out to be the more expensive currency.

---

## S2. Recommended fix, in order

### (a) Bind parameters. Stop inlining.

Highest win-to-risk ratio, no ordering implications at all.

- `SOC_GAME_SESSION.json_state`:
  `MERGE ... USING (SELECT ? AS session_id, ? AS json_state)` — 510KB of
  SQL becomes ~200 bytes plus a bind. Expect ~1105ms → ~250ms.
- Grid cell / square identity: replace the 1120-row `VALUES` list with
  **one bind of a JSON array + `LATERAL FLATTEN`**:

  ```sql
  MERGE INTO SOC_GRID_CELL t
  USING (SELECT v.value:x::int AS x, v.value:y::int AS y, ...
         FROM TABLE(FLATTEN(input => PARSE_JSON(?))) v) s
  ON ...
  ```

  74KB → ~1KB of SQL. Compile drops from 479ms to tens of ms, and the
  plan becomes cacheable across turns. This changes the shape
  `tests/test_snowpark_batching.py` pins — it still asserts a single
  `MERGE INTO SOC_GRID_CELL`, so update the assertion, do not drop it.
- `SOC_REPLAY_FRAME`: same treatment, or stage-and-`COPY` if frames grow.
  3.2s → well under 1s.

### (b) Delete the six write-only projections from the write path entirely

This is the big one, and it *strengthens* §7 rather than threatening it.

Those tables exist so a human can query the game in SQL (§2). They are
derivable from `json_state`. So derive them:

- **Views with `LATERAL FLATTEN` over `json_state`** — zero write cost,
  always consistent by construction. `snowflake/soc_views.sql` is
  already the right home.
- If flattening 1120 cells per query is too slow for ad-hoc SQL, put a
  **Dynamic Table** (`TARGET_LAG = '1 minute'`) on top of the view.
  Snowflake maintains the projection off the JSON; the turn writes
  nothing.

This removes ~8s of the 13s and reduces `save_session_full` to a single
statement. It also removes the entire §7 race window: if nothing else is
written concurrently, no sibling write can interleave with the
authoritative MERGE. The `ThreadPoolExecutor` gets deleted, not tuned.

### (c) Then the small stuff

- `load_session` 3× → 1× per turn with a per-turn cache (613 loads in 3
  days at 123ms each).
- `append_log` and `append_agent_invocation` each run a
  `SELECT COALESCE(MAX(seq))` before the `INSERT` — two statements where
  one will do. Use an `IDENTITY`/sequence, or `(SELECT MAX(seq)+1 ...)`
  inside the insert.
- `auto_suspend` on `SOC_HACKATHON_WH` is 60s. For live play with human
  think-time that guarantees cold resumes. Set it to 300s. This is the
  only warehouse change worth making — **do not resize**, compile time
  does not shrink with warehouse size.

### (d) The architecturally right answer, to go past ~2s

Run the turn *inside* Snowflake. `engine.py` already backs stored
procedures (§7). One `CALL soc_submit_policy(...)` = one round trip, all
statements execute server-side with zero client overhead, and ordering
becomes plain sequential execution inside a single transaction — §7 stops
being a hazard to police and becomes a property you get for free.

Since (a)+(b) should already land at 1–2s, treat this as the option for
the tournament / parallel-seasons goal (§8) rather than a prerequisite.

### Verdict on the five options in §5

| Option | Verdict |
| --- | --- |
| 1. Multi-statement requests | Largely moot after (b) — one write left. If the projections are kept, `MULTI_STATEMENT_COUNT` is safe for them *provided* `json_state` stays a separate later statement; statements in a batch execute in submission order, which is **stronger** than the current thread pool. |
| 2. Async / write-behind | Unnecessary once projections are derived, and it adds a stale-read failure mode. |
| 3. Warehouse configuration | **No**, except `auto_suspend`. Zero overload queuing; compile-bound work does not respond to size. |
| 4. Something else | `MERGE` is not the wrong shape. The *literals* were. |
| 5. Hybrid tables | Genuine fit for `SOC_GAME_SESSION` + `SOC_POLICY_QUEUE` (point writes, read-your-writes, row-level locking would kill the §7 race by construction) — but do not reach for them until (a)+(b) are measured; they would be solving a problem that no longer exists. |

---

## S3. Validating on real Snowflake

The §7 fear is legitimate and local tests cannot catch it. Note that
fix (b) removes the race by removing the concurrency, so the validation
target shifts from "did we preserve ordering" to "is there anything left
that could interleave".

1. **Static gate.** Assert that `save_session_full` issues exactly one
   statement against `SOC_GAME_SESSION`, and that no sibling write
   executes after it. Cheap, runs locally, catches reintroduction.
2. **The real detector — query the account, not the app.** After each
   season, look for the symptom directly:

   ```sql
   select day, count(*) from SOC_REPLAY_FRAME
   where session_id = ? group by day having count(*) > <expected_frames>;

   select day, level, text, count(*) from SOC_GAME_LOG
   where session_id = ? group by 1,2,3 having count(*) > 1;
   ```

   Duplicated days show up as inflated frame counts and repeated log
   lines. Wire this into `scripts/soc.py season` as a post-run assertion
   so every season self-checks.
3. **Force the window open before trusting it.** Run 5+ concurrent
   seasons against one warehouse with different seeds — that is the
   parallel-tournament target anyway (§8) and the highest-contention
   case that will ever ship.
4. **Confirm the fix landed server-side, not just client-side.** Re-run
   the `query_history` query behind the S1 table and check
   `compilation_time` and `length(query_text)` both dropped. If average
   SQL bytes is still five digits, the binds did not take effect.

---

## S4. Headline

This is not a latency problem. It is a **compile-time and
write-amplification** problem, and both halves are fixable in
`snowpark_store.py` plus `snowflake/soc_views.sql`.

---

# Reply: we implemented S2(a) and half of S2(b) — results and open questions

**To:** the engineer/agent who wrote the Solution above.
**From:** the app side.
**Date:** 2026-09-01. **Version tag in code:** `v1.41`.
**Status:** implemented, deployed, validated on the real account.

---

## R0. Read this first

**Your diagnosis was right and your fix works.** SQL text is gone as a
cost: the authoritative session MERGE went from **510,850 bytes to 638**,
the replay insert from **1,174,369 bytes to 954**, and compile time fell
everywhere you said it would.

**But the turn only went 13.07s → 10.71s (-18%).** We want your read on
why, because we think the answer is visible in your own S1 table and we
would rather you confirm it than take our word.

Short version of our reading: **`compilation_time` was never the majority
of the statement we care most about.** Your S1 row for
`MERGE SOC_GAME_SESSION` is `total 1105 / compile 399 / exec 706` — exec
was already 64% of it. Removing the 510KB literal removed the 399ms and
left the 706ms untouched (we now measure 733ms). The 40%-of-a-turn figure
was carried mostly by `INSERT SOC_REPLAY_FRAME` (compile 1397ms), which
fires a handful of times a night, not by the statement that fires on
every save.

So we have moved the bottleneck rather than removed it, and the two
things now in front of us are **execution time on a 375KB point write**
and **statement count against a ~260ms round-trip floor**.

---

## R1. What we changed, precisely

All in `sea_of_colours/snowpark/snowpark_store.py` unless noted.

### R1.1 Bind plumbing

`_exec(sql)` → `_exec(sql, params=None)`, forwarding to
`session.sql(sql, params=[...]).collect()`.

We verified before writing any of it that Snowpark honours `?` binds
server-side (the connector's default paramstyle is `pyformat`, so this
was not a given). Throwaway probe, 544KB payload, same statement twice:

```
head                                        sql_bytes   compile     exec
INSERT INTO _bind_probe SELECT ?, PARSE_           47     100ms    198ms   <- bound, warm
INSERT INTO _bind_probe SELECT 'inlined'      544,065     324ms    217ms   <- inlined
INSERT INTO _bind_probe SELECT ?, PARSE_           47     409ms    461ms   <- bound, cold
```

Note the cold-vs-warm bound rows: **409ms → 100ms compile**, i.e. the
constant SQL text does get a cached plan, as you predicted. Round-trip
was lossless (536,009 bytes back for 544,009 sent; the delta is
`json.dumps` separators, not truncation).

### R1.2 Statements converted to binds

| method | statement | binds |
| --- | --- | --- |
| `save_session` | `MERGE SOC_GAME_SESSION` | 8, incl. `PARSE_JSON(?)` for `json_state` |
| `upsert_policy` | `MERGE SOC_POLICY_QUEUE` | 5 |
| `append_replay_frames` | `INSERT SOC_REPLAY_FRAME` | 2 |
| `append_log` | `INSERT SOC_GAME_LOG` | 4 |
| `append_agent_invocations` | `INSERT SOC_AGENT_INVOCATION` | 3 |

### R1.3 `VALUES` lists → bound JSON array + `LATERAL FLATTEN`

Exactly the shape you specified. For replay frames the old statement
built a `VALUES` tuple per frame with `PARSE_JSON('…')` per VARIANT
column; the new one is a single constant statement:

```sql
INSERT INTO SOC_REPLAY_FRAME (session_id, day, frame_idx, global_idx, …)
SELECT v.value:session_id::STRING, v.value:day::INT, v.value:frame_idx::INT,
       m.base + v.index, …, v.value:cells, v.value:entities, …
FROM TABLE(FLATTEN(input => PARSE_JSON(?))) v,
     (SELECT COALESCE(MAX(global_idx), -1) + 1 AS base
      FROM SOC_REPLAY_FRAME WHERE session_id = ?) m
```

Two things worth flagging to you:

- **No `PARSE_JSON` per column any more.** Once the array is parsed,
  `v.value:cells` is already VARIANT. 20 columns' worth of `PARSE_JSON`
  calls disappeared.
- **We raised the chunk from 50 to 100 frames.** The old cap existed to
  stay under the 1MB *SQL text* limit, which no longer applies. Tell us
  if there is a bind-size ceiling we should respect instead — we are
  currently sending up to ~2MB in one bind and it works, but we do not
  know where it breaks.

### R1.4 Statement folding (your S2(c))

Three `SELECT COALESCE(MAX(seq)…)` round-trips folded into their inserts
as a cross-joined scalar, in `append_log`, `append_agent_invocations` and
`append_replay_frames`. Each saves one statement per call.

For replay frames this also means `global_idx` is now assigned
server-side. Chunks need no offset because each chunk is its own
statement and re-reads `MAX` over the previous chunk's committed rows;
the subquery sees a pre-insert snapshot so the max cannot shift under the
rows being written. **Verified empirically** — see R5.

### R1.5 Four projections removed from the write path

`sea_of_colours/snowpark/engine.py::save_session_full` went from 7 phases
to 3. Deleted: `upsert_square_identity`, `upsert_asset_records`,
`replace_entity_state`, `upsert_grid_cells`. Kept: `replace_hoard_bundle`,
`replace_shipped_bundle`, then `save_session` last on the main thread.

The in-process payload builders for the four (`_grid_cell_rows` etc.) are
no longer called either — that also stops building 1120 dicts per save.
The store methods remain implemented for backfills.

### R1.6 `SOC_LEADERBOARD` derived from `json_state`

`snowflake/soc_views.sql`, redeployed and verified:

```sql
CREATE OR REPLACE VIEW SOC_LEADERBOARD AS
SELECT s.session_id,
       r.value:owner::STRING                               AS player,
       SUM(r.value:total_red_harvested::INT)               AS total_red_harvested,
       SUM(r.value:total_days_on_surface::INT)             AS total_days_on_surface,
       COUNT_IF(r.value:destroyed_on_day::INT IS NULL)     AS alive_assets,
       COUNT_IF(r.value:destroyed_on_day::INT IS NOT NULL) AS destroyed_assets
FROM SOC_GAME_SESSION s,
     LATERAL FLATTEN(input => s.json_state:asset_records) r
GROUP BY s.session_id, r.value:owner::STRING;
```

Note the `::INT` before the NULL test — a JSON null is not a SQL NULL
inside a VARIANT, and casting is what makes `COUNT_IF` behave. Returns 2
rows for the new session and **67 rows across the account**, including
sessions written long before this change, because `json_state` was always
being written.

**Deploy gotcha for whoever runs this next:** the app's role (`SYSADMIN`)
does **not** have `CREATE VIEW` on `SOC_HACKATHON_DB.SEA_OF_COLOURS`. The
view has to go through `scripts/deploy_soc_schema.py --schema-only`,
which elevates to `ACCOUNTADMIN`. `soc_schema.sql` is idempotent
(12 × `CREATE TABLE IF NOT EXISTS`, nothing destructive), so that is safe
to re-run.

---

## R2. Corrections to the brief you were given

We got two facts wrong in the original, and one of them shaped your
recommendation. Flagging both so you can re-weigh.

### R2.1 §2 was wrong: hoard and shipped ARE read

The claim "no store class has a read method for them" is false for
`SOC_HOARD_PARCEL` and `SOC_SHIPPED_PARCEL`. Our grep missed it because
the table name is interpolated:

```python
shipped = _by_session("SOC_SHIPPED_PARCEL")   # inside bulk_session_scores
hoard   = _by_session("SOC_HOARD_PARCEL")
```

`bulk_session_scores` is what the season picker scores off, and there is
a documented history of a divergent second copy of the scoring rules
causing wrong standings, so we did **not** touch these. `list_hoard` /
`list_shipped` also read them, but only from `evals/season_metrics.py`,
never from live play.

The other four are genuinely write-only and are gone. Your S2(b) is
therefore two-thirds done, not done.

### R2.2 §3.3's 260ms client floor was real

You wrote "There is no 0.3s network floor to blame… the 300ms client
measurement is ~260ms of Snowpark/connector per-call overhead, and it is
*not* the bottleneck." The first half was right and the second half is
now wrong — it was not the bottleneck *while compile dominated*. Measured
today:

```
path                                        client ms      server total
snowpark  session.sql().collect()  (375KB)      458ms              56ms
connector cursor.execute().fetchall()           380ms              56ms
snowpark  SELECT 1                              287ms              27ms
connector SELECT 1                              288ms              27ms
```

Two conclusions we would like you to sanity-check:

- **~260ms per statement is unavoidable overhead** at the client, and it
  is *not* the Snowpark DataFrame layer — the raw connector cursor is
  identical (288ms vs 287ms). Region is `AWS_EU_WEST_2` and the client is
  in the UK, so this is not transatlantic.
- At **13–16 statements per turn that is a ~3.5–4.2s floor** before any
  server work at all. Statement count is now a first-order cost, which it
  was not when the answer was written.

### R2.3 The write amplification is worse than we told you

We said "a turn hits two" `save_session_full` call sites and
"`load_session` runs three times". Measured over a real 2-day season:
**20 session MERGEs for 6 turns (3.3/turn)** and **62 loads (10/turn)**.

---

## R3. Results — client side

`scripts/bench_store.py --backend snowflake --turns 3`, same board and
seats as the original §3 measurements.

| | before | after |
| --- | --- | --- |
| mean turn | 13.07s | **10.71s** |
| store calls / turn | 21–24 | 13–16 |
| sum of call time ÷ wall | ×1.5–1.7 | ×1.1 |

Representative turn, after:

```
store method                 calls   seconds   ms/call    rows
load_session                     3      3.01      1003       0
save_session                     2      2.54      1270      16
append_log                       3      1.91       638      13
replace_shipped_bundle           2      1.82       908       4
append_replay_frames             1      1.60      1604       6
upsert_policy                    1      1.33      1331       2
replace_hoard_bundle             2      0.92       461       4
append_agent_invocation          1      0.79       792      10
list_log                         1      0.40       397       0
------------------------------------------------------------
WALL                            16     13.49
```

Note the ×1.1 overlap ratio: **the parallel save fan-out is now
essentially serial**, because only two phases remain in the pool.

---

## R4. Results — server side (the honest witness)

One 2-day, 6-turn heuristic season: **77.1s wall, 59.4s server-side.**
Session id `564b3b73070347a88e544bb8e380646c`, seed 77.

Query is `information_schema.query_history` over the run window, grouped
by statement prefix:

| statement | n | total s | avg total | avg compile | avg exec | SQL bytes |
| --- | --- | --- | --- | --- | --- | --- |
| `MERGE SOC_GAME_SESSION` | 20 | **19.7** | 985ms | 254ms | **733ms** | 638 |
| `SELECT … SOC_GAME_SESSION` (load) | 62 | 7.6 | 123ms | 85ms | 36ms | 143 |
| `INSERT SOC_GAME_LOG` | 21 | 7.5 | 357ms | 143ms | 214ms | 289 |
| `INSERT SOC_AGENT_INVOCATION` | 9 | 4.6 | 511ms | 171ms | 336ms | 581 |
| `MERGE SOC_POLICY_QUEUE` | 6 | 4.3 | 717ms | 282ms | 440ms | 461 |
| `DELETE SOC_HOARD_PARCEL` | 20 | 4.3 | 215ms | 100ms | 104ms | 108 |
| `DELETE SOC_SHIPPED_PARCEL` | 20 | 3.9 | 195ms | 107ms | 88ms | 110 |
| `INSERT SOC_REPLAY_FRAME` | 3 | 1.8 | 600ms | 150ms | 440ms | 954 |
| `SELECT … SOC_GAME_LOG` (list_log) | 11 | 1.8 | 164ms | 137ms | 23ms | 172 |
| `SELECT … SOC_DAY_INDEX` | 18 | 1.7 | 94ms | 88ms | 9ms | 202 |
| `SELECT … parcels` | 12 | 0.8 | 67ms | 63ms | 1ms | 84 |
| `DELETE SOC_REPLAY_FRAME` | 3 | 0.6 | 200ms | 106ms | 89ms | 94 |
| `SELECT * SOC_AGENT_INVOCATION` | 2 | 0.6 | 300ms | 172ms | 110ms | 113 |
| `SELECT * SOC_REPLAY_FRAME` | 1 | 0.3 | 300ms | 142ms | 194ms | 104 |
| **TOTAL** | | **59.4** | | | | |

Caveat on comparing with your S1 table: your sample was 4,310 statements
over 3 days of mixed traffic; this is one clean season. Per-statement
averages are comparable, the `n` column is not.

**Before/after on the two statements you named:**

| | SQL bytes | compile | exec | total |
| --- | --- | --- | --- | --- |
| `MERGE SOC_GAME_SESSION` before | 510,850 | 399ms | 706ms | 1105ms |
| `MERGE SOC_GAME_SESSION` after | **638** | **254ms** | 733ms | 985ms |
| `INSERT SOC_REPLAY_FRAME` before | 1,174,369 | 1397ms | 1780ms | 3177ms |
| `INSERT SOC_REPLAY_FRAME` after | **954** | **150ms** | 440ms | **600ms** |

The replay insert is a 5× win. The session MERGE is an 11% win, because
its cost was mostly execution to begin with — that is the crux of R0.

---

## R5. Correctness validation

Against the real season, not a local double:

```
--- frames per day (your S3.2 duplicate-day detector) ---
  day 1: 6 frames,  6 distinct global_idx, range 0..5
  day 2: 13 frames, 13 distinct global_idx, range 6..18
--- variant columns survived ---
  d1 f0 hour=0 cells=ARRAY entities=4 cap=[opening] PRAXIS begins
  d1 f1 hour=1 cells=ARRAY entities=5 cap=p1 deployed probe_p1_1 at (19,13)
--- sequence integrity ---
  50 log rows, 50 distinct seq
  6 agent invocations, 6 distinct seq
```

So: `global_idx` contiguous and unique and continuing correctly across
the day boundary (the server-side assignment in R1.4 is sound), VARIANT
columns intact through `FLATTEN`, `hour` preserved as an int, no
duplicated days, no seq collisions.

Also done:

- **Your S3.1 static gate**, as `tests/test_snowpark_batching.py::
  test_save_session_full_writes_only_what_is_read_back`. It asserts the
  four projections stay off the save path *and* that `save_session` is
  the last call in the phase log, which is the §7 ordering guarantee.
- Full suite green: **1377 passed, 4 skipped**.

**Not done, and we know it matters:**

- **S3.3, the concurrent-seasons soak.** Nothing has been run under
  contention. Everything above is single-threaded.
- **S3.2 wired in as a post-run assertion.** The queries were run by
  hand, not attached to `scripts/soc.py season`.

---

## R6. Where we think the remaining 59.4s is

1. **`MERGE SOC_GAME_SESSION` execution: 19.7s (33%).** 733ms to write
   one row whose VARIANT is 375KB. Compile is already down to 254ms and
   the SQL is 638 constant bytes, so there is nothing left for us to trim
   in the statement itself.
2. **Write amplification: 3.3 saves and 10 loads per turn.** Pure
   application redundancy. Cutting to 1 and 1 would remove roughly
   `19.7 × (2.3/3.3) + 7.6 × (9/10) ≈ 20.6s` of server time plus ~11
   statements × 260ms of client floor per turn. This is the biggest
   single win and needs no Snowflake change — but it is also the change
   most likely to reopen §7, which is why we have not done it blind.
3. **Hoard/shipped: 8.2s, mostly deleting nothing.** Hoards are empty
   early in a season, so 40 DELETEs run for no rows. Finishing S2(b) here
   requires re-pointing `bulk_session_scores`, `list_hoard` and
   `list_shipped` at `json_state` across all three store implementations.
4. **The ~260ms client floor × statement count**, per R2.2.

---

## R7. Questions we would like your judgement on

1. **Is 733ms execution normal for a single-row MERGE carrying a 375KB
   VARIANT on an XS warehouse?** If yes, does this now make
   `SOC_GAME_SESSION` the hybrid-table case you parked in S2(5) — you
   said "do not reach for them until (a)+(b) are measured", and they now
   are.
2. **Is `MERGE` still the right shape for a single known key?** We
   always know whether the row exists. Would `UPDATE` + fallback
   `INSERT`, or `INSERT OVERWRITE`, execute materially cheaper — or is
   the cost simply rewriting the micro-partition holding a 375KB value
   and therefore shape-independent?
3. **Should the blob live in its own table?** `SOC_GAME_SESSION` also
   carries the small scalar columns (`day`, `phase`, `seed`, …) that the
   session list reads constantly. Splitting `json_state` into a
   one-column side table would stop every list query touching 375KB
   partitions. Worth it, or premature?
4. **Is there anything to do about the 260ms client floor**, given the
   raw connector matches Snowpark exactly? Result format, keepalive,
   session reuse, anything — or is that simply the price of a statement
   and the only answer is to issue fewer of them?
5. **Bind size ceiling.** We now send up to ~2MB in a single bound
   string for `PARSE_JSON(?)`. Where does that actually break, and should
   we be staging instead past some threshold?
6. **Given §7, where would you put the caching boundary** for
   `load_session`? Our instinct is a per-request cache keyed on
   `(session_id, phase, day)` invalidated on every write, but the failure
   mode of getting it wrong is the "day 3 repeats three times" bug, which
   is invisible on file and memory backends.

---

## R8. How to reproduce any of the above

```bash
# client-side per-method timings, the numbers in R3
python scripts/bench_store.py --backend snowflake --turns 3
python scripts/bench_store.py --backend file      --turns 3   # 0.08s/turn control

# the season behind R4 and R5
PYTHONPATH=. SOC_BACKEND=snowflake python scripts/soc.py season \
    --p1 red_harvest --p2 red_harvest --backend snowflake \
    --days 2 --seed 77 --name PERF_FIX_TEST

# engine's own per-phase save timings
SOC_PERF=1 …same…
```

The server-side table in R4 comes from
`information_schema.query_history` grouped by a 44-char prefix of
`query_text`, filtered to DML/SELECT and excluding the meta-queries. The
one column to watch when checking a future change actually landed is
**`length(query_text)`** — if it goes back to five digits, something is
inlining again.

---

## R9. The write amplification, traced to its call sites

Added after R1–R8. We instrumented the store on the **file** backend
(the call pattern is backend-independent, so this costs a second and no
credits) and recorded the caller of every `save_session` and
`load_session`. It is the same four calls every turn, in both the
planning and the orbit phase:

```
=== turn 1 (p1) -> phase now planning ===
  1x load_session   _hydrate_session <- get_view              <- run_agent_turn
  1x load_session   _hydrate_session <- submit_policy         <- run_agent_turn
  1x save_session   save_session_full <- submit_policy        <- run_agent_turn
  1x load_session   _hydrate_session <- save_agent_rationale  <- run_agent_turn
  1x save_session   save_session_full <- save_agent_rationale <- run_agent_turn
  1x load_session   _hydrate_session <- get_session_status    <- (runner poll)
```

**`save_agent_rationale` is the finding.** Writing the agent's rationale
— a *string*, destined for `SOC_AGENT_INVOCATION` — currently does a
full `_hydrate_session` (reads the 375KB blob) followed by a full
`save_session_full` (MERGEs the 375KB blob back). It is
`engine.py:2575` and `engine.py:2577`.

That single call site is **half of the saves and a quarter of the loads
in every turn**. On the R4 numbers that is roughly `985ms + 123ms ≈ 1.1s
of server time per turn`, plus two statements against the ~260ms client
floor, spent persisting the entire game state in order to record a piece
of text that does not live in the game state.

It also looks like the safest thing on the whole list to remove, because
it is not part of the turn's control flow: nothing downstream reads
`json_state` expecting the rationale to be in it. If that holds, this is
a §7-free change — unlike collapsing the `submit_policy` save, which is
the one that genuinely guards the ordering.

The other three are more considered:

| call site | why it loads/saves | can it go? |
| --- | --- | --- |
| `get_view` | reads state to build the agent's percept | needs the read; could share it with the next one |
| `submit_policy` / `submit_orbit_actions` | the real turn write | **keep** — this is the §7-critical save |
| `save_agent_rationale` | shouldn't touch `json_state` at all | **yes, and first** |
| `get_session_status` | the runner's own phase poll | could reuse the submit's post-state |

Suggested order: kill the `save_agent_rationale` round trip, then merge
the `get_view` and `submit_policy` reads behind a per-request cache, and
leave the `submit_policy` save exactly where it is.

---

# Reply 2: your R0 reading is correct, and I measured the rest

**To:** the app side.
**From:** the engineer/agent who wrote the Solution.
**Date:** 2026-09-01.
**Status:** answers below are measured on your account, not reasoned.
Probe left at `scripts/probe_latency.py`; it creates and drops its own
`SOC_PROBE_DB` so it needs no privileges in `SEA_OF_COLOURS`.

---

## T0. Verdict on R0

**You are right and I was wrong about the weighting.** I wrote "roughly
40% of a turn is Snowflake parsing string literals". That was true of the
*sample*, not of the *turn*: the sample was 4,310 statements of mixed
traffic in which `INSERT SOC_REPLAY_FRAME` (compile 1397ms) and the two
1120-row `VALUES` MERGEs carried the compile total, and those fire far
less often per turn than `MERGE SOC_GAME_SESSION`. Weighting compile by
`n`-per-turn rather than by per-statement average would have predicted
~18%, which is what you got. That is the error; the fix itself was sound
and the replay 5× win is where the compile money actually was.

**The more important correction is that R2.2 is now the headline, not a
footnote.** I dismissed the 260ms client floor as "not the bottleneck".
It was not the bottleneck *then*. It is now co-dominant, and I have
decomposed it — see T1.1. It is also not what either of us guessed.

---

## T1. Measurements

All from `scripts/probe_latency.py` against
`SOC_HACKATHON_WH` (XS), `USE_CACHED_RESULT = FALSE`, median of 3–8 runs.

### T1.1 The 260ms floor is Snowflake-side wait, not client work

```
TCP connect to the account host      median   12.5 ms   (~1 RTT — you are next to eu-west-2)
TLS handshake                        median  494.9 ms   (once per connection, not per statement)
SELECT 1, warm, same connection      median  311.9 ms   (server total_elapsed_time: 27 ms)
```

`cProfile` over 5 × `SELECT 1`, sorted cumulative — the entire 1.607s:

```
5    1.606  cursor.py:881(execute)
5    1.602  network.py:795(fetch)
5    1.589  urllib3/connectionpool.py:592(urlopen)
5    1.581  http/client.py:291(_read_status)
11   1.567  urllib3/util/wait.py:113(wait_for_read)      <- 97.5% of wall
11   1.567  urllib3/util/wait.py:57(poll_wait_for_socket)
```

**Conclusions, which answer R7.4:**

- The client burns essentially **zero CPU**. 97.5% of the time is
  `poll_wait_for_socket` — blocked waiting for the HTTP response. So it
  is neither the Snowpark DataFrame layer (you already proved that) nor
  connector overhead, nor serialisation.
- It is **not the wire either**: 12.5ms RTT, connection reused (11
  `recv_into` for 5 requests, so no re-handshake).
- So the ~280ms gap between `total_elapsed_time = 27ms` and
  `client = 312ms` is **Snowflake's own per-request handling outside the
  measured query** — cloud-services dispatch, session/auth validation,
  result packaging. `total_elapsed_time` does not include it, which is
  why your R4 table (59.4s server) and R3 (10.71s × 6 turns ≈ 64s +
  overhead) reconcile only loosely.

**There is nothing to tune here.** No result format, keepalive, or
session setting reaches it. It is ~300ms per statement, and
**statement count is the only lever.** That promotes S2(d) from "the
option for the tournament" to the primary recommendation.

> **CORRECTION (added after §V6).** An earlier draft of this paragraph said
> "~300ms per statement, **full stop**". That absolute is wrong. The floor
> holds for standard tables and for hybrid-table queries that miss the
> operational fast path — including the 375KB `json_state` read, still
> ~540ms. But a **hybrid point lookup returning under 100KB comes back in
> ~90ms**, measured on `SOC_AGENT_MEMORY` (`tables-hybrid-operational-query-performance`).
> So the floor is a property of the query shape, not of the connection, and
> making a read a small point lookup is a second lever alongside reducing
> statement count.

### T1.2 MERGE / UPDATE / hybrid shape matrix

`P_WIDE` = CTAS copy of `SOC_GAME_SESSION` (32 rows, 78KB/row
compressed). Payload is a synthetic 375KB JSON blob, bound via
`PARSE_JSON(?)`. `P_SPLIT` = two-column blob-only table. `P_HYBRID` =
hybrid table, `session_id STRING PRIMARY KEY`.

| shape | client ms | total | compile | exec | bytes scanned |
| --- | --- | --- | --- | --- | --- |
| `MERGE` P_WIDE, 375KB | 975 | 667 | 236 | 386 | 2,487,296 |
| `MERGE` P_WIDE, ~0KB payload | 856 | 527 | 193 | 334 | 2,484,736 |
| `MERGE` 1-row table, 375KB | 838 | 556 | 228 | 328 | 4,096 |
| `MERGE` P_SPLIT (blob-only), 375KB | 1326 | **1039** | 194 | **845** | 4,140,544 |
| `UPDATE` P_WIDE, 375KB | 660 | **388** | 152 | **238** | 2,487,296 |
| `SELECT json_state` (read blob back) | 438 | 129 | 68 | 70 | 2,520,064 |
| `SELECT` scalars from P_WIDE | 388 | **60** | 48 | 11 | 2,483,200 |
| `SELECT` scalars from narrow table | 372 | 102 | 101 | 1 | 0 |
| **`MERGE` P_HYBRID, 375KB** | 372 | **115** | 75 | **40** | 0 |
| `UPDATE` P_HYBRID, 375KB | 478 | 138 | 112 | 26 | 0 |
| `SELECT` P_HYBRID by PK (blob back) | 463 | 113 | 33 | 82 | 0 |

My `MERGE P_WIDE` total is 667ms against your measured 985ms for the real
statement (yours has 8 binds and more columns). Absolute numbers differ;
every ratio below is internally consistent within the one run, so the
directional conclusions hold.

---

## T2. Answers to R7

### R7.1 — Is 733ms exec normal for a 1-row MERGE with a 375KB VARIANT? No.

Same shape, same warehouse, same table size: **667ms total / 386ms exec**.
Yours is ~2× that, so there is some headroom in your statement, but the
class of cost is confirmed and it is *not* the payload:

- Dropping the payload from 375KB to ~0KB moves total from 667ms to
  527ms. **The blob costs ~140ms; the other ~527ms is the MERGE itself.**
- The same MERGE against a **1-row** table costs 556ms with 4KB scanned
  versus 2.49MB. **Scanning the table is ~110ms.** Not the driver either.

**So yes, `SOC_GAME_SESSION` is now the hybrid-table case I parked in
S2(5), and the margin is not marginal:** 667ms → **115ms total, 386ms →
40ms exec**. That is a 5.8× cut on the statement that is 33% of your
server time, and it is a `CREATE HYBRID TABLE` plus a backfill, with the
statement text unchanged.

Two things I want to be straight about rather than sell:

- **It does not touch the 300ms client floor.** `MERGE P_HYBRID` is
  372ms client for 115ms server. Hybrid tables fix server time; only
  fewer statements fix the rest.
- **Hybrid tables have a documented fast path that your blob will miss.**
  Per `hybrid-tables-operational-query-performance`, the operational fast
  path requires a result under 100KB; a 375KB `json_state` read is over
  it. Measured anyway: 113ms vs 129ms on FDN. Reads are a wash. The win
  is entirely on the write.

The real prize is that hybrid tables give **row-level locking and
read-your-writes**, which retires §7 as a *class* rather than as a rule
you have to keep obeying. Given R2.3 (3.3 saves/turn) and R7.6 (you want
to cache reads), removing the race by construction is worth more than the
552ms.

### R7.2 — Is MERGE the right shape? On FDN, no. On hybrid, it stops mattering.

`UPDATE` P_WIDE = **388ms** vs `MERGE` P_WIDE = **667ms**. Same row, same
375KB. **MERGE costs 280ms (42%) more than UPDATE**, and both write the
same bytes.

Your hypothesis — "the cost is simply rewriting the micro-partition
holding a 375KB value and therefore shape-independent" — is **refuted**.
If it were the partition rewrite, the 0KB MERGE would be cheap (it is
527ms) and the 375KB UPDATE would be expensive (it is 388ms). The cost is
MERGE's own machinery: it plans an outer join and a two-sided match even
when the source is a one-row `SELECT ? , PARSE_JSON(?)`.

So `UPDATE`, then `INSERT` only when `rowcount == 0`, is worth ~280ms per
save on FDN. **But do not build it:** on the hybrid table `MERGE` is
115ms and `UPDATE` is 138ms — MERGE is *faster*, and the second statement
in an UPDATE-then-INSERT would cost 300ms of client floor on the miss
path. Go hybrid and keep MERGE. Only take the UPDATE rewrite if you
decide against hybrid.

### R7.3 — Should the blob live in its own table? No — and I measured it backwards from your intuition.

Both halves of the premise fail.

- **The list query does not touch the blob today.** `SELECT session_id,
  day, phase` from the wide 32-row table with 375KB blobs: **60ms total,
  11ms execution.** FDN is columnar; `json_state` is a separate column
  chunk and is never read. The same query against a narrow table was
  *slower* (102ms), all of it compile noise. There is nothing to save.
- **Splitting made the write 56% worse.** `MERGE` into the blob-only
  `P_SPLIT` was **1039ms / 845ms exec** versus 667ms / 386ms on the wide
  table, scanning 4.1MB versus 2.5MB. Every partition in a blob-only
  table is blob, so the write has nowhere cheap to land.

Premature, and in the wrong direction. Drop it.

### R7.4 — Anything to do about the 260ms floor? No. Issue fewer statements.

See T1.1. It is Snowflake-side request handling, invisible to
`total_elapsed_time`, unreachable from the client. Your figure of
"13–16 statements = a 3.5–4.2s floor" is correct and is now the thing to
design against.

Concretely: after the write-amplification fix a turn should be ~5
statements ≈ **1.5s of floor**, which is your 2s target with nothing left
over. Getting under it requires collapsing the turn into **one** call —
S2(d), the stored procedure. One `CALL` = one 300ms tax, and every
statement inside it pays zero.

### R7.5 — Bind size ceiling: you are over the documented limit. Chunk back down.

The 1MB limit did not go away when you switched to binds. From
*Limits on Query Text Size*:

> Snowflake recommends you limit the size of query text … to 1 MB per
> statement. … **This limit also applies when binding values in client
> applications** that use Snowflake connectors and drivers.

So the constraint that justified the 50-frame cap still applies at ~the
same threshold; what changed is that you are now measuring the *bind*
against it instead of the SQL text. At ~2MB per chunk you are ~2× over.

What actually breaks:

- It is a *recommendation*, not a hard error — hence "it works". The
  documented consequence is that **Snowflake truncates statements over
  1MB before persisting them to the metadata store**, so they cannot be
  rerun or retried.
- That silently breaks **your own R8 diagnostic**: `query_history.
  query_text` and `bind_values` get truncated, so `length(query_text)`
  stops being a reliable witness on exactly the biggest statements.
- The hard ceiling underneath is the LOB limit — a single VARCHAR value
  is capped at 16,777,216 bytes on this account (verified). You are far
  from it; the 1MB guidance will bite first.

**Recommendation:** size chunks by *bytes*, not frame count — target
≤800KB of bound JSON per statement, which on your data is somewhere
between 20 and 60 frames. Above ~1MB per batch, stage-and-`COPY` is the
documented path. Note this trades statement count (300ms each) against
chunk size, so measure rather than minimising either blindly.

### R7.6 — Where to put the caching boundary for `load_session`

Your instinct is close but the key is wrong. `(session_id, phase, day)`
is derived *from the state you are caching*, so a stale entry has a stale
key and validates itself as fresh — that is precisely the "day 3 repeats
three times" shape.

Two options, and which one you pick depends on R7.1.

**If you adopt hybrid tables (recommended):** hybrid tables give
read-your-writes within a session, so a **write-through cache** is safe
by construction. Key on `session_id` alone. On `save_session`, store the
object you just wrote. On `load_session`, return it if present. There is
no interleave for a stale entry to expose, because the only writer that
can invalidate you is a *different* session, and that is the multiplayer
case below.

**If you stay on FDN:** make the cache write-through and **scope it to a
single turn**, discarded at the turn boundary. Explicitly:

- `save_session` populates the cache with what it wrote. This is strictly
  *safer* than re-reading, because the §7 bug is a stale read after this
  process's own write — the cache holds the post-write value by
  definition, while the re-read is the thing that can observe a
  pre-resolution snapshot.
- `load_session` reads through the cache; the cache is cleared when the
  turn ends.
- Do **not** add a cheap validation read. `SELECT state_version` is 60ms
  server but still 300ms client — the same price as the full read
  (`SELECT json_state` is 438ms client). Validation buys nothing.
- Add a monotonic `state_version INT` to `json_state` anyway, and assert
  on write that the version you are overwriting is the one you read. That
  turns a lost update into a loud failure instead of a repeated day.
- **Bypass the cache when more than one process can write the session** —
  live multiplayer. Since backend is per game (§7), make it per store
  instance and off by default for web-served sessions.

---

## T3. Revised plan, ranked by measured saving

Per turn, using your R4 season (6 turns) and a 300ms client tax per
statement.

| # | Change | Server saved | Statements saved | Risk |
| --- | --- | --- | --- | --- |
| 1 | **Write amplification: 3.3 saves → 1, 10 loads → 1** (your R6.2) | ~20.6s/season | ~11/turn = **3.3s/turn** | Reopens §7 — mitigate with T2/R7.6 write-through cache |
| 2 | **`SOC_GAME_SESSION` → hybrid table** | 667→115ms per write | 0 | Low; retires §7 as a class |
| 3 | **Stop DELETEing empty hoard/shipped** (your R6.3) | ~8.2s/season | up to 40/season | Low — guard client-side, no scoring change |
| 4 | **Turn → one stored procedure call** (S2(d)) | — | ~4/turn = **1.2s/turn** | Medium; but §7 becomes sequential-by-construction |
| 5 | Chunk replay binds back under ~800KB | slight cost | slight cost | Restores R8 diagnostics |
| ~~6~~ | ~~Split `json_state` into its own table~~ | **−453ms** (worse) | 0 | Do not do this |
| ~~7~~ | ~~MERGE → UPDATE+INSERT~~ | 280ms on FDN, **negative on hybrid** | −1 on miss | Superseded by #2 |

(1) and (2) together should take you from 10.71s to roughly 2–3s. (4) is
what gets you under 2s and is the only thing that scales to the parallel
tournament, because it is the only change that reduces the 300ms tax
rather than working around it.

**Do (1) and (2) in that order but ship them together**, because (2) is
what makes (1)'s cache safe.

## T4. Two corrections to my own S2

- **S2(a) was oversold at the turn level.** Right fix, right mechanism,
  but I weighted compile by per-statement average instead of by
  frequency-per-turn. 18% was the honest prediction.
- **S2(5) said "do not reach for hybrid tables until (a)+(b) are
  measured; they would be solving a problem that no longer exists."** The
  problem does exist, they do solve it, and they also solve §7. Promote
  to step 2.

Your R2.1 correction is accepted: `bulk_session_scores` reads hoard and
shipped through an interpolated table name, so S2(b) is two-thirds done
and the remaining third is a scoring-path change, not a storage change. I
would not finish it — item 3 above gets most of the time for none of the
risk.

**Still outstanding from S3, and it is the one thing I would not skip:**
the concurrent-seasons soak (S3.3). Everything measured on both sides is
single-threaded, and item 1 is a caching change to the exact code path
that produced the original bug. Wire S3.2's duplicate-day queries into
`scripts/soc.py season` as a post-run assertion first, then run 5+
concurrent seasons. That combination is what makes items 1 and 2
releasable.

---

# Result: v1.42 app-side fix + hybrid table, measured

**Date:** 2026-09-01. **Status:** implemented, swapped, validated on the
real account.

## U1. Headline

| | mean turn | source |
| --- | --- | --- |
| v1.41 baseline | 10.71s | R3 |
| + v1.42 app-side (rationale save, empty-parcel skip) | **10.59s** | `bench_store.py --turns 3` |
| + `SOC_GAME_SESSION` as a HYBRID TABLE | **7.27s** | same command |

**10.71s → 7.27s, −32%.** The app-side work was worth ~1%; the hybrid
table was worth the other 31%.

`save_session`, the hot write, went from **1666ms / 1108ms per call to
420ms / 435ms**.

## U2. Isolated A/B, real statement, real payload

`scripts/probe_hybrid.py` — the exact SQL from
`snowpark_store.save_session`, the largest real `json_state` in the
account (1.19 MB), interleaved variants, median of 6, `USE_CACHED_RESULT
= FALSE`.

| variant | client | server | compile | exec |
| --- | --- | --- | --- | --- |
| `MERGE` FDN *(app's statement)* | 1176ms | 848ms | 234ms | 614ms |
| **`MERGE` HYBRID** | **577ms** | **243ms** | 116ms | 124ms |
| `UPDATE` FDN | 996ms | 684ms | 168ms | 532ms |
| `UPDATE` HYBRID | 537ms | 199ms | 87ms | 112ms |
| `SELECT` FDN inlined *(app's statement)* | 780ms | 206ms | 108ms | 98ms |
| `SELECT` HYBRID inlined | 710ms | 156ms | 42ms | 120ms |

Notes:

- **Writes are where the win is** (848→243ms server). Reads barely move
  (206→156ms), because a >100KB result misses the hybrid operational fast
  path documented in `hybrid-tables-operational-query-performance`.
- Docs recommend `UPDATE` over `MERGE` on hybrid tables for small row
  counts. Measured, it is only ~44ms better and would need an
  `INSERT`-on-miss second statement worth ~300ms of client floor.
  **Keeping `MERGE`.**
- **The migration required zero code changes.** The bound `MERGE`,
  `SELECT` and `DELETE` statements all work unaltered.

## U3. Migration performed

```sql
CREATE OR REPLACE HYBRID TABLE SOC_GAME_SESSION_HT (... PRIMARY KEY (SESSION_ID));
INSERT INTO SOC_GAME_SESSION_HT SELECT ... FROM SOC_GAME_SESSION;   -- 40 rows
ALTER TABLE SOC_GAME_SESSION    RENAME TO SOC_GAME_SESSION_FDN_BAK;
ALTER TABLE SOC_GAME_SESSION_HT RENAME TO SOC_GAME_SESSION;
```

- Needs `ACCOUNTADMIN`; `SYSADMIN` has no `CREATE TABLE` on
  `SEA_OF_COLOURS` (same trap as the view in R1.6).
- The 1.19 MB blob backfilled fine — no row-size limit hit.
- `SOC_LEADERBOARD` and the other view survived the rename.
- **Revert:** rename back. `SOC_GAME_SESSION_FDN_BAK` still holds all 40
  pre-swap rows.
- `snowflake/soc_schema.sql` now under-describes reality — it declares
  `SOC_GAME_SESSION` as a standard table. It is `CREATE TABLE IF NOT
  EXISTS`, so re-running the deploy is a no-op and will not clobber the
  hybrid table, but the file should be updated.

## U4. Validation

- **1377 tests pass**, plus 2 new regression tests
  (`test_repeat_empty_parcel_bundle_skips_the_delete`,
  `test_save_agent_rationale_writes_only_the_authoritative_row`). The 2
  failures in `test_binding_registry.py` are pre-existing and reproduce
  with these changes reverted — they concern the untracked
  `emp_harvest_test` harness.
- **S3.2 wired in**: `scripts/soc.py season` now runs
  `_season_integrity()` after every season and exits non-zero on failure.
- **S3.3 done, twice**: 5 concurrent seasons pre-hybrid and 5 post-hybrid.
  All 10 clean, verified in SQL independently of the in-app checker —
  contiguous `global_idx` across the day boundary, no duplicate `seq`, no
  repeated log lines.
- **The riskiest edit is confirmed correct by data**: for all 5 hybrid
  seasons, `array_size(json_state:log)` exactly equals `count(*)` from
  `SOC_GAME_LOG` (62=62, 61=61, 60=60, 62=62, 62=62). Replacing
  `save_session_full` with `store.save_session` in `save_agent_rationale`
  preserved every log line, including the rationales, which is what
  `get_session_status`'s `log_tail` depends on. Confirmed live through the
  web API too.
- Season wall time under 5-way concurrency: **55–63s**, against 77.1s for
  a single season pre-fix.

## U5. Hybrid-table consistency — flagged, then measured, and it is a non-issue

Hybrid tables use a **session-based consistency model**: reads in the same
session see that session's writes, but changes made *outside* the session
can be up to **100ms stale** (`tables-hybrid-limitations`, "Consistency").
On FDN a committed MERGE was immediately visible, so on paper this is a
second mechanism for the §7 stale-read bug that "blob written last" does
not protect against.

**Measured with `scripts/probe_staleness.py`** — two independent Snowpark
sessions, A writes `json_state`, B reads it back as fast as the client can
issue the request, 40 rounds each:

```
HYBRID, 375KB payload, default consistency   stale 0/40  min gap write->read 275.0ms
HYBRID,   4KB payload, default consistency   stale 0/40  min gap write->read 269.0ms
HYBRID, 375KB, READ_LATEST_WRITES=true       stale 0/40  min gap write->read 276.9ms
FDN (pre-swap backup), 375KB                 stale 0/40  min gap write->read 392.8ms
```

**Zero staleness in 160 attempts, and the reason is structural:** the
minimum gap between the write returning and the read being issued is
**269ms**, because the client cannot issue the next request until the
write's HTTP response arrives and that costs ~275ms of Snowflake request
handling (§T1.1). The staleness window closes ~2.7x over before a
sequential client can produce a read. The per-request floor that makes
this app slow is the same thing that makes this race unreachable.

Scope of the exposure, corrected:

- **Seasons in parallel: no exposure.** Separate processes, separate
  sessions, disjoint `session_id` rows. No cross-session read of the same
  key occurs. An earlier draft of this section implied otherwise; it was
  wrong.
- **The repeated-day bug: no exposure.** That bug requires the season
  runner — the component that decides whether to re-resolve — to read
  stale. The runner is a single Snowpark session and therefore has
  read-your-writes.
- **Web UI polling a game a runner is writing:** the only real case.
  Worst case `/status` shows state one turn behind for <100ms and
  self-corrects on the next poll. Cosmetic.
- **`SOC_SF_CONNECTIONS>1`:** the case to watch, but weaker than first
  stated — the pool is LIFO, so a read straight after a write tends to
  borrow the same connection back.

Limits of the measurement: 160 samples bounds the rate at roughly
<1-in-160 under these conditions, not zero; and the warehouse was lightly
loaded, so heavy load could push propagation past 100ms (though the client
floor would grow with it).

**Recommendation:** no action now. If the pool is ever enabled, set
`READ_LATEST_WRITES = true` — measured cost 276.9ms vs 286.6ms, inside
noise — and say so in the pool's docstring.

## U6. Where the remaining 7.27s is

Representative turn, 11 calls, 5.61s:

```
load_session              3     2.30s    768ms/call   <- now the biggest item
append_log                2     1.32s    658ms
save_session              2     0.87s    435ms        <- was 1108ms
append_agent_invocation   1     0.69s    691ms
list_log                  1     0.39s    387ms
replace_hoard_bundle      1     0.00s      0ms        <- v1.42 skip
replace_shipped_bundle    1     0.00s      0ms        <- v1.42 skip
```

**11 statements × ~300ms of Snowflake-side request handling = 3.3s, i.e.
59% of the turn is now the per-request floor** (§T1.1), not query work.

So the ordering of what is left is unambiguous:

1. **Buffer the turn and flush once.** Three `load_session` calls re-read
   state the process already holds (2.30s for nothing) and two
   `save_session` calls write the same blob twice. A `BufferedSocStore`
   decorator — write-through read cache, coalesced appends, one ordered
   flush — takes 11–14 statements to ~1 and removes ~3s/turn. It also
   makes the §7 ordering a single line in `flush()` instead of a
   convention spread over 8 call sites.
2. **Bind `load_session`'s `session_id`.** It still inlines via `_quote`,
   so the SQL text varies per session and the plan cannot cache — 42ms of
   its compile is avoidable. Small, free.
3. Only then is a stored procedure worth considering.

---

# Result: v1.43 buffered store — target met

**Date:** 2026-09-01. **Status:** implemented, validated, and **on by
default since v1.43** — `SOC_BUFFERED_STORE=0` is the way back.

## V1. The ledger

| step | mean turn | vs baseline |
| --- | --- | --- |
| v1.41 baseline (R3) | 10.71s | — |
| v1.42 app-side (rationale save, empty-parcel skip) | 10.59s | −1% |
| `SOC_GAME_SESSION` hybrid | 7.27s | −32% |
| + 4 more hot tables hybrid | 5.57s | −48% |
| **+ buffered store** | **1.19–1.40s** | **−87–89%** |

Season, 3 days / 10 turns, same command: **127.1s → 30.3s**. Five of those
concurrently: **46s wall total**.

File backend is 0.07s/turn, so Snowflake went from ~150x local to **~18x**.

## V2. Why the two obvious shortcuts do not work

Both measured, both negative, both recorded so nobody repeats them:

- **Multi-statement requests are a wash.** Warm, 5 rounds: n=1 multi 579ms
  vs separate 641ms; n=3 multi 2124ms vs 1794ms; n=6 multi 3924ms vs 4105ms.
  Snowflake runs the batch sequentially and the driver polls per result set,
  so the per-statement cost survives. This is why the design coalesces
  *redundant* statements rather than packing them into one request.
- **Warehouse size is irrelevant** (§S1) and remains so.

## V3. What the buffered store does

`sea_of_colours/snowpark/buffered_store.py`, wrapping any `SocStore`.

Two durability tiers, because the consequences of loss differ:

| tier | contents | flushed by |
| --- | --- | --- |
| per **turn** | `json_state`, policy queue, parcel bundles | `flush_turn()`, called from `engine.save_session_full` |
| per **day** | `SOC_GAME_LOG`, `SOC_REPLAY_FRAME`, `SOC_AGENT_INVOCATION` | `flush()` on day rollover, season end, or `atexit` |

Game state is durable every turn, so a crash can never corrupt or rewind a
game. A hard crash mid-day costs at most one day of LOG text and replay
animation.

What it eliminates per turn: `load_session` 3 → 0 (write-through cache),
`save_session` 2 → 1 (coalesced), `append_log` 2–3 → deferred,
frames/invocations → deferred. A turn is now ~2 statements plus an amortised
day flush.

**Buffer-aware reads, so deferral stays invisible:**

- `list_log` merges pending entries, synthesising `seq` as
  `max(durable seq) + n` — exactly what the INSERT's folded `MAX(seq)+1`
  assigns, so displayed numbers match what lands. Verified by test.
- `day_index` adds pending frame counts, leaving `first/last_global_idx`
  null because they are assigned server-side and genuinely unknown until
  flush. `get_session_status` only sums `frame_count`.
- Everything else falls through `__getattr__`, which **flushes then
  delegates** — so any store method added later is correct by default and
  merely slow.

**The §7 guarantee is now stronger, not weaker.** "Session row last" used to
be a convention spread across 8 `save_session_full` call sites. It is now one
line at the bottom of `_emit()`, in one place, pinned by
`test_session_row_is_written_last_on_every_flush`.

## V4. Validation

- **9 new tests** in `tests/test_buffered_store.py` covering the ordering
  guarantee, the two tiers, cache hits, day-rollover flush, season-end flush,
  `list_log` seq prediction, `day_index` merging, `__getattr__` safety, and a
  multi-day multi-session no-loss run. All pass.
- **1436 pass** overall. Four failures are pre-existing and reproduce with
  every change reverted: two in `test_binding_registry.py` (the untracked
  `emp_harvest_test` harness) and two in `test_battles.py` (concurrent work
  in `sea_of_colours/evals/battles/`).
- **5 concurrent 3-day buffered seasons, verified in SQL** independently of
  the in-app checker:

  | | frames | uniq idx | span | log rows | seqs | dup lines | log in blob | invocations |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | BSOAK_1 | 45 | 45 | 45 | 105 | 105 | 0 | 105 | 10 |
  | BSOAK_2 | 48 | 48 | 48 | 105 | 105 | 0 | 105 | 10 |
  | BSOAK_3 | 47 | 47 | 47 | 103 | 103 | 0 | 103 | 10 |
  | BSOAK_4 | 38 | 38 | 38 | 95 | 95 | 0 | 95 | 10 |
  | BSOAK_5 | 38 | 38 | 38 | 91 | 91 | 0 | 91 | 10 |

  `frames = uniq = span` means no gaps and no duplicates. `log rows = seqs =
  log in blob` means nothing buffered was lost and the blob agrees with the
  durable table. `invocations = 10 = turn count` in all five.
- **Live UI verified** on a buffered season: day 4 `season_complete`,
  `replay_windows` 45 matching the 45 rows in SQL, log_tail populated,
  5 agent rationales visible.
- **File backend unaffected** — buffering wraps only the Snowflake store;
  a file season still runs in 0.9s with integrity clean.

## V5. Operational notes

- **On by default**, flipped after a full seven-day season confirmed the
  gain end to end rather than per statement: 97.9s → 62.2s heuristic,
  254.5s → 205.3s with a V12 seat. `SOC_BUFFERED_STORE=0` disables it, and
  is the first thing to try if a Snowflake season looks short of history.
- **The read cache is scoped to sessions this process writes**, which is
  §T2's "bypass the cache when more than one process can write the
  session" honoured. It has no expiry, so serving every read would have
  pinned a spectating server to the day it first saw — a real hazard the
  moment buffering became the default, since `soc season` in one terminal
  and a server in another is the ordinary way to work here. Pinned by
  `test_a_session_we_only_read_is_never_answered_from_a_stale_cache`.
  Two processes *writing* one session is still unsafe; that wants §V6's
  `state_version`, not a cache policy.
- **A restart during a table rename degrades the server to file-only.** The
  `multi` store is documented to degrade rather than fail, and it caches
  that decision. Observed live: a server started mid-rename served 19
  file sessions and reported "session not found" for every Snowflake game.
  Fix is a restart (`python run_web.py --port 8000 --replace --no-reload`);
  no data is affected. Worth doing renames with the server down.
- **Five tables are now hybrid**: `SOC_GAME_SESSION`, `SOC_GAME_LOG`,
  `SOC_REPLAY_FRAME`, `SOC_POLICY_QUEUE`, `SOC_AGENT_INVOCATION`. Each has a
  `*_FDN_BAK` standard-table backup with the pre-swap rows. Revert is two
  renames per table. `snowflake/soc_schema.sql` still declares all five as
  standard tables and should be updated.
- Hybrid PKs are now **enforced**. A duplicate `(session_id, day, seq)` will
  raise instead of silently duplicating — a silent corruption converted into
  a loud failure, which is what §7 wants.

## V6. What is left, if anyone wants more

A turn is ~2 statements plus the amortised day flush; at a ~300ms floor that
is close to the limit of a client-side design. Remaining ideas, in order of
value:

1. `list_log` still costs a durable read per session (~330ms). It is cached
   per session but invalidated on every flush; caching it across flushes by
   applying the flushed entries to the cache would remove it.
2. Bind `load_session`'s `session_id` — still inlined via `_quote`, so the
   plan cannot cache.
3. A stored procedure would collapse the remaining round trips, but at
   ~1.2s/turn the return no longer justifies moving `engine.py` server-side.

## V7. CORRECTION to an earlier draft of this section

An earlier version of §V7 claimed `frontier.py` had no in-process cache and
that `enemy_probe_disk_history` was therefore dead on the file backend. **That
was wrong.** `frontier.py:86` declares `_ENEMY_LANDINGS`, and both
`record_enemy_landings` (:114) and `enemy_landings` (:143) hydrate on a cold
cache and then accumulate in process — the same pattern as `hazard_memory.py`.
The error came from grepping for `_CACHE|_IN_MEMORY|_MEM`, matching only
`_MEM_KIND`, and then reading `_hydrate_enemy` in isolation.

All four memory kinds work in-process on every backend. The only difference is
resuming a session in a **new process**: Snowflake rehydrates, the file backend
starts cold. That is by design.

Also corrected: the `except Exception: pass` around agent-memory writes is
**intentional**, not a latent bug. The agent is sometimes fired on a human turn
with no store or session, and the quiet skip is what makes that work.

The audit itself, and the handover for whoever picks this up, is in
`docs/SNOWFLAKE_PERF_HANDOVER.md` — including the one genuinely actionable
finding (every orbit turn in an LLM season runs on a heuristic while
`fallback_turns` reports 0).

## V7. Audit: did any of this affect the agents or the game?

Asked directly, so checked directly rather than assumed.

**Agent memory (`SOC_AGENT_MEMORY`) — unaffected, verified end to end.**
It is the one table reached by raw SQL *outside* the store protocol (the
tabula_v12 harness, via `frontier.py`, `hazard_memory.py` and `_v7/memory.py`).
It was **not** migrated to hybrid and the buffered store never sees it.

There was one real hazard: those writers call
`backend.snowpark_session_for(store)`, which is `getattr(store, "session",
None)`, and skip the write when it returns `None` — the guard that stops a
memory-backed game persisting into the account. Wrapping the store could have
returned `None` and **silently** stopped agent memory persisting: no error,
just an agent that forgets. `BufferedSocStore.__getattr__` passes
non-callables straight through, so both halves still hold, confirmed by
experiment and pinned by
`test_snowpark_session_for_still_resolves_through_the_wrapper`.

Round-trip proof through the buffered store: `save_entry(...)` →
`clear_in_memory_store()` (forcing the read to hit Snowflake) →
`read_recent(...)` returned the entry with correct content.

**Agent invocations (`SOC_AGENT_INVOCATION`) — deferred, not lost.** The
buffer holds them to the day boundary, but every reader
(`list_agent_invocations`, behind `/agent-log` and `/agent-cards`) goes through
`__getattr__` and flushes first. All ten soak seasons show
`invocations = turn count` exactly.

**Agent rationales — preserved.** The v1.42 change from `save_session_full` to
`store.save_session` was the riskiest edit. In all ten seasons
`array_size(json_state:log)` equals `count(*)` from `SOC_GAME_LOG`, and the
live UI shows the `[RED_HARVEST]` rationale lines in `log_tail`.

**Game mechanics — untouched.** No simulation code was modified. The only
engine edit outside `save_agent_rationale` is one `flush_turn()` hook at the
end of `save_session_full`. Scores, replay frames and the policy queue are
structurally consistent in every season checked.

### Residual risks, stated plainly

1. **No byte-level proof of simulation equivalence is possible**, because
   `--seed` does not reproduce a season (see the note above §U1 — three runs
   of identical code gave 344/2, 694/344 and 0/0). Verification is by
   structural invariant, not equality.
2. **Hybrid primary keys are enforced.** A race that previously duplicated a
   `(session_id, day, seq)` row silently will now raise. Better — but it is a
   behaviour change, and a season could fail loudly where it used to corrupt
   quietly.
3. **Per-day deferral** costs at most one day of log/replay on a hard crash.
   Chosen deliberately; game state stays per-turn durable.
4. **`list_log` predicts `seq`** as `max(durable)+n`. Correct for one writer
   per session, which is the case in practice; two processes appending to the
   same session's log concurrently could show numbers that differ from what
   lands. Display only.
