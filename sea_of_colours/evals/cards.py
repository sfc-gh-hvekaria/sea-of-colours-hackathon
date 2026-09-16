"""Render an agent's turn as Markdown.

One renderer, two callers, because the same card is wanted in two
places and they hold it in different shapes:

* the server, from a persisted ``SOC_AGENT_INVOCATION`` row, so the
  AGENT tab's download button works for any season anyone has played;
* the headless season runner, from the harness envelope it has in hand,
  which additionally carries the structured extras the invocation row
  has no column for.

:func:`normalise` flattens both into one dict so the renderers only have
to know about the union. There are two, over the same normalised card,
because the audience splits:

* :func:`render` / :func:`render_many` — **Markdown**, for an LLM being
  asked why a turn went wrong. This is the format an attendee pastes
  into their coding agent, so it stays the primary artefact.
* :func:`render_html` — **HTML**, for the person reading it themselves:
  a day rail, section tabs and colour, because a season is ~1,400 lines
  of Markdown and scrolling that to find "day 3, p1, what was it
  offered" is miserable.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Iterable, Mapping

#: Extras worth printing, in the order a reader wants them: what it was
#: told, what it thought, what it chose, what survived.
#:
#: The third element is the fence's language tag. Markdown has no colour
#: of its own, so the only portable way to tell sections apart at a
#: glance is to let the reader's syntax highlighter do it — ``json`` for
#: the structured decisions, ``ini`` because the option menu's ``[GRAB1]``
#: ids highlight as section headers, ``diff`` for anything that is a
#: before/after. A card is ~1,400 lines; without this it is a wall.
_EXTRA_SECTIONS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("Plan directive", ("thinker_directive",), "json"),
    ("Options offered", ("option_menu_block",), "ini"),
    ("Options chosen", ("selected_option_ids",), "json"),
    ("Predicted outcome", ("predicted_outcome",), "json"),
)


#: How V12 opens its option menu. Matched loosely (the sentence after
#: it is long and has been reworded more than once) and treated as a
#: hint, not a contract — a fork is free to render its own menu and
#: simply won't match, which costs it this one section and nothing else.
_MENU_MARKER = "OPTION MENU (SELECT by ID"

#: The menu is followed by the pass's task block, which always opens a
#: new ``=== ... ===`` banner.
_MENU_END = re.compile(r"^=== ", re.M)


def menu_from_prompt(prompt: str) -> str:
    """Recover the option menu from a stored prompt.

    The menu is the most useful block on a card — it is the difference
    between "here is what the agent said" and "here is what it was
    offered and what it passed over" — but it is not persisted as a
    field. ``extras`` only ever exists on a live envelope, and the
    invocation row has no column for it, so a card rebuilt from the
    database has never had one.

    It IS in the prompt, though, which is stored. So carve it back out.
    Returns "" when the prompt does not contain one, which is the
    honest answer for a heuristic seat (it has no menu) and for any
    card saved before the excerpt stopped truncating the menu away.
    """
    if not prompt:
        return ""
    start = prompt.find(_MENU_MARKER)
    if start < 0:
        return ""
    rest = prompt[start:]
    end = _MENU_END.search(rest, 1)
    return rest[: end.start()].strip() if end else rest.strip()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping) or isinstance(value, (list, tuple)):
        return json.dumps(value, indent=1, default=str)
    return str(value)


def _first(src: Mapping[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        val = src.get(name)
        if val not in (None, "", [], {}):
            return val
    return None


def _fence(body: str, lang: str = "") -> list[str]:
    """Fence ``body`` with enough backticks to survive its own content.

    Prompts and model replies routinely contain fenced blocks of their
    own, and a plain three-tick fence around them ends early — the rest
    of the card then renders as prose and the *next* section's heading
    looks like it belongs to the model. Widen the fence past the longest
    run inside instead.
    """
    longest = max((len(m) for m in re.findall(r"`+", body)), default=0)
    ticks = "`" * max(3, longest + 1)
    return [f"{ticks}{lang}", body, ticks]


def _fmt_move(move: Any) -> str:
    """One engine order, readable.

    Moves reach us as ``{"a": "step", "unit": "harvester_p1", "at": [19, 16]}``
    and a card full of raw dicts is unreadable at the exact moment you
    need it — when you are checking what the engine was actually asked
    to do versus what the agent thought it asked for.
    """
    if not isinstance(move, Mapping):
        return _text(move)
    known = {"a", "action", "unit", "id", "at", "to"}
    bits = [str(_first(move, ("a", "action")) or "?")]
    unit = _first(move, ("unit", "id"))
    if unit:
        bits.append(str(unit))
    at = _first(move, ("at", "to"))
    if isinstance(at, (list, tuple)) and len(at) == 2:
        bits.append(f"({at[0]},{at[1]})")
    elif at:
        bits.append(_text(at))
    rest = {k: v for k, v in move.items() if k not in known}
    line = " ".join(bits)
    if rest:
        line += "  " + ", ".join(f"{k}={v}" for k, v in rest.items())
    return line


def _demote_headings(md: str) -> str:
    """Push every heading down one level, ignoring fenced content.

    Only headings *outside* a fence are the card's own. The model writes
    its own markdown — ``# THINK PASS`` — and that lives inside a fence;
    demoting it there would corrupt the block, and leaving it undemoted
    once a fence breaks makes it masquerade as a turn.
    """
    out: list[str] = []
    fence: str | None = None
    for line in md.split("\n"):
        opener = re.match(r"^(`{3,})", line)
        if opener:
            tag = opener.group(1)
            if fence is None:
                fence = tag
            elif line.strip() == fence:
                fence = None
            out.append(line)
            continue
        if fence is None and re.match(r"^#+ ", line):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def normalise(
    row: Mapping[str, Any] | None = None,
    *,
    envelope: Mapping[str, Any] | None = None,
    season: str = "",
    session_id: str = "",
    day: int | None = None,
    player: str = "",
    agent: str = "",
    phase: str = "",
) -> dict:
    """Flatten a store row and/or a harness envelope into one card.

    Both are optional and either can fill a gap in the other: the row
    is what survives in the database, the envelope is what the harness
    actually returned this turn.
    """
    row = row or {}
    env = envelope or {}
    extras = env.get("extras")
    extras = dict(extras) if isinstance(extras, Mapping) else {}

    prompt = (
        _first(row, ("prompt_excerpt", "prompt"))
        or _first(extras, ("thinker_prompt", "plan_prompt"))
        or ""
    )
    # A card rebuilt from the database has no ``extras`` at all — that
    # key only ever exists on a live envelope — so the "Options offered"
    # section was empty for every saved season. The menu is recoverable
    # from the prompt, so fill the gap rather than leave the section
    # permanently blank.
    if not extras.get("option_menu_block"):
        recovered = menu_from_prompt(str(prompt))
        if recovered:
            extras["option_menu_block"] = recovered

    return {
        "season": season,
        "session_id": session_id or row.get("session_id") or "",
        "day": row.get("day") if day is None else day,
        "phase": phase or row.get("phase") or "",
        "player": player or row.get("player") or "",
        "agent": agent or row.get("agent_id") or "",
        "runtime": row.get("runtime") or "",
        "ms_elapsed": row.get("ms_elapsed"),
        "status": row.get("status") or "",
        "rationale": _first(row, ("rationale",))
        or _first(env, ("rationale", "agent_rationale"))
        or "",
        "prompt": prompt,
        "response": _first(row, ("response_text",))
        or _first(extras, ("thinker_reasoning",))
        or _first(env, ("response",))
        or "",
        "tool_calls": row.get("tool_calls") or [],
        "extras": extras,
        # ``orchestrator_2.runtime`` does not copy the harness's top-level
        # ``moves`` into the envelope it returns, so for every LLM seat
        # the only surviving copy is ``extras.final_moves`` — which is why
        # LLM cards showed the reasoning but never the orders it produced.
        # The heuristic runtime does pass ``moves``, hence both spellings.
        "moves": _first(env, ("moves",))
        or _first(extras, ("final_moves",))
        or [],
        # What the harness changed after the model spoke. Empty on a clean
        # turn; when it is not, it is usually the whole explanation for a
        # card whose reasoning looks right and whose result does not.
        "corrections": _first(extras, ("sanitizer_changes",)) or [],
        "fallback_reason": (
            _first(extras, ("fallback_reason",))
            if extras.get("fallback_used")
            else None
        ),
    }


# ── folding a turn's passes back together ───────────────────────────
#
# V12 and its forks take a turn in **passes**, and each pass is saved as
# its own ``SOC_AGENT_INVOCATION`` row: a bounded reasoning pass, a
# decision pass that returns structured JSON, then the deterministic
# packager. Three rows, one turn.
#
# Read row-per-card — which is what the server did until v1.41 — that
# turn appears in the rail three times, identically labelled, and the
# interesting halves never meet: the reasoning is on one card, the
# option ids it chose are on the next, and neither says so. Folding
# them back into one card is the whole difference between "three
# unlabelled entries" and "here is what it thought, then what it
# decided, then what was compiled".

#: Suffixes the harness appends to its agent id, one per pass, mapped to
#: the label and colour the card shows. Order matters: longest first, so
#: ``_THINKER`` is not matched as ``_THINK``.
_PASSES: tuple[tuple[str, str, str, str], ...] = (
    ("_THINKER", "Decision", "chosen", "json"),
    ("_THINK", "Reasoning", "rationale", "md"),
)


def split_pass(agent_id: str) -> tuple[str, str, str, str]:
    """``EMP_HARVEST_TEST_THINKER`` -> base id, label, colour, syntax.

    The packager row carries the bare agent id and so falls through to
    the default — it is the pass that closes a turn.
    """
    up = str(agent_id or "")
    for suffix, label, kind, syntax in _PASSES:
        if up.upper().endswith(suffix):
            return up[: -len(suffix)], label, kind, syntax
    return up, "Packager", "orders", "text"


def group_rows(rows: Iterable[Mapping[str, Any]]) -> list[list[dict]]:
    """Fold consecutive pass rows into one group per turn.

    The packager always speaks last, so a bare agent id closes the
    group. That rule holds for a heuristic seat too — it has only the
    one row, which opens and closes a group of its own — and for an
    orbit turn, where the LLM passes do not run at all.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        row = dict(row)
        base, label, _kind, _syn = split_pass(row.get("agent_id"))
        row["_pass"] = label
        row["_base_agent"] = base
        current.append(row)
        if label == "Packager":
            groups.append(current)
            current = []
    if current:
        # A turn that never reached the packager — the model spoke and
        # something threw. Worth showing, precisely because it broke.
        groups.append(current)
    return groups


def normalise_group(
    rows: list[Mapping[str, Any]],
    *,
    season: str = "",
    session_id: str = "",
) -> dict:
    """One card from one turn's worth of pass rows."""
    last = rows[-1] if rows else {}
    card = normalise(last, season=season, session_id=session_id)
    card["agent"] = last.get("_base_agent") or card["agent"]
    card["ms_elapsed"] = sum(int(r.get("ms_elapsed") or 0) for r in rows) or None

    # The rationale worth showing is the reasoning pass's, not the
    # packager's placeholder string.
    passes: list[dict] = []
    prompts: list[tuple[str, str]] = []
    for row in rows:
        _b, label, kind, syntax = split_pass(row.get("agent_id"))
        reply = _text(row.get("response_text"))
        if reply and not _is_placeholder(reply):
            if syntax == "json":
                reply = _repretty(reply)
            passes.append({
                "label": label, "kind": kind, "syntax": syntax,
                "body": reply, "ms": row.get("ms_elapsed"),
            })
        prompt = _text(row.get("prompt_excerpt") or row.get("prompt"))
        if prompt and prompt not in [p for _, p in prompts]:
            prompts.append((label, prompt))

    # Each pass logs its own rationale, and the reasoning pass's is a
    # fixed marker string. Take the first one that says something.
    card["rationale"] = next(
        (
            r for r in (_text(x.get("rationale")) for x in rows)
            if r and not _is_placeholder(r)
        ),
        "",
    )
    card["passes"] = passes
    card["prompts"] = [{"label": l, "body": b} for l, b in prompts]
    card["prompt"] = prompts[-1][1] if prompts else card.get("prompt") or ""

    # ``normalise`` above only saw the LAST pass row — the packager —
    # and the packager's prompt carries the menu only when it has no
    # recipe to follow. The menu reliably lives in the think and plan
    # prompts, so search every pass rather than just the one that
    # happened to be last.
    if not card["extras"].get("option_menu_block"):
        for _label, body in prompts:
            recovered = menu_from_prompt(body)
            if recovered:
                card["extras"]["option_menu_block"] = recovered
                break
    # The decision pass's JSON is the turn's actual choice; lift the
    # option ids out so the card can show them without the reader
    # parsing a blob.
    card["plan_ids"] = _plan_ids(passes)
    return card


def _repretty(blob: str) -> str:
    """Re-indent a decision pass's JSON, or leave it exactly as it came.

    The model answers on one line, which reads as a single 900-column
    row. Left alone when it will not parse — a malformed decision is
    the most interesting thing on the card and must not be hidden by a
    renderer that quietly failed to format it.
    """
    try:
        return json.dumps(json.loads(blob), indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        return blob


def _is_placeholder(text: str) -> bool:
    """True for a pass's fixed marker, false for its real summary.

    Both are bracketed — ``[think pass — bounded reasoning]`` against
    ``[plan=aggressive: HD1L, PR1] [fallback=False]`` — so length is not
    the tell and using it silently blanks the rationale on any turn
    whose summary happens to be short. The tell is structure: a marker
    is one bracketed phrase, a summary is a run of ``[key=value]``.
    """
    text = text.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return False
    return "=" not in text and "] [" not in text


def _plan_ids(passes: list[dict]) -> list[str]:
    for p in passes:
        if p["syntax"] != "json":
            continue
        try:
            blob = json.loads(p["body"])
        except (ValueError, TypeError):
            continue
        plan = blob.get("plan") if isinstance(blob, Mapping) else None
        if isinstance(plan, (list, tuple)):
            return [str(x) for x in plan]
    return []


def render(card: Mapping[str, Any]) -> str:
    """One turn as Markdown."""
    head = f"{card.get('player') or '?'} — day {card.get('day') or '?'}"
    if card.get("phase"):
        head += f" ({card['phase']})"
    out: list[str] = [f"# {head}", ""]

    facts = [
        ("season", card.get("season")),
        ("session", card.get("session_id")),
        ("agent", card.get("agent")),
        ("runtime", card.get("runtime")),
        ("took", f"{card['ms_elapsed']}ms" if card.get("ms_elapsed") else None),
        ("status", card.get("status")),
    ]
    for key, val in facts:
        if val:
            out.append(f"- **{key}**: {val}")
    out.append("")

    if card.get("rationale"):
        out += ["## Rationale", "", _text(card["rationale"]), ""]

    if card.get("plan_ids"):
        out += [
            "## Plan chosen", "",
            "- " + ", ".join(f"`{p}`" for p in card["plan_ids"]), "",
        ]

    # One heading per pass, so a model being asked "why did it decide
    # that" can see the reasoning and the decision in sequence instead
    # of as two unrelated cards.
    for p in card.get("passes") or []:
        out += [f"## {p['label']}", ""]
        out += _fence(p["body"], {"json": "json", "md": "markdown"}.get(
            p["syntax"], "text"))
        out.append("")

    # ``diff`` so every order renders green: these are the moves that
    # survived to the engine, and they read against the Corrections
    # block below, which shows what did not.
    moves = card.get("moves") or []
    if moves:
        body = "\n".join(
            f"+ {i:2d}. {_fmt_move(m)}" for i, m in enumerate(moves, 1)
        )
        out += [f"## Orders issued ({len(moves)})", ""]
        out += _fence(body, "diff")
        out.append("")

    corrections = card.get("corrections") or []
    if corrections or card.get("fallback_reason"):
        lines = [f"- {_text(c)}" for c in corrections]
        if card.get("fallback_reason"):
            lines.append(f"! FELL BACK: {_text(card['fallback_reason'])}")
        out += [f"## Corrections ({len(corrections)})", ""]
        out += _fence("\n".join(lines), "diff")
        out.append("")

    extras = card.get("extras") or {}
    for title, names, lang in _EXTRA_SECTIONS:
        val = _first(extras, names)
        if val:
            out += [f"## {title}", ""]
            out += _fence(_text(val), lang)
            out.append("")

    if card.get("response") and not card.get("passes"):
        out += ["## What the model said", ""]
        out += _fence(_text(card["response"]), "markdown")
        out.append("")

    if card.get("tool_calls"):
        out += ["## Tool calls", ""]
        out += _fence(_text(card["tool_calls"]), "json")
        out.append("")

    # Last, and fenced: it is the longest thing here by an order of
    # magnitude and almost never the thing you opened the card for.
    prompts = card.get("prompts") or (
        [{"label": "", "body": card["prompt"]}] if card.get("prompt") else []
    )
    for p in prompts:
        label = f" ({p['label']} pass)" if p.get("label") else ""
        out += [f"## The prompt it was given{label}", ""]
        out += _fence(_text(p["body"]), "text")
        out.append("")

    if len(out) <= 3:
        out.append("_This turn recorded no card. A heuristic seat has no "
                   "prompt and no reasoning — only the orders it played._")

    return "\n".join(out).rstrip() + "\n"


def render_many(cards: Iterable[Mapping[str, Any]], *, title: str = "") -> str:
    """A whole season's cards in one file, in play order."""
    cards = list(cards)
    out: list[str] = []
    if title:
        out += [f"# {title}", "", f"{len(cards)} recorded turn(s).", "", "---", ""]
    for card in cards:
        # Every heading demoted one level so the per-turn cards nest
        # under the title instead of competing with it — but only the
        # card's own headings, never the model's markdown inside a fence.
        out.append(_demote_headings(render(card)))
        out += ["", "---", ""]
    return ("\n".join(out)).rstrip() + "\n"


def filename(card: Mapping[str, Any], ext: str = "md") -> str:
    """A sortable, filesystem-safe name for one turn's card.

    The phase is part of the name because a seat plans twice on most
    days — orbit and night — and without it the night card silently
    overwrites the buying card, which is half the game.
    """
    day = card.get("day")
    day_s = f"d{int(day):02d}" if isinstance(day, int) else "dXX"
    seat = "".join(
        c for c in str(card.get("player") or "seat") if c.isalnum()
    ) or "seat"
    phase = "".join(
        c for c in str(card.get("phase") or "") if c.isalnum() or c == "_"
    )
    return f"{day_s}_{seat}{('_' + phase) if phase else ''}.{ext}"


# ── HTML view ───────────────────────────────────────────────────────────
#
# Same cards, different reader. Markdown is what you paste into a model
# when you want help fixing your agent; HTML is what you read yourself
# when you want "day 3, p1, what was it offered". A season is ~1,400
# lines of Markdown and scrolling it to answer that is miserable.
#
# Both come out of :func:`normalise`, so the two views cannot disagree.

#: Colour per section kind. Keys are CSS-safe and used verbatim as class
#: suffixes. The point is that a card is skimmable — orders green because
#: they are what reached the engine, corrections amber because they are
#: what did not.
_KINDS = {
    "rationale": "#d8d3c4",
    "orders": "#5fd77a",
    "corrections": "#e8a33d",
    "directive": "#54c8e8",
    "offered": "#7f9fe0",
    "chosen": "#5fd77a",
    "predicted": "#c08ae0",
    "response": "#b9b3a4",
    "tools": "#54c8e8",
    "prompt": "#7d776a",
}

#: Which extras map to which colour. Keyed by the title in
#: :data:`_EXTRA_SECTIONS` so the two lists cannot drift apart silently.
_EXTRA_KINDS = {
    "Plan directive": "directive",
    "Options offered": "offered",
    "Options chosen": "chosen",
    "Predicted outcome": "predicted",
}


def _html_sections(card: Mapping[str, Any]) -> list[dict]:
    """The card's sections as data, in the order a reader wants them.

    ``syntax`` picks the highlighter on the page. It is chosen here
    rather than sniffed in the browser because here we know what the
    section *is* — a decision pass is JSON even on the turn it came
    back malformed, and highlighting it as JSON is how you see that.
    """
    out: list[dict] = []

    def add(title: str, kind: str, body: Any, syntax: str = "text") -> None:
        text = _text(body).strip()
        if text:
            out.append({
                "title": title, "kind": kind, "body": text,
                "syntax": syntax, "lines": text.count("\n") + 1,
            })

    # The one-line summary the harness logs: option ids, prediction,
    # whether it fell back. Tokenised as prompt text so those ids pick
    # up the same colour they have everywhere else on the card.
    add("Rationale", "rationale", card.get("rationale"), "prompt")

    # The passes, in the order they ran: what it thought, then what it
    # chose. This is the "agent's thinking" the card exists for.
    for p in card.get("passes") or []:
        add(p["label"], p["kind"], p["body"], p["syntax"])

    moves = card.get("moves") or []
    if moves:
        add(
            f"Orders ({len(moves)})", "orders",
            "\n".join(
                f"{i:2d}.  {_fmt_move(m)}" for i, m in enumerate(moves, 1)
            ),
            "orders",
        )

    corrections = card.get("corrections") or []
    if corrections or card.get("fallback_reason"):
        lines = [f"• {_text(c)}" for c in corrections]
        if card.get("fallback_reason"):
            lines.append(f"! FELL BACK: {_text(card['fallback_reason'])}")
        add(f"Corrections ({len(corrections)})", "corrections",
            "\n".join(lines))

    extras = card.get("extras") or {}
    for title, names, lang in _EXTRA_SECTIONS:
        add(title, _EXTRA_KINDS.get(title, "response"), _first(extras, names),
            {"json": "json", "ini": "prompt"}.get(lang, "text"))

    if not card.get("passes"):
        add("Model reply", "response", card.get("response"), "md")
    add("Tool calls", "tools", card.get("tool_calls"), "json")

    # Last: the longest thing here by an order of magnitude and almost
    # never what you opened the card for. One per pass when the passes
    # were given different prompts — that difference is often the bug.
    prompts = card.get("prompts") or (
        [{"label": "", "body": card.get("prompt")}]
        if card.get("prompt") else []
    )
    for p in prompts:
        label = f"Prompt · {p['label']}" if p.get("label") else "Prompt"
        add(label, "prompt", p.get("body"), "prompt")
    return out


def _turn_payload(card: Mapping[str, Any]) -> dict:
    return {
        "day": card.get("day"),
        "player": card.get("player") or "?",
        "phase": card.get("phase") or "",
        "agent": card.get("agent") or "",
        "runtime": card.get("runtime") or "",
        "ms": card.get("ms_elapsed"),
        "status": card.get("status") or "",
        "plan": list(card.get("plan_ids") or []),
        "sections": _html_sections(card),
    }


def render_html(cards: Iterable[Mapping[str, Any]], *, title: str = "") -> str:
    """A whole season's cards as one self-contained page.

    Self-contained on purpose: these are opened straight off disk out of
    ``reports/seasons/…``, so the data is registered by a ``<script>``
    tag and there is no ``fetch`` anywhere. A fetch cannot work over
    ``file://`` — the same rule the battle room lives by.
    """
    cards = list(cards)
    payload = {
        "title": title or "Agent cards",
        "season": next((c.get("season") for c in cards if c.get("season")), ""),
        "session": next(
            (c.get("session_id") for c in cards if c.get("session_id")), ""
        ),
        "turns": [_turn_payload(c) for c in cards],
    }
    # ``</`` would close the script element early and blank the page.
    blob = json.dumps(payload, default=str).replace("</", "<\\/")
    kind_css = "\n".join(
        f"  .k-{k} {{ --accent: {v}; }}" for k, v in _KINDS.items()
    )
    return (
        _HTML_TEMPLATE
        .replace("/*KINDS*/", kind_css)
        .replace("__TITLE__", html.escape(payload["title"]))
        .replace("__DATA__", blob)
    )


_HTML_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root {
    --bg: #0e0f0d; --panel: #161815; --line: #2a2d27;
    --ink: #d8d3c4; --dim: #7d776a; --accent: #5fd77a;
    /* Token palette. One scheme across every section so a number is
       the same colour in the prompt, the decision JSON and the
       orders — the reader learns it once. */
    --t-head: #5fd77a; --t-bang: #e8635a; --t-id: #54c8e8;
    --t-key: #7f9fe0; --t-str: #c3d97a; --t-num: #e0a45f;
    --t-bool: #c08ae0; --t-punc: #6f6a5e; --t-em: #e8d9a0;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    display: grid; grid-template-columns: 268px 1fr; height: 100vh;
  }
/*KINDS*/
  /* ── day rail ── */
  #rail { border-right: 1px solid var(--line); overflow-y: auto; }
  #rail h1 {
    font-size: 12px; letter-spacing: .14em; text-transform: uppercase;
    margin: 0; padding: 14px 12px; color: var(--dim);
    border-bottom: 1px solid var(--line); position: sticky; top: 0;
    background: var(--bg);
  }
  .day { padding: 8px 0 4px; }
  .day > b {
    display: block; padding: 2px 12px; color: var(--dim);
    font-weight: 400; letter-spacing: .1em; font-size: 11px;
  }
  .turn {
    display: block; width: 100%; text-align: left; cursor: pointer;
    background: none; border: 0; border-left: 2px solid transparent;
    color: var(--ink); font: inherit; padding: 5px 12px;
  }
  .turn:hover { background: var(--panel); }
  .turn.on { background: var(--panel); border-left-color: var(--accent); }
  .turn small { color: var(--dim); }
  /* ── main ── */
  #main { overflow-y: auto; display: flex; flex-direction: column; }
  #facts {
    padding: 14px 18px; border-bottom: 1px solid var(--line);
    display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline;
  }
  #facts h2 { margin: 0 10px 0 0; font-size: 15px; }
  .chip {
    border: 1px solid var(--line); border-radius: 2px;
    padding: 1px 7px; color: var(--dim); font-size: 11px;
  }
  #tabs {
    display: flex; gap: 4px; flex-wrap: wrap; padding: 10px 18px;
    border-bottom: 1px solid var(--line); position: sticky; top: 0;
    background: var(--bg); z-index: 2;
  }
  /* Tinted even when inactive: the whole point of the colour is that a
     turn with Corrections on it reads amber before you click anything. */
  .tab {
    cursor: pointer; background: none; font: inherit; color: var(--accent);
    border: 1px solid var(--line); border-radius: 2px; padding: 3px 10px;
    opacity: .6;
  }
  .tab:hover { opacity: 1; }
  .tab.on {
    color: #0e0f0d; background: var(--accent);
    border-color: var(--accent); opacity: 1;
  }
  #body { padding: 0 0 60px; }
  /* ── code view ──
     One grid ROW per source line, rather than a gutter column beside a
     single <pre>. A 32k prompt has lines far wider than the viewport,
     and with one tall pre the numbers drift out of step with the text
     the moment anything wraps. Per-row, a wrapped line pushes its own
     number down with it. */
  .code {
    display: grid; grid-template-columns: auto 1fr;
    background: var(--panel); border-left: 2px solid var(--accent);
    padding: 8px 0;
  }
  .ln {
    text-align: right; padding: 0 10px 0 14px; color: #46433c;
    user-select: none; font-variant-numeric: tabular-nums;
  }
  .li {
    padding: 0 16px 0 12px; white-space: pre-wrap;
    word-break: break-word; overflow-wrap: anywhere;
  }
  .code:hover .ln { color: #5b574e; }
  .t-head { color: var(--t-head); font-weight: 700; }
  .t-bang { color: var(--t-bang); font-weight: 700; }
  .t-id   { color: var(--t-id); }
  .t-key  { color: var(--t-key); }
  .t-str  { color: var(--t-str); }
  .t-num  { color: var(--t-num); }
  .t-bool { color: var(--t-bool); }
  .t-punc { color: var(--t-punc); }
  .t-em   { color: var(--t-em); font-weight: 700; }
  .t-dim  { color: var(--dim); }
  mark { background: #4a4416; color: #ffe98a; border-radius: 2px; }
  /* ── section toolbar ── */
  #bar {
    display: flex; gap: 8px; align-items: center;
    padding: 8px 18px; border-bottom: 1px solid var(--line);
    color: var(--dim); font-size: 11px;
  }
  #bar input {
    background: var(--panel); border: 1px solid var(--line); color: var(--ink);
    font: inherit; padding: 3px 8px; border-radius: 2px; width: 190px;
  }
  #bar input:focus { outline: 1px solid var(--accent); }
  #bar button {
    background: none; border: 1px solid var(--line); color: var(--dim);
    font: inherit; padding: 3px 9px; border-radius: 2px; cursor: pointer;
  }
  #bar button:hover { color: var(--ink); border-color: var(--dim); }
  .grow { flex: 1; }
  /* Plan ids as badges — the turn's actual decision, above the fold. */
  .plan { display: flex; gap: 5px; flex-wrap: wrap; }
  .pill {
    background: #17301d; border: 1px solid #2f6b40; color: #7de095;
    border-radius: 2px; padding: 1px 7px; font-size: 11px;
  }
  .empty { color: var(--dim); padding: 24px 18px; }
</style>
<div id="rail"><h1>__TITLE__</h1><div id="days"></div></div>
<div id="main">
  <div id="facts"></div><div id="tabs"></div><div id="bar"></div>
  <div id="body"></div>
</div>
<script id="cards" type="application/json">__DATA__</script>
<script>
(function () {
  var D = JSON.parse(document.getElementById('cards').textContent);
  var turns = D.turns || [];
  var cur = 0, tab = 0, find = '';

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // ── highlighting ──────────────────────────────────────────────
  //
  // Tokenisers return [text, className] pairs and the caller builds
  // real text nodes from them, so nothing here can inject markup —
  // these strings are model output and a prompt full of angle
  // brackets, and innerHTML on either is how you get a blank page.

  function jsonTok(src) {
    var out = [], re = /("(?:\\\\.|[^"\\\\])*")(\\s*:)?|\\b(true|false|null)\\b|(-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)|([{}\\[\\],])/g;
    var last = 0, m;
    while ((m = re.exec(src))) {
      if (m.index > last) out.push([src.slice(last, m.index), '']);
      if (m[1]) {
        out.push([m[1], m[2] ? 't-key' : 't-str']);
        if (m[2]) out.push([m[2], 't-punc']);
      } else if (m[3]) out.push([m[3], 't-bool']);
      else if (m[4]) out.push([m[4], 't-num']);
      else out.push([m[5], 't-punc']);
      last = re.lastIndex;
    }
    if (last < src.length) out.push([src.slice(last), '']);
    return out;
  }

  // Inline tokens shared by the prompt and the orders view: option ids
  // (GRAB1, HD1L, BL2), grid coordinates, bare numbers, quoted text.
  function inlineTok(line, out) {
    var re = /\\b([A-Z]{2,}[0-9]+[A-Z]*)\\b|(\\((?:\\s*\\d+\\s*,\\s*\\d+\\s*)\\))|("(?:\\\\.|[^"\\\\])*")|\\b(\\d+(?:\\.\\d+)?)\\b/g;
    var last = 0, m;
    while ((m = re.exec(line))) {
      if (m.index > last) out.push([line.slice(last, m.index), '']);
      if (m[1]) out.push([m[1], 't-id']);
      else if (m[2]) out.push([m[2], 't-num']);
      else if (m[3]) out.push([m[3], 't-str']);
      else out.push([m[4], 't-num']);
      last = re.lastIndex;
    }
    if (last < line.length) out.push([line.slice(last), '']);
  }

  function promptTok(src) {
    var out = [];
    src.split('\\n').forEach(function (line, i) {
      if (i) out.push(['\\n', '']);
      if (/^\\s*!!!/.test(line)) { out.push([line, 't-bang']); return; }
      if (/^\\s*={2,}/.test(line)) { out.push([line, 't-head']); return; }
      // A line with letters but no lower case is a banner heading —
      // "SEA OF COLOURS — ENGINE RULES", which carries no colon.
      if (/[A-Z]/.test(line) && !/[a-z]/.test(line) && line.trim().length > 2) {
        out.push([line, 't-head']);
        return;
      }
      // Otherwise a leading ALL-CAPS run is a section label. Anchored
      // on what FOLLOWS it — a colon or an opening paren — so that a
      // sentence merely starting in caps ("A COLD drop is …") is left
      // as prose.
      var h = line.match(/^(\\s*)([A-Z][A-Z0-9 _\\/&-]{3,}(?=[:(]))/);
      if (h) {
        out.push([h[1], '']);
        out.push([h[2], 't-head']);
        inlineTok(line.slice(h[0].length), out);
        return;
      }
      if (/^\\s*[-*•]\\s/.test(line)) {
        var b = line.match(/^(\\s*[-*•]\\s)([\\s\\S]*)$/);
        out.push([b[1], 't-punc']);
        inlineTok(b[2], out);
        return;
      }
      inlineTok(line, out);
    });
    return out;
  }

  function mdTok(src) {
    var out = [];
    src.split('\\n').forEach(function (line, i) {
      if (i) out.push(['\\n', '']);
      if (/^#{1,6}\\s/.test(line)) { out.push([line, 't-head']); return; }
      var re = /(\\*\\*[^*]+\\*\\*)|(`[^`]+`)|\\b([A-Z]{2,}[0-9]+[A-Z]*)\\b|\\b(\\d+(?:\\.\\d+)?)\\b/g;
      var last = 0, m;
      while ((m = re.exec(line))) {
        if (m.index > last) out.push([line.slice(last, m.index), '']);
        if (m[1]) out.push([m[1], 't-em']);
        else if (m[2]) out.push([m[2], 't-str']);
        else if (m[3]) out.push([m[3], 't-id']);
        else out.push([m[4], 't-num']);
        last = re.lastIndex;
      }
      if (last < line.length) out.push([line.slice(last), '']);
    });
    return out;
  }

  function ordersTok(src) {
    var out = [];
    src.split('\\n').forEach(function (line, i) {
      if (i) out.push(['\\n', '']);
      var m = line.match(/^(\\s*\\d+\\.\\s+)(\\S+)([\\s\\S]*)$/);
      if (m) {
        out.push([m[1], 't-punc']);
        out.push([m[2], 't-head']);
        inlineTok(m[3], out);
      } else inlineTok(line, out);
    });
    return out;
  }

  var TOK = { json: jsonTok, prompt: promptTok, md: mdTok, orders: ordersTok };

  function span(text, cls) {
    if (!cls) return document.createTextNode(text);
    return el('span', cls, text);
  }

  // Append one token's text, splitting the find term out of it so the
  // highlight survives tokenising instead of fighting it.
  function emit(line, text, cls, low) {
    if (!low) { line.appendChild(span(text, cls)); return; }
    var rest = text, at;
    while ((at = rest.toLowerCase().indexOf(low)) !== -1) {
      if (at) line.appendChild(span(rest.slice(0, at), cls));
      line.appendChild(el('mark', cls, rest.substr(at, low.length)));
      rest = rest.slice(at + low.length);
    }
    if (rest) line.appendChild(span(rest, cls));
  }

  function paint(wrap, text, syntax, needle) {
    var toks = (TOK[syntax] || function (s) { return [[s, '']]; })(text);
    var low = needle ? needle.toLowerCase() : '';
    var n = 0, line = null;

    function newline() {
      n++;
      wrap.appendChild(el('div', 'ln', String(n)));
      line = el('div', 'li');
      wrap.appendChild(line);
    }
    newline();

    toks.forEach(function (t) {
      var parts = String(t[0]).split('\\n');
      for (var i = 0; i < parts.length; i++) {
        if (i) newline();
        if (parts[i]) emit(line, parts[i], t[1], low);
      }
    });
    return n;
  }

  // Rail: grouped by day, one button per seat/phase within it. That
  // grouping IS the navigation — a seat plans twice most days.
  function buildRail() {
    var days = document.getElementById('days'), seen = {};
    turns.forEach(function (t, i) {
      var key = String(t.day);
      if (!seen[key]) {
        var g = el('div', 'day');
        g.appendChild(el('b', null, 'DAY ' + (t.day == null ? '?' : t.day)));
        days.appendChild(g);
        seen[key] = g;
      }
      var b = el('button', 'turn');
      b.appendChild(document.createTextNode(t.player + ' '));
      // Without the phase — which pre-v1.41 rows do not carry — a seat
      // that planned twice showed as two identical buttons. Fall back
      // to the agent and the plan it chose, which do distinguish them.
      var note = t.phase || (t.plan || []).join(' ') || t.agent || '';
      b.appendChild(el('small', null, note));
      if (t.status && t.status !== 'ok') {
        b.appendChild(el('small', 't-bang', '  ' + t.status));
      }
      b.onclick = function () { cur = i; tab = 0; find = ''; draw(); };
      b.dataset.i = i;
      seen[key].appendChild(b);
    });
  }

  function draw() {
    var t = turns[cur];
    Array.prototype.forEach.call(
      document.querySelectorAll('.turn'),
      function (b) { b.classList.toggle('on', +b.dataset.i === cur); }
    );

    var facts = document.getElementById('facts');
    facts.textContent = '';
    if (!t) { facts.appendChild(el('h2', null, 'No turns recorded')); return; }
    facts.appendChild(el('h2', null,
      t.player + ' — day ' + t.day + (t.phase ? ' · ' + t.phase : '')));
    [t.agent, t.runtime, t.ms ? t.ms + 'ms' : '', t.status, D.season]
      .filter(Boolean)
      .forEach(function (v) { facts.appendChild(el('span', 'chip', v)); });

    if (t.plan && t.plan.length) {
      var plan = el('span', 'plan');
      t.plan.forEach(function (p) { plan.appendChild(el('span', 'pill', p)); });
      facts.appendChild(plan);
    }

    var tabs = document.getElementById('tabs'),
        bar = document.getElementById('bar'),
        body = document.getElementById('body');
    tabs.textContent = ''; body.textContent = ''; bar.textContent = '';
    var secs = t.sections || [];
    if (!secs.length) {
      body.appendChild(el('div', 'empty',
        'This turn recorded no card. A heuristic seat has no prompt and ' +
        'no reasoning — only the orders it played.'));
      return;
    }
    if (tab >= secs.length) tab = 0;
    secs.forEach(function (s, i) {
      var b = el('button', 'tab k-' + s.kind + (i === tab ? ' on' : ''), s.title);
      b.onclick = function () { tab = i; find = ''; draw(); };
      tabs.appendChild(b);
    });

    var sec = secs[tab];
    bar.appendChild(el('span', null, sec.lines + ' lines'));
    var box = el('input');
    box.placeholder = 'find in section';
    box.value = find;
    // Re-tokenising 32k of prompt on every keystroke is imperceptible
    // and much simpler than an incremental overlay.
    box.oninput = function () { find = box.value; render(); box.focus(); };
    bar.appendChild(box);
    bar.appendChild(el('span', 'grow'));
    var copy = el('button', null, 'copy');
    copy.onclick = function () {
      navigator.clipboard.writeText(sec.body).then(function () {
        copy.textContent = 'copied'; setTimeout(function () {
          copy.textContent = 'copy';
        }, 900);
      });
    };
    bar.appendChild(copy);

    function render() {
      body.textContent = '';
      var wrap = el('div', 'code k-' + sec.kind);
      paint(wrap, sec.body, sec.syntax, find);
      body.appendChild(wrap);
    }
    render();
  }

  // j/k step through turns; the whole point is fast comparison of the
  // same section across consecutive turns, so the tab is kept.
  document.addEventListener('keydown', function (e) {
    if (e.key === 'j' && cur < turns.length - 1) { cur++; draw(); }
    if (e.key === 'k' && cur > 0) { cur--; draw(); }
  });

  buildRail();
  draw();
})();
</script>
"""
