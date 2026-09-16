"""Take one recorded turn apart into the three things worth reading.

A turn card as recorded is three walls of text: an 86KB prompt, a 20KB
option menu, and a list of wire moves. All of the information is there
and none of it is legible — which is why the honest reaction to the
first review UI was that it was impossible to parse.

The turn only has three questions in it:

* **what was it told** — the prompt, split at its own section markers
  instead of served as one block;
* **what was it offered** — the menu, parsed back into the options it
  actually is, each with its own yield, walk and risk;
* **what did it do** — the ids it picked, the directive it wrote, and
  the orders that reached the engine.

Everything here is parsing, not judgement. There is deliberately no
score: a percentage invites you to read the number and stop, and the
number is the least reliable thing on the card.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

# ── the prompt ──────────────────────────────────────────────────────

#: A line that opens a new section of the prompt. The prompt is built
#: from blocks that announce themselves, so this is recognition rather
#: than guesswork: `!!! …` advisories, `=== SECTION 1 — … ===` banners,
#: an ALL-CAPS run before a colon, and ALL-CAPS lines with no colon.
_BANNER = re.compile(r"^\s*(?:!!!|={2,})")
_LABEL = re.compile(r"^(\s*)([A-Z][A-Z0-9 _/&()'-]{2,})(:)")


def _is_heading(line: str) -> bool:
    if not line.strip():
        return False
    if _BANNER.match(line):
        return True
    stripped = line.strip()
    if len(stripped) > 90:
        return False
    # An ALL-CAPS line with no lower case at all is a banner heading.
    if re.search(r"[A-Z]", stripped) and not re.search(r"[a-z]", stripped):
        return True
    m = _LABEL.match(line)
    # Only a label at the very start of the line — indented "yield:"
    # style keys inside a block belong to the block, not above it.
    return bool(m and len(m.group(1)) <= 2)


def _heading_text(line: str) -> str:
    stripped = line.strip().strip("=").strip()
    stripped = stripped.strip("!").strip()
    m = _LABEL.match(line)
    if m and not _BANNER.match(line):
        return m.group(2).strip()
    return stripped[:80]


def prompt_sections(prompt: str) -> list[dict]:
    """The prompt as its own sections, in order.

    Anything before the first heading is kept under ``(opening)`` rather
    than dropped — on a setup night that is the advisory that explains
    the whole turn.
    """
    text = str(prompt or "")
    if not text.strip():
        return []
    out: list[dict] = []
    title, body = "(opening)", []

    def flush() -> None:
        if body and "".join(body).strip():
            out.append({
                "title": title,
                "body": "\n".join(body).strip("\n"),
                "lines": len(body),
            })

    for line in text.splitlines():
        if _is_heading(line):
            flush()
            title, body = _heading_text(line), [line]
        else:
            body.append(line)
    flush()
    return out


# ── the option menu ─────────────────────────────────────────────────

_FAMILY = re.compile(r"^ (?=\S)([A-Z][A-Z0-9 &/'-]{2,}?)(?:\s+[—-]|:)")
_OPTION = re.compile(r"^\s{1,4}\[([A-Z][A-Z0-9_]*)\]\s*(.*)$")
_ATTR = re.compile(r"^\s{5,}([A-Za-z][A-Za-z ]*?):\s*(.*)$")
_DOCTRINE = re.compile(r"^\s{2,}\((.*)\)\s*$")


def menu_options(menu: str) -> list[dict]:
    """The option menu parsed back into options.

    The menu reaches the model as one block of text, which is the right
    shape for a model and the wrong shape for a person: the reason an
    option was or was not taken is usually in its ``yield`` or its
    ``WHY``, and those are invisible in the wall.

    Each option keeps the family it was offered under, because the
    family header carries the doctrine — "banks nothing tonight" sits
    above the EMP salvos and explains more about why they go unpicked
    than anything on the options themselves.
    """
    out: list[dict] = []
    family, doctrine = "", ""
    current: dict | None = None

    for line in str(menu or "").splitlines():
        if not line.strip():
            continue

        opt = _OPTION.match(line)
        if opt:
            current = {
                "id": opt.group(1),
                "title": opt.group(2).strip(),
                "family": family,
                "doctrine": doctrine,
                "attrs": {},
            }
            out.append(current)
            continue

        if current is not None:
            attr = _ATTR.match(line)
            if attr:
                current["attrs"][attr.group(1).strip().lower()] = \
                    attr.group(2).strip()
                continue

        fam = _FAMILY.match(line)
        if fam:
            family, doctrine, current = fam.group(1).strip(), "", None
            continue

        doc = _DOCTRINE.match(line)
        if doc and current is None:
            doctrine = doc.group(1).strip()

    return out


def menu_preamble(menu: str) -> list[str]:
    """The budget lines above the first family — what it had to spend."""
    out = []
    for line in str(menu or "").splitlines():
        if _OPTION.match(line) or _FAMILY.match(line):
            break
        if line.strip():
            out.append(line.strip())
    return out


# ── what it actually did ────────────────────────────────────────────

def orders(moves: Sequence[Mapping[str, Any]]) -> list[dict]:
    """The wire moves as numbered hours.

    One order is one hour (RULEBOOK §3.4), so the index is not
    decoration — "which hour did the lift happen" is most of what you
    ask a turn.
    """
    out = []
    for i, m in enumerate(moves or [], 1):
        if not isinstance(m, Mapping):
            continue
        at = m.get("at") or m.get("to")
        out.append({
            "hour": i,
            "verb": str(m.get("a") or m.get("action") or "?"),
            "unit": str(m.get("unit") or m.get("id") or ""),
            "at": list(at) if isinstance(at, (list, tuple)) else None,
            "extra": {
                k: v for k, v in m.items()
                if k not in ("a", "action", "unit", "id", "at", "to")
            },
        })
    return out


def turn(card: Mapping[str, Any], moves: Iterable[Mapping[str, Any]]) -> dict:
    """One recorded turn, taken apart. No scoring, by design."""
    card = card or {}
    menu = str(card.get("options_offered") or "")
    chosen = [str(x) for x in (card.get("options_chosen") or [])]
    options = menu_options(menu)
    picked = {o["id"] for o in options if o["id"] in chosen}

    return {
        "prompt": prompt_sections(card.get("prompt") or ""),
        "budget": menu_preamble(menu),
        "options": [
            dict(o, chosen=o["id"] in chosen, order=(
                chosen.index(o["id"]) + 1 if o["id"] in chosen else 0
            ))
            for o in options
        ],
        "chosen": chosen,
        # An id in the plan with no matching option is worth surfacing:
        # it means the model invented one, which the packager then had
        # to drop.
        "invented": [c for c in chosen if c not in picked],
        "directive": card.get("directive") or "",
        "reasoning": card.get("reasoning") or "",
        "orders": orders(list(moves or [])),
    }
