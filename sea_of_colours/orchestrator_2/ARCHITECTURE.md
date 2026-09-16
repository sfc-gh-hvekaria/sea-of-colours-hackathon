# orchestrator_2 — Architecture

## Why a parallel orchestrator?

The legacy orchestrator (`sea_of_colours/agent/runtime.py`) couples
agent-specific preprocessing (candidate compilation, threat brief,
season memory) into the prompt builder. That coupling has worked but
makes it hard to:

1. Guarantee every agent receives the SAME world view (fairness in a
   competitive tournament).
2. Treat agents as independent submissions — i.e. let anyone deploy a
   harness without touching the orchestrator.
3. Host harnesses outside the orchestrator process (Snowflake stored
   procedure, SPCS container, etc.).

orchestrator_2 splits the responsibilities cleanly:

* The **orchestrator** owns world-state loading, the universal STATE
  envelope, dispatch routing, policy-queue reconciliation, audit, and
  the heuristic fallback.
* Each **agent harness** owns its own preprocessing, its own prompt
  shape, its own Cortex Agent invocation, and its own quirks.

## The dispatch contract

```
┌─────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR (orchestrator_2.runtime.run_agent_turn)            │
│                                                                  │
│  1. view = soc_engine.get_view(store, session_id, player)       │
│  2. binding = binding_registry.resolve_binding(...)             │
│  3. result = dispatcher.dispatch_turn(binding, view, ...)       │
│  4. reconcile against SOC_POLICY_QUEUE                          │
│  5. if not submitted → heuristic fallback                       │
│  6. audit.write_invocation(...)                                 │
│  7. return envelope dict                                        │
└────────────────────────────┬────────────────────────────────────┘
                             │
                  ┌──────────┴──────────┐
                  │  Resolved binding   │
                  └──────────┬──────────┘
        ┌────────────────────┼─────────────────────────┐
        ▼                    ▼                         ▼
  kind=cortex_agent    kind=harness_in_process    kind=heuristic
  POST agents/X:run    import X:Y; call X:Y(view) plan_moves(view)
                                                  submit_policy()
                  ┌──────────┐
                  │ FUTURE   │
                  │ kinds    │
                  └──────────┘
        kind=harness_proc   kind=harness_spcs
        CALL <proc>(...)    POST <spcs_url>
        (NotImplemented)    (NotImplemented)
```

## Binding kinds

| Kind | Locator format | Use case |
|---|---|---|
| `cortex_agent` | Snowflake agent name (`SOC_FOO`) | Bare Cortex Agent that reads the universal envelope and calls `soc_submit_policy` |
| `harness_in_process` | `<module_path>:<callable>` | Python harness in this repo with custom preprocessing |
| `harness_proc` | Snowflake procedure identifier | (deferred) Stored-procedure-hosted harness |
| `harness_spcs` | HTTPS URL | (deferred) SPCS container-hosted harness |
| `heuristic` | `RED_HARVEST` | In-process RED_HARVEST policy |

## Binding resolution order

The registry checks, in order:

0. **Seat label** from the New Game menu, looked up in
   `AGENT_LABEL_BINDINGS`. This is how live games route and the path
   almost everything takes. It deliberately outranks the env vars: a
   host with `SOC_CORTEX_AGENT` exported shouldn't have it silently
   override what a player picked in the menu.
1. **`SOC_BINDING_<PLAYER>` env var** — explicit override, format
   `kind:locator[#label]`. The label separator is `#`, not a third
   colon, because `harness_in_process` locators contain a colon
   (`module:callable`) and colon-splitting mangled them.
2. **`SOC_CORTEX_AGENT` env var + `KNOWN_AGENT_BINDINGS` map** — lets
   headless callers name an agent by its registry key. Today maps
   `SOC_RED_REAPER_TABULA_V12` plus any hackathon forks.
3. **`runtime_override="cortex"` + unknown agent name** — falls back to
   `kind=cortex_agent`. Vestigial: no Cortex agent objects ship any
   more, since V12 talks to Cortex *inference* over REST.
4. **`SOC_AGENT_RUNTIME=heuristic` (or unset)** — `kind=heuristic`.
5. **Default** — `kind=heuristic`.

Unrecognised labels resolve to the heuristic rather than raising, so a
typo produces a bot that plays instead of a crashed turn. Convenient in
a live game, confusing while developing — if your agent seems to have
been replaced by RED_HARVEST, check the label spelling first.

The `SOC_AGENT_BINDING` Snowflake table is authored in
`snowflake/orchestrator_v2_schema.sql` but not consulted yet; the
in-process registry covers every current use case.

## What's "universal" vs what's per-agent

| Concern | Universal (orchestrator) | Per-agent (harness) |
|---|---|---|
| World-state loading | yes | no |
| STATE envelope (meta/hud/world/navigation/my_assets/last_night/competitor_intel) | yes | no |
| Move grammar | yes | no |
| Policy-queue reconciliation | yes | no |
| Heuristic fallback on miss | yes | no |
| Audit row write | yes | no |
| Option-menu compilation | NO | yes (V12 and its forks) |
| Threat brief | NO | yes (V12 and its forks) |
| Season memory | NO | yes (V12 and its forks) |
| Custom prompt format | NO | yes |
| Inner Cortex Agent invocation | NO | yes |
| Wall-clock cap | mechanism yes, value per-agent | value yes |

## Fairness contract

The orchestrator MUST NOT inject agent-specific keys into the universal
envelope. Two automated tests pin this:

* `tests/test_envelope.py` asserts the envelope shape is identical for
  any caller (no `agent_name` parameter).
* `tests/test_dispatcher.py::test_universal_envelope_identical_across_bindings`
  builds envelopes for `cortex_agent` and `heuristic` and asserts they
  are byte-equal.

If you find yourself wanting to add an `agent_name` argument to
`envelope.build_universal_envelope`, you almost certainly want to move
that logic into the agent's harness module instead.

## Compatibility with the legacy orchestrator

* `sea_of_colours.agent.runtime.run_agent_turn` is unchanged and serves
  PILOT v1, GRID_FAST, SOC_RED_REAPER, LIST_V2, heuristic. Switching a
  caller to orchestrator_2 is one import change.
* `sea_of_colours.evals.*` continue to use the v1 runtime. The
  `orchestrator_2/evals/` wrapper re-uses scenarios/builders/assertions
  but swaps the agent-turn callable.
* The audit table `SOC_AGENT_INVOCATION` is shared; orchestrator_2
  writes the same column shape (`runtime` column carries the binding
  `kind` for orchestrator_2 turns so postmortems can tell the two
  apart).
