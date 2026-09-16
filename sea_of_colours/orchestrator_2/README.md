# orchestrator_2 — where agents plug in

This package decides *who* takes a turn and *how* they are called. It
delivers the same world view to every agent and lets each one bring
whatever brain it likes: a Python harness in this repo, a heuristic, or
(in principle) a remote service.

For the hackathon, everything you need is here and in
[`harnesses/tabula_v12/README.md`](harnesses/tabula_v12/README.md).

## Mint your own agent

```bash
python scripts/new_agent.py --team redwatch --name reaper
python run_web.py          # restart; REDWATCH_REAPER is now in NEW GAME
```

That forks V12 — the shipped LLM agent — into
`harnesses/redwatch_reaper/`, renames its identity so its turns are
attributed to you, and writes the `agent.json` that registers it. Then open
`harnesses/redwatch_reaper/README.md`: it explains the pipeline and
points at the two deliberate gaps that are the exercise.

Naming is `<team>_<agent>`. A room full of forks all called `reaper`
helps nobody, and the label ends up in the audit trail and on the
scoreboard.

**Fork; don't edit V12 in place.** V12 is the control in your
experiment. Change it directly and you can no longer tell whether your
agent is better than the baseline, because there is no baseline left.

## What a fork actually edits

Everything is inside your own `harnesses/<your_fork>/`. Registering a
fork edits nothing shared, so that directory is the whole surface — which
is also why a room full of teams can work in one repo.

Which module does what is in the fork guide
([Where to make changes](harnesses/tabula_v12/README.md#where-to-make-changes)).
This is the other view of the same thing: the files one *shipped* fork
touched, so you can size the job before starting it.

**Your tests live in there too** — `harnesses/<your_fork>/tests/`, not in
the repo's `tests/`. They are outside `pytest.ini`'s `testpaths`, so run
them with `python -m pytest sea_of_colours/orchestrator_2/harnesses/<your_fork>`.
This is not tidiness (v1.43). `emp_harvest_test` put one file in the
shared suite, and the next time its author edited that fork's doctrine
the repo's only red belonged to one team's half-finished idea. With
forty forks in a room that is everybody's suite, all day.
`test_a_forks_tests_stay_inside_the_fork` fails if one drifts back.

### Gap 1 — firing the weapons V12 already buys

`harnesses/emp_harvest_test/` is a real minted fork in this repo, and
the table below is its whole diff against V12 — eight files, one of them
new. Run `python scripts/soc.py weapons --agent <yours>` to see which
rung you are on; the ladder is ordered because each rung is invisible
until the one below it works.

The same job written out line by line, with the wrong turns kept in, is
[`docs/TEACHING_WEAPONS.md`](../../docs/TEACHING_WEAPONS.md).

| File | Change | Why it is on the list |
| --- | --- | --- |
| `prompt.py` | A `YOUR RACK` block in the STATE the model reads | Rung 1. `weapon_stock` is on the view and V12 prints it nowhere, so the night phase plans as though the rack were empty |
| `scorch.py` *(new)* | Blast geometry, target picking, friendly-fire guard | Four other modules need the same footprint maths; one copy of it |
| `agency.py` | Register `EMP_SCORCH`, `SCORCH_REDSIGN`, `BLIND_SCORCH` | The model picks ids off a menu. A play that is not on the menu cannot be chosen, however good the doctrine |
| `doctrine.py` | `DOCTRINE_SCORCH` — when to spend a charge, and when not to | Every other doctrine block is about surviving someone else's weapon |
| `packager.py` | Compile the new ids into `emp_launch` moves | Ids → concrete moves. Also sequences the salvo and keeps your own fleet out of the cloud |
| `chat_schema.py` | Widen the move enum with `emp_launch`; loosen `at` to take a salvo's list of cells | Only governs the **fallback** LLM mover — but a strict enum is a hard wall, so without this a fallback night silently disarms the seat |
| `harness.py` | Pass enemy probe sightings into planning; hoist the salvo to hour 1 | Both move sources meet here, and it is the only point that sees what was actually produced |
| `orbit_policy.py` | Buy the EMP on the first day it can be paid for | You cannot fire what you never bought — and the two halves have to move together, or BLUE goes into a rack instead of harvesters |

**Do not make these edits in `_v7/`.** Two of the rows above have an
obvious-looking home there — the STATE block is built in `_v7/prompt.py`
and the move enum is defined in `_v7/chat_schema.py` — and the fork
changed neither. `_v7/` is the frozen substrate the regression tests
measure against; the fork imports the piece it needs and overrides it in
the top-level module of the same name. Edit `_v7/` and you still get an
agent, but you no longer have a baseline to tell you whether it is
better.

### Gap 2 — taking BLUE seriously

Smaller, and mostly numbers rather than new code. The five levers that
hold blue down are tabulated in
[Gap 2](harnesses/tabula_v12/README.md#gap-2--it-treats-blue-as-an-afterthought);
two of them are constants at the top of `value_pyramid.py`, and the
other three gate *when* blue is even offered (`prompt.py`, `harness.py`,
`doctrine.py`).

## Registering by hand

**Do not edit `binding_registry.py`.** Since v1.39 registration is
discovery: any directory under `harnesses/` holding a valid `agent.json`
is found at import and bound automatically. That is what lets a room of
teams register agents simultaneously without a shared file to queue
behind, and what lets an organiser collect forty forks without a merge.

`scripts/new_agent.py` writes the manifest for you. If you wrote a
harness from scratch, write it yourself — it goes beside your
`harness.py`:

```json
{
  "team": "redwatch",
  "name": "reaper",
  "participants": ["Ada Lovelace", "Grace Hopper"],
  "entry": "harness:run",
  "menu_label": "REDWATCH_REAPER — redwatch's agent",
  "needs_llm": true
}
```

The directory name must be `<team>_<name>`, because that is the import
path — a manifest that disagrees with its own folder is rejected at load
rather than surfacing later as a seat silently falling back to the
heuristic. `participants` is required: the league is the day's public
record, and an entry naming nobody cannot be credited or chased.
`needs_llm` fails loudly at game creation if there is no PAT, which is
better than a mid-game stall.

That is the whole registration. The New Game dropdown is built from
`selectable_agents()` and served at `/api/meta/agents`, so your agent
appears without touching the frontend, and the eval CLI accepts your
label as a `--config` without an eval config.

Omit `menu_label` to keep an agent routable but unlisted — useful for a
half-finished fork you don't want opponents picking yet.

## The harness contract

One function. It is called once per turn, for one seat:

```python
def run(*, store, session_id: str, player: str,
        view: Mapping[str, Any]) -> Dict[str, Any]:
    ...
```

| Argument | What it is |
| --- | --- |
| `store` | Storage backend handle; pass it to engine calls |
| `session_id` | The game |
| `player` | Your seat — `"p1"`, `"p2"`, … |
| `view` | The turn brief. `view["agent_view"]` is the fog-limited board |

**You must call `soc_engine.submit_policy(store, session_id, player,
moves)` yourself.** Returning moves is not enough; the orchestrator
checks that a policy landed and substitutes a heuristic fallback if one
didn't.

Return an audit envelope. Everything is optional except that the shape
is a dict:

```python
{
  "ok": True,
  "agent_id": "REDWATCH_REAPER",
  "rationale": "one-line summary shown in the game log",
  "submitted_policy": True,
  "moves": [...],
  "ms_elapsed": 8321,
  "extras": {"prompt_excerpt": "..."},   # captured into the audit trail
}
```

Both phases come through the same function — check `view["phase"]` and
branch, as V12 does in `harness.py`.

### What you get in `view`

`view["agent_view"]` is the fog-limited board: `meta` (rules and caps),
`hud` (scores, vault), `world` (the grid, `null` where fogged),
`my_assets`, `navigation`, `last_night`, `competitor_intel`.

Read caps and radii from `meta.rules` rather than hardcoding them —
retunes happen, and an agent with a stale constant plays a game nobody
else is playing.

`envelope.py` can render this as a text brief
(`build_universal_envelope`) if you want a prompt-shaped version. It is
deliberately agent-agnostic and takes no agent name: every agent sees
the same board.

## Debugging a turn

| Want | Where |
| --- | --- |
| What the model saw and did | Set `SOC_CARD_DUMP_DIR=/tmp/cards`, read `/tmp/cards/dNN_pN.txt` |
| Per-turn audit rows | `SOC_AGENT_INVOCATION` (rationale, prompt excerpt, timings) |
| One-line summary per turn | The in-game log panel |
| Scenario regressions | The eval CLI, below |

## Evals

```bash
# Heuristic baseline — no credentials:
PYTHONPATH=. python -m sea_of_colours.orchestrator_2.evals.cli \
    --config grid_v1 --runtime heuristic --backend memory

# V12, the agent to beat:
PYTHONPATH=. python -m sea_of_colours.orchestrator_2.evals.cli \
    --config tabula_v12 --runtime cortex --backend memory

# Your fork, by its registered label:
PYTHONPATH=. python -m sea_of_colours.orchestrator_2.evals.cli \
    --config redwatch_reaper --runtime cortex --backend memory
```

## File map

```
orchestrator_2/
├── runtime.py            public run_agent_turn + heuristic safety net
├── dispatcher.py         switch over binding.kind; imports harnesses
├── binding_registry.py   labels → AgentBinding. Shipped agents only —
│                         forks register via agent.json, not here.
├── agent_manifest.py     agent.json discovery. This is registration.
├── envelope.py           universal STATE brief (agent-agnostic)
├── cortex_chat.py        Cortex inference over REST with a PAT
├── cortex_invoker.py     shared SSE/PAT transport + per-agent caps
├── audit.py              writes SOC_AGENT_INVOCATION
├── harnesses/
│   └── tabula_v12/       V12 — the agent you fork. Read its README.
├── snowflake/            SOC_AGENT_BINDING + SOC_AGENT_MEMORY schema
├── evals/                scenario suite wrapper
└── tests/
```

## Binding kinds

| Kind | Locator | Status |
| --- | --- | --- |
| `harness_in_process` | `module.path:callable` | The one you want |
| `heuristic` | `RED_HARVEST` / `RED_HARVEST_LITE` | Built-in bots |
| `human` | `HUMAN` | Not a dispatch target; in the roster so the menu is one list |
| `cortex_agent` | Snowflake agent object name | Legacy; no agent objects ship any more |
| `harness_proc` / `harness_spcs` | proc id / HTTPS URL | Stubs, raise `NotImplementedError` |

## Resolution order

1. Seat label from the New Game menu (`AGENT_LABEL_BINDINGS`) — the
   normal path.
2. `SOC_BINDING_<PLAYER>` env var, e.g.
   `SOC_BINDING_P2=harness_in_process:my.harness:run#MYAGENT`. The label
   suffix uses `#` because harness locators already contain a colon.
3. `SOC_CORTEX_AGENT` + `KNOWN_AGENT_BINDINGS`.
4. `SOC_AGENT_RUNTIME=heuristic`.
5. Default: heuristic.

Anything unrecognised resolves to the heuristic rather than erroring, so
a typo'd label produces a bot that plays rather than a crash — check the
game log if your agent seems to have been replaced by RED_HARVEST.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for why it is built this way.
