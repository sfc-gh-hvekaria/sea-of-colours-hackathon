"""Shared, human-trackable season names for the head-to-head test runners.

The engine auto-generates evocative ``Latin_Noun`` names (``Tempus_Falcon``)
which are pretty but IMPOSSIBLE to tell apart in the replay list — every diag /
versus run looks the same. These helpers build a season name that LEADS with the
technical identity (matchup + seed + days + optional tag), so the left-aligned
replay picker is scannable at a glance, e.g.::

    V9_vs_HEUR_s42_d7                     (1v1)
    V9_vs_V8_vs_V7_s42_d7_phase2          (3-way, tagged)
    V9_vs_V8_vs_V7_s42_d7_phase2_0724-1401   (+ uniqueness stamp)

Use :func:`make_season_name` from a runner and pass the result to
``soc_engine.init_session(season_name=...)``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

# Known agent-label -> short token. Anything else falls back to a cleaned,
# uppercased, prefix-stripped form (see :func:`compact_label`).
_LABEL_ALIASES = {
    "heuristic": "HEUR",
    "red_harvest": "HEUR",
    "red_reaper": "HEUR",
}
_STRIP_PREFIXES = ("tabula_", "soc_red_reaper_", "soc_", "pilot_")


def compact_label(label: str) -> str:
    """Compress an agent label to a short, upper-case token.

    ``tabula_v9`` -> ``V9``; ``heuristic`` / ``red_harvest`` -> ``HEUR``;
    ``pilot_v2`` -> ``PILOTV2``. Unknown labels are stripped of the common
    harness prefixes, upper-cased, reduced to alphanumerics, and capped.
    """
    s = str(label or "").strip().lower()
    if not s:
        return "AGENT"
    if s in _LABEL_ALIASES:
        return _LABEL_ALIASES[s]
    for pfx in _STRIP_PREFIXES:
        if s.startswith(pfx):
            s = s[len(pfx):]
            break
    token = "".join(ch for ch in s if ch.isalnum()).upper()
    return (token or "AGENT")[:10]


def matchup(labels: Sequence[str]) -> str:
    """``["tabula_v9","heuristic"]`` -> ``V9_vs_HEUR`` (any seat count)."""
    toks = [compact_label(x) for x in labels] or ["AGENT"]
    return "_vs_".join(toks)


def _stamp() -> str:
    return datetime.now().strftime("%m%d-%H%M")


def make_season_name(
    labels: Sequence[str],
    seed: int,
    days: Optional[int],
    *,
    tag: Optional[str] = None,
    stamp: bool = True,
    override: Optional[str] = None,
) -> str:
    """Build a scannable season name.

    ``override`` (a ``--name`` CLI value) wins verbatim when given. Otherwise:
    ``<matchup>_s<seed>_d<days>[_<tag>][_<MMDD-HHMM>]``. ``tag`` is sanitized to
    alphanumerics so it stays slug-safe; ``stamp`` appends a compact wall-clock
    time so re-running the SAME matchup does not produce indistinguishable rows.
    """
    if override and override.strip():
        return override.strip()
    parts = [matchup(labels), f"s{int(seed)}"]
    if days is not None:
        parts.append(f"d{int(days)}")
    if tag and tag.strip():
        clean = "".join(ch for ch in tag.strip() if ch.isalnum() or ch in "-.")
        if clean:
            parts.append(clean)
    if stamp:
        parts.append(_stamp())
    return "_".join(parts)
