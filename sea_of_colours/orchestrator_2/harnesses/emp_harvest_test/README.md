# EMP_HARVEST_TEST — the worked example

This is **not** the agent you fork, and it is **not** the baseline you are
measured against. Both of those are `harnesses/tabula_v12/`.

This directory ships as a **worked example**: one team's attempt at V12's
first deliberate gap — *it buys weapons and never fires them* — left in the
kit so you can read a real attempt before you start your own. It is a
demonstration, not a finished agent. Read it, disagree with it, do better.

To start your own entry, don't copy this directory. Mint a fresh one:

```bash
soc new --team redwatch --name reaper --participants "Ada, Grace"
```

---

## The finding this example exists to record

The obvious reading of gap 1 is that V12 *can't* fire — that the plumbing
to get a salvo from the model's reply into the engine is missing. So you
build that plumbing. `docs/TEACHING_WEAPONS.md` calls it the four rungs:
the weapon reaches the prompt, the model can name it, the sanitiser lets
it through, the engine resolves it.

Building all four rungs took about a day, and at the end of it the agent
**still would not fire.** Every rung passed in isolation and the model
kept choosing harvest chains anyway.

The reason is economics, not plumbing. Each option on the menu is
rendered with the yield it banks. A harvest chain shows a real number. A
plain salvo — `BLIND`, `EMP`, `SCORCH` — shows `+0`, because blowing up
an opponent's holding banks nothing for you that night. A model asked to
pick the best-scoring option will never pick the one that scores zero,
no matter how many rungs you built underneath it.

**Firing a weapon is not a plumbing problem. It is a menu-pricing
problem.** That is the thing worth taking away from this directory, and
it generalises past weapons to anything you want an agent to start doing.

## What this example does about it (v13)

Rather than teach the model that `+0` is sometimes worth it, this attempt
changes what is on the menu. A **compound play** bundles the salvo with
the harvester wave that exploits it, and offers the pair as one option —
so the thing the model is choosing between now carries a real yield
number, and competes on the same terms as an ordinary harvest.

Two are offered, each keyed to a redsign state the plain patterns handle
badly:

| Play | When | What it does |
| --- | --- | --- |
| `SMASH_THEN_LOCK` | mine, contested | Smash the pure at H1, lock the halo at H3 — pure banked and the halo denied |
| `RACE_CRASH_EMP` | shared vision, contested | Drop at H1, lift at H2, salvo at H3 — take the pure, then close the door behind you |

The model picks one compound id; `packager.py` emits every hour of it in
the right order. Salvo-only waves still bank nothing, and
`option_economics.py` is explicit about that so the accounting stays
honest.

Where to look:

- `seam_control.py` — the compound patterns and the `emp_launch_at` /
  `emp_hole` / `defer_until_clear` wave fields they need
- `packager.py` — `spend_emp`, which turns a chosen compound into hours
- `doctrine.py` — the case matrix that decides which play is offered
- `option_economics.py` — why an `emp_only` wave banks nothing

## Whether it works is your experiment

This example records an approach and the reasoning behind it. It does not
come with a scoreboard, and you should not assume the compound plays are
an improvement. Cast it into a frozen turn next to stock V12 and look at
what actually changed:

```bash
soc lab            # what frozen turns exist, and who can play them
python run_web.py  # then visit /lab and cast EMP_HARVEST_TEST into a seat
```

The lab deliberately gives you a divergence view rather than a mark. The
question it answers is *how is this different*, which is the only
question an example like this can honestly settle.
