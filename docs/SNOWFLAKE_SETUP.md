# BYO-Snowflake setup

**TL;DR: you don't need any of this to play.** Just start the server and
you get a full game against `RED_HARVEST` / `RED_HARVEST_LITE` with zero
external services:

```bash
python run_web.py
```

> The storage backend is auto-detected. With no Snowflake setup you land
> on the in-process `memory` store, and the server says so on boot.
> Nothing below is required to reach that state.

Beyond playing, there are two reasons to bring a Snowflake account into
it. They are **technically independent** — either works without the
other — but they use **different credentials**, which is the single
most confusing thing on this page:

1. **Running / improving V12** — the reference Cortex agent
   (`sea_of_colours/orchestrator_2/harnesses/tabula_v12/`). It calls
   Snowflake Cortex's inference API to think; everything else about it
   is plain Python on your machine. **A Programmatic Access Token, and
   nothing else — no extra install, no schema deploy.**
2. **Persistent game history** (`SOC_BACKEND=snowflake`) — every season
   written to real tables instead of living in server memory.
   **Key-pair auth + one extra dependency + a schema deploy.**

Setting up one does not set up the other: a PAT will not open a Snowpark
session, and a key pair will not authenticate a Cortex call.

**If you're here for the hackathon, do both.** (1) lets you play V12;
(2) is what the iteration tooling reads — `turn_suite.py`,
`replay_turn.py` and `advise_v12.py` all work from persisted sessions,
and the launcher only offers LLM agents on a persistent backend, because
an LLM game you can't reopen teaches you nothing. Budget about ten
minutes for (2); the key-pair step is the fiddly one and §2 spells it
out.

## 1. Playing against / building V12 — PAT only, no schema deploy

V12 talks to Snowflake over one REST endpoint —
`/api/v2/cortex/v1/chat/completions` (Cortex's inference API, not the
"Agents" feature) — using a Programmatic Access Token. It does **not**
need any table, view, or stored procedure deployed; the harness runs
entirely as local Python and calls `soc_engine.submit_policy(...)`
directly, same as the heuristic agents.

### Steps

1. **Get a Snowflake account.** A
   [30-day free trial](https://signup.snowflake.com/) works — pick any
   cloud/region, the `Standard` edition is enough as long as Cortex is
   available in that region ([regional availability list](https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql#region-availability)).
2. **Generate a Programmatic Access Token (PAT).** In Snowsight:
   `Admin → Users & Roles → <your user> → Programmatic access tokens →
   Generate new token`. Give it a role that can call Cortex (see next
   step) and copy the token value — Snowflake only shows it once.
3. **Make sure your role can use Cortex.** Any role with the
   `SNOWFLAKE.CORTEX_USER` database role granted (ACCOUNTADMIN has it by
   default on a fresh trial account) can call the inference endpoint:
   ```sql
   GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE <your_role>;
   ```
4. **Name your account in a Snowflake connection.** This kit reads the
   *standard* connection store (v1.45) — the same
   `~/.snowflake/connections.toml` used by the Snowflake CLI, Cortex
   Code and the VS Code extension — so if you have ever run
   `snow connection add` you are already done and can skip to step 5.

   Otherwise create `~/.snowflake/connections.toml`:
   ```toml
   [soc]
   account = "<your_account_identifier>"
   ```
   then either make it the default with
   `default_connection_name = "soc"` in `~/.snowflake/config.toml`, or
   point just this game at it:
   ```bash
   export SOC_SNOWFLAKE_CONNECTION=soc
   ```
   Find your account identifier in Snowsight's bottom-left account
   selector, or in the URL: `https://<account_identifier>.snowflakecomputing.com`.

   `soc doctor` prints the exact file and section it resolved, which is
   the fastest way to check you are pointed where you think.

   > **Already have a `~/.ssh/sf_config`?** It still works and is read
   > last, so nothing you have set up breaks. It is deprecated: it is not
   > an SSH config despite living in `~/.ssh`, no other Snowflake tool
   > reads it, and it makes you configure the same account twice.
5. **Export the PAT** in the shell you'll run the game / scripts from:
   ```bash
   export SNOWFLAKE_PAT=<the token from step 2>
   ```
6. **Smoke-test the credentials** before wiring up a whole game:
   ```bash
   python - <<'PY'
   from sea_of_colours.orchestrator_2.cortex_chat import CortexChatInvoker
   inv = CortexChatInvoker(max_completion_tokens=20)
   print("ready:", inv.is_ready())
   print(inv.invoke("Reply with exactly the word: pong"))
   PY
   ```
   `ready: True` and a response containing `pong` means you're set.
7. **Play a game against V12** — seat P2 as `V12` in the New Game
   launcher, or run it headless with
   `python scripts/run_matchup_v12.py --modes heur lite`. A PAT-only
   `sf_config` deliberately does not flip the storage backend, so
   finishing this section won't start writing to your account.

   > The headless runner works on a PAT alone. The **launcher** offers
   > LLM seats only on a persistent backend, so if you haven't done §2
   > yet the V12 option won't be in the dropdown — that's the storage
   > rule, not a credentials problem.

### Cost / rate-limit notes

- Each V12 turn makes a small, bounded number of chat-completions calls
  (see `sea_of_colours/orchestrator_2/harnesses/tabula_v12/harness.py`),
  each capped by `max_completion_tokens` and a wall-clock timeout — so a
  runaway turn can't rack up an unbounded bill.
- Trial-account credits comfortably cover a full hackathon's worth of
  matches; Cortex inference billing is per-token, independent of
  warehouse compute (no warehouse is even required for this path).

## 2. Persistent Snowflake-backed sessions (`SOC_BACKEND=snowflake`)

**Hackathon attendees: do this one too.** Section 1 is enough to *play*
against V12, but the agent-iteration tooling — `scripts/turn_suite.py`,
`scripts/replay_turn.py`, `scripts/advise_v12.py` — all read persisted
sessions, and improving your agent is the point of the day. Without this
you can play a game but not take one apart afterwards. It also unlocks
the LLM agents in the New Game launcher, which are restricted to
persistent backends for exactly that reason.

It writes every season to real tables (`SOC_SESSION`,
`SOC_REPLAY_FRAME`, …) instead of living in server memory, and pulls in
one extra dependency:

```bash
pip install -r requirements-snowflake.txt
```

1. **Generate a key pair.** This path authenticates with a Snowpark
   session rather than the PAT — a different mechanism, same config
   file. Two commands, in whatever directory you keep keys:
   ```bash
   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
   openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
   ```
   `-nocrypt` gives an unencrypted key, which is what the loader expects;
   an encrypted one fails with a passphrase error.
2. **Register the public key on your Snowflake user.** Snowflake wants
   the base64 body *only* — no `-----BEGIN/END-----` lines, no newlines.
   This prints exactly what to paste:
   ```bash
   awk 'NR>2 {print prev} {prev=$0}' rsa_key.pub | tr -d '\n'
   ```
   Then, in Snowsight:
   ```sql
   ALTER USER <your_username> SET RSA_PUBLIC_KEY='<the single line from above>';
   ```
   Getting `JWT token is invalid` later almost always means a stray
   newline or a header line survived this step.
3. **Extend your connection** with the user and the *private* key path:
   ```toml
   [soc]
   account = "<your_account_identifier>"
   user = "<your_username>"
   private_key_file = "/path/to/your/rsa_key.p8"
   ```
   Key-pair auth is what selects the Snowflake storage backend — a PAT
   alone never does, which is why finishing §1 cannot start writing to
   your account.

   (Snowflake's own [key-pair auth docs](https://docs.snowflake.com/en/user-guide/key-pair-auth)
   cover rotation and encrypted keys if you want them.)
4. Check where you're about to deploy. The defaults are hackathon-scoped
   so they can't collide with an existing SOC install in the same
   account:
   ```bash
   python scripts/deploy_soc_schema.py --dry-run
   # Target: SOC_HACKATHON_DB.SEA_OF_COLOURS (warehouse SOC_HACKATHON_WH)
   ```
   Override with `SOC_DATABASE` / `SOC_SCHEMA` / `SOC_WAREHOUSE` (or the
   `database=` / `schema=` / `warehouse=` keys in `sf_config`).
5. Deploy. The database, schema and an XSMALL auto-suspending warehouse
   are all created if absent, so this works on a brand-new trial
   account. Non-destructive — safe to re-run:
   ```bash
   python scripts/deploy_soc_schema.py
   ```
6. Confirm the whole path is live:
   ```bash
   python scripts/quickstart_check.py
   ```
7. Start the server. With key-pair auth in `sf_config` plain
   `python run_web.py` will auto-detect Snowflake, but for a season you
   intend to keep, be explicit and turn reload off:
   ```bash
   SOC_BACKEND=snowflake python run_web.py --no-reload
   ```
   Explicit is **strict**: the server exits with the reason instead of
   quietly handing you a `memory` store when something in the Snowpark
   path is broken — which otherwise surfaces an hour later as a missing
   season and absent LLM seats. `--no-reload` stops a file save from
   restarting the process and dropping an agent turn mid-night — the
   trade is that a Python change needs a manual restart, so add
   `--replace` to have the old server stopped for you instead of meeting
   `address already in use`. Run it in your own terminal, not through an
   AI coding agent, and leave it up.
   Storage is a **per-game** choice from v1.14: the New Game launcher
   offers Snowflake or Memory per season, so you can still take a fast
   throwaway game against a heuristic without a warehouse round-trip per
   move. `SOC_BACKEND` only sets the default the launcher starts on.

> **Seasons accumulate; nothing is wiped.** `init_session` mints a fresh
> `session_id` and the row coexists with every earlier season, so
> starting a new game never touches your history. Wiping is opt-in and
> explicit — `store.wipe_all_sessions()`, or `--wipe-first` on
> `scripts/run_season.py`. Still worth running the `--dry-run` above so
> you know which deployment you're pointed at.

### Deploy flags

| Flag | Effect |
| ---- | ------ |
| `--schema-only` | Stop after `soc_schema.sql`, `orchestrator_v2_schema.sql` and `soc_views.sql`. |
| `--no-procs`    | Skip `soc_procedures.sql` (procedures will be missing). |
| `--config FILE` | Force a legacy key=value config file. Omit it to use your standard Snowflake connection; `SOC_SNOWFLAKE_CONNECTION` picks the section. |
| `--dry-run`     | Print the resolved database / schema / warehouse and exit without connecting. |

> **Deployed before v1.19?** Re-run the deploy. `SOC_AGENT_MEMORY` and
> `SOC_AGENT_BINDING` live in `orchestrator_v2_schema.sql`, which was never
> wired into this script, so no account has them. V12 mirrors its
> cross-night memory there best-effort and swallows the failure, so the
> only symptom is the agent quietly forgetting the season between server
> restarts. Everything here is `CREATE TABLE IF NOT EXISTS`; re-running is
> non-destructive.

The deploy also builds and uploads the engine package zip
(`build/sea_of_colours.zip`) to `@SOC_PY_STAGE`, so every stored
procedure's `IMPORTS =` clause resolves to live code.

### What it costs

- Every executed move writes one row to `SOC_REPLAY_FRAME` (plus a
  per-night `[opening]` and `[dawn]` row). A full 25-move night caps at
  ~27 rows; a 30-night season is roughly 750 VARIANT rows.
- Stored procedures run on the warehouse resolved by
  [`naming.py`](../sea_of_colours/snowpark/naming.py) — by default a
  dedicated XSMALL `SOC_HACKATHON_WH` with `AUTO_SUSPEND = 60`. Point
  `SOC_WAREHOUSE` at an existing warehouse if you'd rather not have
  another one.
- **V12 doesn't use a warehouse at all.** It bills Cortex inference
  tokens per turn, calling the chat-completions endpoint directly with
  a PAT. That cost applies whether or not you deploy this schema.

### Seasons accumulate

Every game is a new `session_id` alongside the last, on any backend —
the season picker lists them all, and replays stay available. If you do
want a clean slate, `store.wipe_all_sessions()` is public and
`scripts/run_season.py --wipe-first` exposes it; nothing calls it for
you. Archiving-before-wipe is deliberately deferred.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `CortexChatInvoker(...).is_ready()` is `False` | `SNOWFLAKE_PAT` unset/empty, or no `account` on the resolved connection. Run `soc doctor` — it prints the file and section it read. |
| `soc doctor` says `no connection named 'x'` | `SOC_SNOWFLAKE_CONNECTION` (or `SNOWFLAKE_DEFAULT_CONNECTION_NAME`) names a section that isn't in your `connections.toml`. It lists the ones that are. Deliberately refused rather than falling back, so you can't end up on someone else's account by typo. |
| `soc doctor` reads a connection you didn't expect | Precedence is: `SF_CONFIG_FILE` if exported → `SOC_SNOWFLAKE_CONNECTION` → `SNOWFLAKE_DEFAULT_CONNECTION_NAME` → `default_connection_name` in the file → the only section → `[default]`. |
| `401`/`403` from the chat-completions call | Role on the PAT doesn't have `SNOWFLAKE.CORTEX_USER` granted, or the PAT expired. |
| Cortex call times out / V12 falls back to a shorter plan | Normal under load — V12's wall-clock cap intentionally truncates and falls back rather than stalling the game; see the harness's timeout constants if you want to tune it. |
| `ModuleNotFoundError: snowflake.snowpark` | You set `SOC_BACKEND=snowflake` without `pip install -r requirements-snowflake.txt`. Unset it to auto-detect, or install the extras. Not needed for V12 / the PAT path above. |
| `SOC_BACKEND=snowflake was requested but the store could not be opened` | Explicit requests are strict by design — the server exits rather than silently writing your season to the in-memory store. The next line names the fix. |
| Boot says `store backend: memory` when you wanted Snowflake | Auto-detect needs *both* the Snowpark extras and `private_key_file=` in `sf_config`; the boot line says which one is missing. A PAT alone is section 1 and never selects this backend. |
| Sessions disappear when you restart the server | That game was on `memory` — either the server default, or the launcher's STORAGE choice. Pick Snowflake for that game; `GET /api/meta/backend` shows the default and every option. |
| `JWT token is invalid` / authentication fails on a fresh key pair | Almost always the public key: `RSA_PUBLIC_KEY` must be the base64 body with no `-----BEGIN/END-----` lines and no newlines. Re-run the `awk`/`tr` one-liner in §2 step 2 and re-`ALTER USER`. |
| Key loading fails with a passphrase error | The key was generated encrypted. Regenerate with `-nocrypt` as in §2 step 1. |
| The V12 option is missing from the New Game dropdown | You've selected the Memory backend, which is heuristics-only — an LLM game that isn't persisted leaves nothing for the turn suite or the advisor to read. Switch STORAGE to Snowflake, or complete §2 if it's greyed out. |
| Snowflake is greyed out in the launcher's STORAGE row | The modal shows the reason inline (missing extras, no `private_key_file=`, key file absent). Fix that and reopen the launcher — no restart needed. |
| A finished Snowflake season is missing a day's log or replay, but the game itself is intact | Write buffering (on since v1.43) defers a day of LOG text, replay frames and invocation rows, and the process died before the day flushed. Game state flushes every turn, which is why the game survived. `SOC_BUFFERED_STORE=0` disables it at the cost of ~35% on a season. |
