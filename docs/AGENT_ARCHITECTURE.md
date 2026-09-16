# Sea of Colours — Agent architecture & expansion plan

> # ⚠️ HISTORICAL — describes a retired architecture
>
> This document describes the **Cortex Agents-API** generation of agents
> (`SOC_RED_REAPER_PILOT` and friends): agents declared as Snowflake
> *objects* in `soc_create_agent*.sql` specs, invoked through
> `agent/runtime.py` with `SOC_AGENT_RUNTIME=cortex`.
>
> **All of that has been removed from this distribution.** The specs are
> deleted, `agent/runtime.py` is heuristic-only, and
> `/agent/think?runtime=cortex` returns 410.
>
> **The shipped LLM agent is V12** —
> `sea_of_colours/orchestrator_2/harnesses/tabula_v12/` — which runs
> in-process, builds its prompt in Python, and calls Cortex *inference*
> over REST with a PAT. Start at that package's `README.md` and
> `ENGINE_INTERFACE.md`.
>
> Kept because the **design reasoning** (harness pre-resolves world
> state, agent gets an action-only tool surface, prompt-size discipline)
> still governs V12. Treat every file path and env var below as stale.

---

## 0. TL;DR for the next agent

- There are **two kinds of agent**: a deterministic Python **heuristic**
  (`RED_HARVEST`) and one or more **Snowflake Cortex** agents. The "haiku
  agent" you're expanding is the Cortex agent **`SOC_RED_REAPER_PILOT`**, which
  pins the model **`claude-haiku-4-5`**.
- The Cortex agent is **declared entirely in a Snowflake spec**
  (`snowflake/soc_create_agent_pilot.sql`): model, time/token budget, system
  instructions (all the game doctrine), its single tool (`soc_submit_policy`),
  and the warehouse procedure that tool maps to.
- The **orchestrator** is `sea_of_colours/agent/runtime.py::run_agent_turn`. Per
  turn it: (1) builds a structured **view**, (2) builds a **prompt**, (3) makes
  **one REST `:run` call** to the Cortex agent, streaming SSE, (4) the *agent
  itself* calls `soc_submit_policy` warehouse-side to lock its move queue, (5)
  the orchestrator polls the engine to see whether the seat locked / the night
  resolved, and (6) writes an **audit row**.
- The agent **only plays the night (PLANNING) phase**. The daytime **ORBIT**
  phase is always handled by the heuristic — **this is the biggest open
  expansion opportunity** (see §7).
- The PILOT is a **"rules-in-spec"** agent: its doctrine lives in the Snowflake
  spec, so the per-turn message is a **slim** envelope + live STATE JSON. Older
  agents (`SOC_RED_REAPER`) are **"rules-in-prompt"**: doctrine is re-sent every
  turn. The switch is the `RULES_IN_SPEC_AGENTS` set in `runtime.py`.

---

## 1. The two-agent model

### 1.1 `RED_HARVEST` — the deterministic heuristic
- File: `sea_of_colours/agent/heuristic_agent.py`.
- Pure Python, no Snowflake/Cortex needed. Runs in-process.
- Powers the **[LET RED_HARVEST PLAY]** button and is the **fallback** whenever
  a Cortex turn fails to submit.
- It is also the **only** thing that plays the **ORBIT** phase today (via
  `plan_orbit_actions`): repair → buy probes → buy harvester, gated on the
  3-slot action cap and the seat's credit balance.

### 1.2 Cortex agents — the AI seats
Registered in the `AI_AGENTS` map in `runtime.py`. Current entries:

| Agent name | Model | Prompt style | Notes |
|---|---|---|---|
| `SOC_RED_REAPER` | (per spec) | rules-in-prompt (verbose) | First AI agent; action-only tool surface. |
| `SOC_RED_REAPER_GRID_FAST` | `claude-3-5-haiku`* | rules-in-prompt | Sub-60s sibling; 55s wall-clock cap. |
| **`SOC_RED_REAPER_PILOT`** | **`claude-haiku-4-5`** | **rules-in-spec (slim)** | **The "haiku agent".** Full grid, single tool, 120s/30k budget. |

\* Note: `claude-3-5-haiku` is **not authorized** in the deployment account,
which silently broke `GRID_FAST` — `PILOT` was created partly to fix this by
pinning `claude-haiku-4-5`. Keep this in mind when adding new agents: **verify
the model is grantable in the account before relying on it.**

---

## 2. How the haiku agent is built (the Snowflake spec)

The agent is a **`CREATE OR REPLACE AGENT ... FROM SPECIFICATION $$ ... $$`**
object. Source of truth: **`snowflake/soc_create_agent_pilot.sql`**. The spec
has five load-bearing blocks:

```yaml
models:
  orchestration: claude-haiku-4-5          # the model that drives the turn

orchestration:
  budget:
    seconds: 120                           # model time budget per run
    tokens: 30000

instructions:
  orchestration: |                         # the SYSTEM prompt: ALL doctrine
    You are SOC_RED_REAPER_PILOT ...
    ── HARD CONTRACT ──   (slim brief, one tool, JSON-STRING p_policy)
    ── HOW TO READ world.grid ──  (the [y][x] reading guide)
    ── MOVE GRAMMAR ──    (drop / step / pickup / probe)
    ── CORE RULES ──      (auto-harvest, hold=6, fleet cap=21, dawn destroys)
    ── TURN PROCEDURE ──  (deterministic 8-step recipe to pick a target + chain)
  response: |                              # the after-tool-call format
    PLAN: / MOVES: / RATIONALE:

tools:
  - tool_spec:
      type: generic
      name: soc_submit_policy              # the ONE tool this agent has
      input_schema: { p_session_id, p_player, p_policy:string }

tool_resources:
  soc_submit_policy:
    type: procedure
    identifier: {{SOC_DATABASE}}.{{SOC_SCHEMA}}.SOC_SUBMIT_POLICY
    execution_environment: { type: warehouse, warehouse: {{SOC_WAREHOUSE}}, query_timeout: 60 }
```

Key design choices baked into the spec:

1. **Rules-in-spec.** Because the move grammar, world-reading guide, and turn
   procedure live in `instructions.orchestration`, they are sent to the model
   **once** (as system context) instead of being re-transmitted in every
   turn's user message. This is why the per-turn prompt for PILOT is *slim*.
2. **Single tool.** PILOT exposes **only** `soc_submit_policy`. The earlier
   agents also had `soc_save_rationale`; the `GRID_FAST` audit showed the model
   sometimes called `soc_save_rationale` *instead* of submitting, wasting the
   turn. Dropping the second tool removes that failure mode.
3. **`p_policy` is a JSON STRING, not an object.** The warehouse runtime cannot
   pass OBJECT params through tool calls, so the agent must stringify the
   envelope: `'{"moves":[{"a":"drop","unit":"harvester_p1","at":[8,5]}]}'`.
4. **The tool maps to a warehouse procedure** (`SOC_SUBMIT_POLICY`). When the
   model calls the tool, **Snowflake executes the proc warehouse-side** — it
   writes the seat's policy into the same store the orchestrator reads. The
   orchestrator does *not* relay the moves; it just observes the result.

**Deploying / editing the agent** = editing that `.sql` and re-running it
against the account (after `soc_schema.sql`, `soc_views.sql`,
`soc_procedures.sql`). The file re-applies USAGE grants and `DESCRIBE`s the
agent at the end.

---

## 3. How the orchestrator engages the agent

Entry point: **`sea_of_colours/agent/runtime.py::run_agent_turn(store,
session_id, player, runtime_override=None)`** — the single function the FastAPI
route `POST /api/game/{id}/agent/think` calls.

### 3.1 The per-turn sequence

```
run_agent_turn(store, session_id, player)
  │
  1. view = engine.get_view(store, session_id, player)      # structured world state
  │     └─ view["agent_view"] = build_agent_view(...)       # see §4
  │
  2. if view.phase == "orbit":                              # DAYTIME
  │     plan_orbit_actions(agent_view)  →  heuristic only   # NO LLM (see §7)
  │     engine.submit_orbit_actions(...) ; save audit ; return
  │
  3. runtime = runtime_override or $SOC_AGENT_RUNTIME        # "heuristic" | "cortex"
  │
  4. if runtime == "cortex":                                 # NIGHT / PLANNING
  │     invoker = CortexAgentInvoker(agent_name=$SOC_CORTEX_AGENT)
  │     if invoker.is_ready():                               # PAT + account present
  │        prompt = _build_cortex_prompt(session_id, view, agent_name)   # §4/§5
  │        result = invoker.invoke(prompt)                   # ONE REST :run call, SSE
  │        # the AGENT calls soc_submit_policy warehouse-side during this stream
  │
  5. Reconcile with the engine:
  │     status = engine.get_session_status(...)              # pending[seat]?
  │     - seat locked  → trust Cortex; night_resolved if day advanced
  │     - tried but seat empty → proc rejected; lock EMPTY, keep Cortex blame
  │     - never called the tool → FALL BACK to heuristic, relabel as RED_HARVEST
  │
  6. engine.save_agent_rationale(...)   # audit row: prompt, tool_calls, response,
  │                                     #            ms_elapsed, runtime, agent_id
  └─ return envelope { agent_id, runtime, rationale, moves, night_resolved, ... }
```

### 3.2 The actual network call (`cortex_invoker.py`)

`CortexAgentInvoker.invoke(prompt)` makes **one HTTP POST**:

- **Endpoint** (constructed from the SF config `account`):
  ```
  https://<account>.snowflakecomputing.com
     /api/v2/databases/<SOC_DATABASE>/schemas/<SOC_SCHEMA>/agents/SOC_RED_REAPER_PILOT:run
  ```
- **Auth:** `Authorization: Bearer <PAT>` — the PAT comes from `$SNOWFLAKE_PAT`
  or `pat=` / `access_token=` in `$SF_CONFIG_FILE` (default `~/.ssh/sf_config`).
  If the PAT is missing, `is_ready()` is False and the orchestrator silently
  uses the heuristic instead of crashing.
- **Body** (this is the whole request — note the prompt is the single user
  message; everything else is in the spec):
  ```json
  {
    "messages": [
      { "role": "user", "content": [ { "type": "text", "text": "<THE PROMPT>" } ] }
    ],
    "tool_choice": { "type": "auto" }
  }
  ```
- **Response:** a **streamed SSE** body. The invoker reads it line by line and
  extracts:
  - `text` / `content` chunks → accumulates the model's prose (the rationale).
  - `status: executing_tool` + `tool_use` chunks → which tools were called.
    `soc_submit_policy` flips `submitted_policy = True`; anything outside the
    declared set (`soc_submit_policy`, `soc_save_rationale`) is recorded as a
    **hallucinated tool**.
  - `tool_result` / top-level `error` chunks → tool/orchestration errors
    (surfaced so a proc rejection isn't invisible).
- **Safety caps** (`runtime` decides fallback off these):
  - **Wall-clock cap** per agent (`WALLCLOCK_CAP_OVERRIDES`): PILOT = **125s**
    (120s budget + 5s commit buffer). On cap, the stream is closed and whatever
    landed is returned with `wallclock_capped=True`.
  - **Response-body cap** (`RESPONSE_CAP_OVERRIDES`): PILOT = **40 000 bytes**
    (roomier so its full chain-of-thought shows in the AGENT "thinking" tab).
  - **Race-tolerant re-poll:** if Cortex tried to submit but the seat still
    reads empty, the orchestrator waits (200ms normal, 3s if wall-clock-capped)
    and re-checks before concluding failure — absorbs warehouse commit-visibility
    lag.

### 3.3 Who actually submits the moves

**The agent does, warehouse-side.** Because `soc_submit_policy` is wired to the
`SOC_SUBMIT_POLICY` procedure in the spec's `tool_resources`, when the model
calls the tool Snowflake runs the proc, which writes the policy into the store.
The orchestrator **never relays the move queue** for a Cortex turn — it only
reads back `get_session_status` to learn whether the seat locked. (Contrast: the
heuristic path calls `engine.submit_policy(...)` directly.)

### 3.4 Environment knobs

| Env var | Meaning |
|---|---|
| `SOC_AGENT_RUNTIME` | `heuristic` (default) or `cortex`. |
| `SOC_CORTEX_AGENT` | Which Cortex agent name to invoke (default `SOC_RED_REAPER`). Set to `SOC_RED_REAPER_PILOT` for the haiku agent. |
| `SNOWFLAKE_PAT` / `SF_CONFIG_FILE` | Credentials for the REST call. |
| `SOC_AGENT_WORLD_VIEW` | `grid` or `list` — controls the shape of `world` in the view (§4.3). PILOT expects `grid`. |
| `SOC_BACKEND` | `memory` / `file` / `snowflake`. The agent's `soc_submit_policy` proc runs warehouse-side, so live Cortex play needs `snowflake` (the session must be mirrored there). |

---

## 4. The data sent to the agent (the "view")

Built by `sea_of_colours/snowpark/view.py::build_agent_view(sess, player)` and
wrapped by `engine.get_view`. The agent payload (`view["agent_view"]`) has
exactly these top-level keys, all fog-of-war gated to the seat:

| Key | What it carries |
|---|---|
| `meta` | `day`, `phase`, `player`, `players`, `season`, `policy_actions_left/max`, and `rules` (`drop_mode`, `probe_radius`, `probe_lifetime_nights`). |
| `hud` | `score`, `scores`, `hoard` + `shipped` (`used/max/free/pct_full/by_tier/warning`), `season_day_cap`. |
| `last_night` | Yesterday's recap: `my_orders` (each `ok`/`illegal` + reason), `my_assets_destroyed`, `my_parcels_banked`. |
| `competitor_intel` | What you can lawfully see of the opponent: `new_this_day` (enemy probe launches per §3.15, witnessed trails) + `persistent_echoes`. |
| `world` | The board — **grid** or **list** shape (§4.3). |
| `navigation` | Pre-sorted convenience views: `best_red_visible` / `best_red_echo` (sorted by value DESC), `best_green`, `best_blue`, `fog_clusters` (centroid + size + `nearest_visible_edge`). |
| `my_assets` | Every asset you've owned this season: `id`, `kind`, `state` (`orbit`/`deployed`/`surface`/`destroyed`), `at`, `carrying`, `lifetime`, and `nights_remaining` for probes. |

### 4.3 `world`: grid vs list
- **grid mode** (`SOC_AGENT_WORLD_VIEW=grid`, what PILOT uses): `world.width`,
  `world.height`, and `world.grid[y][x]` — a 2D nested array. `null` = fog;
  otherwise `{tile, purity?, value?, entity?, echo?, collision?, synthetic?}`.
  `world.fog_count` is a count, not a list.
- **list mode**: `world.live[]` (current LOS), `world.echo[]` (remembered, may be
  stale), `world.fog_count`.

The prompt builder auto-detects the shape (presence of `world.grid`) and emits
the matching "READING THE WORLD" primer — see `_build_reading_block`.

---

## 5. The two prompt envelopes

Both built in `runtime.py`. The chooser is `RULES_IN_SPEC_AGENTS` (currently
just `{"SOC_RED_REAPER_PILOT"}`).

### 5.1 Slim (rules-in-spec) — `_build_cortex_prompt_slim`
Used for PILOT. Structure:
```
<one-line header: "rules are in your system instructions; do not expect them here">
session=… day=D/CAP seat=pN actions_left=L/M
"Read STATE below, then CALL soc_submit_policy ... p_policy as a JSON STRING"
STATE (JSON):
```json
{ meta, hud, last_night, competitor_intel, world(grid), navigation, my_assets }
```
```
- Capped at **`SLIM_PROMPT_CAP_CHARS = 120 000`**. On overflow it **windows**
  `world.grid` to the active region (bounding box around harvesters +
  `best_red_visible`, padded 8 cells) rather than dropping the map — the agent
  must always see the board.

### 5.2 Verbose (rules-in-prompt) — `_build_cortex_prompt`
Used for the non-PILOT agents. Structure: COMMIT-FIRST protocol → core
objective → session header → tool contract + forbidden-tools + wire-format →
HARD RULES (move grammar, budget, magnetic cover, publicity, drop legality,
collisions, vision, harvest doctrine, vault ladder, lifecycle, adjacency, juicy
seam) → READING-THE-WORLD primer → STATE JSON → closing.
- Capped at **`PROMPT_PAYLOAD_CAP_CHARS = 28 000`**; on overflow it trims
  `world.live/echo` to the N nearest cells (list mode) or drops `world.grid`
  with a `truncated_to_nearest:0` flag (grid mode).

> **See [`AGENT_PROMPT_SAMPLES.md`](./AGENT_PROMPT_SAMPLES.md)** for the full,
> real strings of both styles across a cold-open turn and a RED-in-view turn.

### 5.3 Rationale capping
The model's response is stored as the rationale, smart-truncated to
`RATIONALE_CAP_CHARS = 2 000` while preserving the structured `PLAN:` tail
(`_smart_truncate_rationale`). The full untruncated text still lands in the
audit row's `response_text`.

---

## 6. The audit trail

Every turn (Cortex or heuristic) writes a row via
`engine.save_agent_rationale` into `SOC_AGENT_INVOCATION`, capturing:
`session_id`, `day`, `agent_id` (who actually played), `player`, the capped
`rationale`, `runtime` (`cortex`/`heuristic`), the full `prompt_excerpt` (exact
prompt we sent), `tool_calls`, `response_text`, and `ms_elapsed`. The UI's
AGENT/LOG panels and the eval command-center read these.

---

## 7. Expansion opportunities (for the next agent)

In rough priority order:

1. **Teach the agent the ORBIT (daytime) phase.** Today `run_agent_turn` short-
   circuits `phase == "orbit"` straight to the heuristic `plan_orbit_actions`
   (buy/repair/ship), so the LLM **never** reasons about economy: probes vs
   harvester purchases, RED catapult bids, shipping. A real expansion would:
   - add an `soc_submit_orbit_actions` tool to the agent spec (+ its proc),
   - build an orbit-phase prompt (the view already has `orbit` data: ship
     prices, credits, queued actions),
   - branch `run_agent_turn` to invoke Cortex for orbit instead of the
     heuristic. **No orbit prompt sample exists yet because no orbit prompt is
     ever built — this is greenfield.**
2. **Weapons.** The haiku agent has no concept of EMP / chaff. The
   grammar and doctrine for these would need to be added to the spec and the
   move-validation path.
3. **Multi-harvester planning.** The PILOT turn procedure is explicitly "ONE
   good harvester drop". Late-game seats can field 3 harvesters; a stronger
   agent would chain all of them within the 21-action fleet budget.
4. **A new agent variant.** To add one:
   1. Write `snowflake/soc_create_agent_<name>.sql` (copy the PILOT spec; pick a
      grantable model; tune budget/doctrine/tools).
   2. Register it in `AI_AGENTS` (and `RULES_IN_SPEC_AGENTS` if its doctrine is
      in-spec) in `runtime.py`; add wall-clock / response caps in
      `cortex_invoker.py` if needed.
   3. Point `SOC_CORTEX_AGENT` at it and run.

### 7.1 How to test an agent change
- **Single raw turn against a real grid:** `scripts/pilot_turn.py`
  (`SOC_BACKEND=snowflake SOC_AGENT_WORLD_VIEW=grid SOC_CORTEX_AGENT=... python
  scripts/pilot_turn.py --scenario solo_drop_orbit --show-prompt`) — prints the
  exact prompt sent, the tool calls, whether `soc_submit_policy` fired, and the
  landed move queue.
- **Eval scenarios:** `scripts/run_evals.py` + `sea_of_colours/evals/` (parsed
  policy + assertions across fixtures).
- **Full season:** `scripts/run_season.py` (Snowflake-backed) /
  `scripts/run_battery.py` (local heuristic battery).
- **Prompt-shape unit tests:** `tests/test_agent.py` (cap behaviour, grid vs
  list primer, slim vs verbose selection).

---

## 8. File map

| Concern | File |
|---|---|
| Orchestrator (turn loop, prompt builders) | `sea_of_colours/agent/runtime.py` |
| REST/SSE Cortex client | `sea_of_colours/agent/cortex_invoker.py` |
| Heuristic agent (+ orbit planner) | `sea_of_colours/agent/heuristic_agent.py` |
| Haiku agent spec | `snowflake/soc_create_agent_pilot.sql` |
| Other agent specs | `snowflake/soc_create_agent*.sql` |
| View / payload builder | `sea_of_colours/snowpark/view.py` |
| Submit-policy proc | `snowflake/soc_procedures.sql` |
| Single-turn capture tool | `scripts/pilot_turn.py` |
| Eval harness | `sea_of_colours/evals/`, `scripts/run_evals.py` |
| Captured prompt samples | `docs/AGENT_PROMPT_SAMPLES.md` |
