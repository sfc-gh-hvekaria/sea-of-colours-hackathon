"""Audit writer for orchestrator_2.

Stamps one row into ``SOC_AGENT_INVOCATION`` per dispatched turn. The
column shape matches what the legacy orchestrator writes so the existing
UI / replay / postmortem queries keep working.

Source: :func:`sea_of_colours.snowpark.engine.save_agent_rationale`.
We delegate to it for backend consistency rather than rolling our own
INSERT — that way row deduplication, day-resolution race handling, and
column-truncation semantics stay in one place.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from sea_of_colours.orchestrator_2.binding_registry import AgentBinding
from sea_of_colours.orchestrator_2.dispatcher import DispatchResult
from sea_of_colours.snowpark import engine as soc_engine


# Soft cap on rationale length — the audit row's column is variant-y
# but the UI gets unhappy past a few KB. Matches the legacy
# orchestrator's ``RATIONALE_CHAR_CAP``.
RATIONALE_CHAR_CAP = 2_000

# Cap on the prompt-excerpt column.
#
# v1.42 — raised from 32,000, which was cutting roughly 80KB out of the
# middle of every card. A V12 think or plan prompt runs to about 115,000
# characters, so the old cap did not store an excerpt of the prompt so
# much as its two ends, and the card could never answer the question it
# exists to answer: what, exactly, was this agent looking at?
#
# The old number was chosen against a "40KB STATE JSON" the prompt has
# long since outgrown, and nothing else was holding it down: the column
# is a Snowflake STRING (16MB), it is not part of the primary key, and
# hybrid tables constrain the width of *indexed* columns rather than
# payload ones. A whole prompt was always within reach.
#
# Kept as a cap rather than removed, because an audit writer should have
# a bound on what a single row can cost and a fork with a runaway prompt
# builder should degrade rather than take the turn down with it. At
# roughly twice the real figure it should not fire in practice — and
# when it does, it now says so in the row itself.
PROMPT_EXCERPT_CHAR_CAP = 262_144

#: How much of the prompt's TAIL to keep if the cap ever does fire.
#:
#: The excerpt used to be a plain head slice, and that quietly threw
#: away the single most useful block in the whole prompt. V12 renders
#: the OPTION MENU last on purpose ("the last thing it reads before it
#: reasons"), so a head slice reliably decapitated it: every saved card
#: could show what the agent *said* but never what it was *offered*,
#: which is exactly the comparison a fork needs.
PROMPT_EXCERPT_TAIL_CHARS = 60_000

#: Loud on purpose. A card that has been cut should read as a card that
#: has been cut rather than as a short prompt: whoever is reading it is
#: usually trying to work out why two agents diverged, and a silent gap
#: in the middle of the evidence is the worst thing to hand them.
_ELISION = (
    "\n\n[!! {n:,} CHARACTERS CUT FROM THE MIDDLE OF THIS PROMPT — the audit "
    "row's cap was reached, so this card is NOT the whole prompt. The turn "
    "lab keeps an uncut copy of every prompt it sends. !!]\n\n"
)


def _truncate(s: str, cap: int) -> str:
    if len(s) <= cap:
        return s
    return s[: cap - 1] + "…"


def _truncate_prompt(s: str, cap: int = PROMPT_EXCERPT_CHAR_CAP) -> str:
    """Fit a prompt into the excerpt column, keeping BOTH ends.

    Generic on purpose: it preserves the tail rather than hunting for a
    named marker, so it keeps working for a fork that renames its menu
    or reorders its closing blocks.
    """
    if len(s) <= cap:
        return s
    tail = min(PROMPT_EXCERPT_TAIL_CHARS, cap // 2)
    marker = _ELISION.format(n=len(s) - cap)
    head = cap - tail - len(marker)
    if head <= 0:
        return s[-cap:]
    return s[:head] + marker + s[-tail:]


def write_invocation(
    *,
    store,
    session_id: str,
    player: str,
    day: int,
    binding: AgentBinding,
    result: DispatchResult,
) -> None:
    """Persist one audit row for the dispatched turn."""
    rationale = _truncate(result.rationale or result.response, RATIONALE_CHAR_CAP)
    agent_id = binding.agent_label or binding.locator

    timings = {
        "elapsed_ms": result.elapsed_ms,
        "wallclock_capped": result.wallclock_capped,
    }
    timings.update(result.extras or {})

    # v0.9.27 — persist the prompt excerpt too. Harnesses that expose
    # the built prompt via ``extras["prompt_excerpt"]`` (V12 does)
    # get their prompt captured in the audit row so we can debug what
    # the agent actually saw. Cap length so a 40KB brief doesn't blow
    # the row up.
    prompt_excerpt: Optional[str] = None
    extras = result.extras or {}
    if isinstance(extras, Mapping):
        raw = extras.get("prompt_excerpt")
        if isinstance(raw, str) and raw:
            prompt_excerpt = _truncate_prompt(raw, PROMPT_EXCERPT_CHAR_CAP)

    # Multi-agent observability: a harness that fires more than one Cortex call
    # per turn (e.g. the v7 split's THINKER + MOVER) can expose each extra call
    # via ``extras["sub_invocations"]``. We persist each as its OWN
    # SOC_AGENT_INVOCATION row (distinct agent_id) BEFORE the primary row, so
    # the reasoning of every sub-agent is visible in the UI / scannable in the
    # audit trail — not just the final move-enforcer. Best-effort: a bad
    # sub-row must never block the primary audit row.
    sub_invocations = []
    if isinstance(extras, Mapping):
        raw_subs = extras.get("sub_invocations")
        if isinstance(raw_subs, (list, tuple)):
            sub_invocations = list(raw_subs)
    for sub in sub_invocations:
        if not isinstance(sub, Mapping):
            continue
        try:
            sub_label = str(sub.get("label") or "SUB_AGENT")
            sub_prompt = sub.get("prompt_excerpt")
            soc_engine.save_agent_rationale(
                store,
                session_id,
                int(day),
                sub_label,
                player,
                _truncate(str(sub.get("rationale") or ""), RATIONALE_CHAR_CAP),
                runtime=str(sub.get("kind") or "sub"),
                prompt_excerpt=(
                    _truncate_prompt(sub_prompt, PROMPT_EXCERPT_CHAR_CAP)
                    if isinstance(sub_prompt, str) and sub_prompt else None
                ),
                tool_calls=[],
                response_text=str(sub.get("response_text") or ""),
                ms_elapsed=int(sub.get("ms_elapsed") or 0),
            )
        except Exception:  # pragma: no cover - a sub-row must not kill the turn
            pass

    try:
        soc_engine.save_agent_rationale(
            store,
            session_id,
            int(day),
            str(agent_id),
            player,
            rationale,
            runtime=binding.kind,
            prompt_excerpt=prompt_excerpt,
            tool_calls=list(result.tool_calls or []),
            response_text=result.response,
            ms_elapsed=int(result.elapsed_ms),
        )
    except Exception:  # pragma: no cover - audit failure must not kill a turn
        pass
