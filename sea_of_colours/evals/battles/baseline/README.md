# The V12 baseline — what you forked, frozen

One run of stock `tabula_v12` over every board at rung `armed`, with and
without an EMP in the rack. Checked in so you can answer *"is my fork
actually better than the thing I copied?"* without a Snowflake PAT and
without waiting five minutes for twenty model calls.

```
index.json      summary: score, failed checks, ordnance, per battle
cards/          one card per battle — the moves V12 actually issued
```

## Reading it

`soc why` prints it automatically underneath your own run, so the usual
way to use this is just:

```bash
python scripts/soc.py why plain_night_armed my_agent --rung armed --loadout emp
```

which gives you your orders, your checks, then V12's orders and what it
missed, in the same shape, one above the other.

## The one number worth staring at

```
fired: 0
```

on **all twenty battles**, including the ten where it was holding an
EMP. That is not a bug in the recording. Stock V12 buys weapons and
never fires them, and this is the receipt. If your fork's ORDNANCE line
also reads zero, you have not yet closed the largest scoring gap in the
kit — [`docs/TEACHING_WEAPONS.md`](../../../../docs/TEACHING_WEAPONS.md)
is that job done once, end to end.

## What it is not

**Not an oracle.** An LLM is not deterministic, so this is *one* run,
not V12's true mean. A 5% gap either way is noise. A check V12 passed
and you failed is a lead worth following; a check you both failed is
probably the board being hard.

**Not automatically current.** If you retune a board or add one, this
snapshot silently goes stale for it — a board with no card here simply
prints no comparison, which is a missing answer rather than a wrong one.
`tests/test_battles_baseline.py` fails if a board has no baseline, so
you will be told rather than left guessing.

## Regenerating

Needs a PAT and a few minutes:

```bash
python scripts/soc.py suite --agent tabula_v12 --rung armed \
    --loadout empty,emp --cards sea_of_colours/evals/battles/baseline/cards
```

then rebuild `index.json` from the cards. Do this only when a board
changes — not to "get a better number", which defeats the point of a
control group.
