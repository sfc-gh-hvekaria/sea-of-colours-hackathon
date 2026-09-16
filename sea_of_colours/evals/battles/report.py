"""Turn suite results into something worth reading at 2am.

The audience is someone who just watched their agent lose and wants to
know what to change. So the report is ordered by what to fix, not by
board id, and it leads with the diagnosis rather than the score.

Three things it tries to surface that a pass/fail table cannot:

* **Where on the ladder it stopped.** An agent clean at ``quiet`` and
  failing from ``watched`` down has a specific, nameable problem. The
  rung summary says which.
* **Which predicate fails most.** Fourteen scattered failures usually
  have one cause, and the tally finds it faster than reading them.
* **Whether the weapons were used at all.** An agent that scores
  identically with an empty rack and a full one has not learned to
  fight, and no pass rate will tell you that.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from sea_of_colours.evals.battles.ladder import BY_ID as RUNG_BY_ID
from sea_of_colours.evals.battles.runner import BattleResult, SuiteResult

_W = 74


def _bar(frac: float, width: int = 18) -> str:
    """A density bar in the same glyphs the board uses for purity."""
    full = int(frac * width)
    part = frac * width - full
    tail = "░▒▓"[min(2, int(part * 3))] if full < width and part > 0.08 else ""
    return ("█" * full + tail).ljust(width, "·")


def _rule(ch: str = "─") -> str:
    return ch * _W


def render(suite: SuiteResult, *, verbose: bool = False) -> str:
    out: list[str] = []
    add = out.append

    add(_rule("═"))
    add(f"  REDSIGN BATTLES · {suite.agent}")
    add(_rule("═"))
    add("")
    add(f"  score   {_bar(suite.score)}  {suite.score:.0%}")
    add(f"  clean   {suite.clean}/{len(suite.battles)} battles passed every run")
    add("")

    # Before anything else, because it invalidates everything else.
    if suite.fallback_rate > 0:
        add("  " + "!" * (_W - 4))
        add(f"  {suite.fallback_rate:.0%} of turns fell back to the harness "
            f"safety net.")
        add("  The model was not reached or its reply would not parse, so "
            "that")
        add("  share of this score belongs to the fallback heuristic and not")
        add("  to your agent. Check credentials before reading anything "
            "below.")
        add("  " + "!" * (_W - 4))
        add("")

    add(_ladder_section(suite))
    add(_diagnosis_section(suite))
    add(_weapons_section(suite))

    hard = sorted(suite.battles, key=lambda b: (b.score, b.board.id))
    worst = [b for b in hard if b.rate < 1.0]
    if worst:
        add(_rule())
        add("  WHAT TO FIX FIRST")
        add(_rule())
        add("")
        for battle in worst[: (len(worst) if verbose else 3)]:
            add(_battle_detail(battle))
    else:
        add("  Every battle clean. Raise the rung or add a loadout.")
        add("")

    return "\n".join(out)


def _ladder_section(suite: SuiteResult) -> str:
    rows = suite.by_rung()
    if len(rows) < 2:
        return ""
    lines = [_rule(), "  THE LADDER", _rule(), ""]
    ordered = sorted(rows.items(), key=lambda kv: RUNG_BY_ID[kv[0]].order)
    stopped: str | None = None
    for rung_id, (clean, total) in ordered:
        frac = clean / total if total else 0.0
        lines.append(
            f"  {rung_id:<9} {_bar(frac, 14)} {clean}/{total}   "
            f"{RUNG_BY_ID[rung_id].summary}"
        )
        if stopped is None and frac < 1.0:
            stopped = rung_id
    lines.append("")
    if stopped is not None:
        lines.append(f"  It stops at {stopped.upper()}, which tests")
        lines.append(f"  {RUNG_BY_ID[stopped].teaches}")
        lines.append("")
    return "\n".join(lines)


def _diagnosis_section(suite: SuiteResult) -> str:
    tally: dict[str, int] = {}
    example: dict[str, str] = {}
    for battle in suite.battles:
        for run in battle.runs:
            for check in run.failures:
                tally[check.name] = tally.get(check.name, 0) + 1
                example.setdefault(check.name, check.detail)
    if not tally:
        return ""
    lines = [_rule(), "  WHAT WENT WRONG, BY FREQUENCY", _rule(), ""]
    for name, n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:5]:
        lines.append(f"  {n:>3}x  {name}")
        lines.append(f"        e.g. {_wrap(example[name], 62, 13)}")
    lines.append("")
    return "\n".join(lines)


def _weapons_section(suite: SuiteResult) -> str:
    """Did an armed agent fight differently from an unarmed one?"""
    armed = [b for b in suite.battles if b.loadout.id != "empty"]
    if not armed:
        # Silence here used to be indistinguishable from "fired nothing",
        # and the default loadout is `empty` — so the commonest run in the
        # kit was quietly saying nothing about the kit's biggest gap.
        return "\n".join([
            _rule(), "  ORDNANCE", _rule(), "",
            "  This run never armed your agent, so it says",
            "  nothing about whether it can fight. Weapons are",
            "  the largest scoring gap in the kit and the default",
            "  loadout does not test them:",
            "",
            "      soc suite --agent <you> --loadout empty,both",
            "",
            "  runs each board twice, once with a full rack, and",
            "  the two scores together are the answer.",
            "",
        ])
    fired = suite.weapons_fired()
    total = sum(fired.values())
    lines = [_rule(), "  ORDNANCE", _rule(), ""]
    if total == 0:
        lines.append(f"  Nothing fired across {len(armed)} armed battles.")
        lines.append("  The rack was full and the plan never changed.")
        lines.append("")
        # Deliberately NOT called a failure. When to spend a charge is
        # doctrine, and doctrine is the attendee's to write — plenty of
        # good agents are told to hold everything for a redsign night.
        # What the suite can honestly say is that the chance existed.
        lines.append("  Whether that is wrong is your call, not the suite's:")
        lines.append("  a charge held for a jackpot night is a real doctrine.")
        lines.append("  But if you did NOT choose silence, the rack is")
        lines.append("  decoration — run `soc weapons --agent <you>` to see")
        lines.append("  which rung it is stuck on.")
    else:
        bits = ", ".join(f"{v}x {k.replace('_', ' ')}" for k, v in
                         sorted(fired.items()) if v)
        lines.append(f"  Fired {bits} across {len(armed)} armed battles.")
        empty = [b for b in suite.battles if b.loadout.id == "empty"]
        if empty:
            a = sum(b.score for b in armed) / len(armed)
            e = sum(b.score for b in empty) / len(empty)
            delta = a - e
            lines.append(
                f"  Armed {a:.0%} vs unarmed {e:.0%} — "
                + ("the weapons are earning their slot."
                   if delta > 0.02 else
                   "no measurable gain from being armed yet.")
            )

    # Per-battle, because an aggregate hides the useful question. "Two
    # salvos across nine battles" reads like weapon use; seeing WHICH
    # seven were silent is what tells you the doctrine has a hole in a
    # particular shape of night.
    rows = [(b, sum((b.weapons_fired or {}).values())) for b in armed]
    if any(n for _b, n in rows):
        lines.append("")
        lines.append("  per battle:")
        for b, n in rows:
            mark = f"{n} fired" if n else "silent"
            lines.append(f"    {mark:<9} {b.battle_id}")
    lines.append("")
    return "\n".join(lines)


def _battle_detail(battle: BattleResult) -> str:
    lines: list[str] = []
    flag = "FLAKY" if battle.flaky else "FAIL"
    lines.append(f"  [{flag}] {battle.battle_id}  "
                 f"{battle.passes}/{len(battle.runs)} runs, "
                 f"{battle.score:.0%} of checks")
    lines.append(f"     {_wrap(battle.board.shape, 66, 5)}")
    lines.append("")
    for name, n in battle.recurring_failures():
        detail = next(
            (c.detail for r in battle.runs for c in r.failures if c.name == name),
            "",
        )
        lines.append(f"     x{n} {name}")
        lines.append(f"        {_wrap(detail, 62, 8)}")
    err = next((r.error for r in battle.runs if r.error), "")
    if err:
        lines.append(f"     ERROR {err}")
    lines.append("")
    lines.append(f"     The canonical: {_wrap(battle.board.canonical, 62, 5)}")
    lines.append("")
    return "\n".join(lines)


def _wrap(text: str, width: int, indent: int) -> str:
    import textwrap

    body = textwrap.fill(" ".join(str(text).split()), width=width)
    pad = " " * indent
    return body.replace("\n", "\n" + pad)


def render_compact(suite: SuiteResult) -> str:
    """One line per battle. For CI logs and league tables."""
    rows = [f"{suite.agent}: {suite.score:.0%} "
            f"({suite.clean}/{len(suite.battles)} clean)"]
    for b in sorted(suite.battles, key=lambda b: b.battle_id):
        mark = "ok  " if b.rate == 1.0 else ("~   " if b.flaky else "FAIL")
        rows.append(f"  {mark} {b.battle_id:<44} {b.score:>4.0%}")
    return "\n".join(rows)


def league_table(
    results: Sequence[SuiteResult],
    rosters: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """Rank agents. Same suite, same rungs, one row each.

    ``rosters`` maps an agent label to the people who built it. Printed
    under the table rather than in it: the ranking is about agents, but
    the day is about the people, and a table nobody can be credited from
    is no use when it is read out at the end.
    """
    ranked = sorted(results, key=lambda r: (-r.score, r.agent))
    lines = [_rule("═"), "  LEAGUE", _rule("═"), ""]
    lines.append(f"  {'#':<3} {'agent':<24} {'score':>6}  {'clean':>7}  ladder")
    lines.append("  " + _rule()[:_W - 2])
    suspect = []
    for i, res in enumerate(ranked, 1):
        rungs = res.by_rung()
        reached = [
            r for r in sorted(rungs, key=lambda k: RUNG_BY_ID[k].order)
            if rungs[r][0] == rungs[r][1]
        ]
        top = reached[-1] if reached else "—"
        # An agent that never reached its model is ranked on its safety
        # net. Flagged rather than dropped: the run still happened, and
        # a silently missing entrant is worse than a marked one.
        mark = " *" if res.fallback_rate > 0 else ""
        if res.fallback_rate > 0:
            suspect.append((res.agent, res.fallback_rate))
        lines.append(
            f"  {i:<3} {res.agent + mark:<24} {res.score:>5.0%}  "
            f"{res.clean:>3}/{len(res.battles):<3}  clean through {top}"
        )
    lines.append("")
    if suspect:
        lines.append("  * scored partly on the fallback heuristic, not the "
                     "agent's own plan:")
        for name, rate in suspect:
            lines.append(f"      {name}: {rate:.0%} of turns never reached "
                         f"a model")
        lines.append("")
    if rosters:
        lines.append("  WHO BUILT THEM")
        for res in ranked:
            people = rosters.get(res.agent)
            if people:
                lines.append(f"      {res.agent:<24} {', '.join(people)}")
        lines.append("")
    return "\n".join(lines)
