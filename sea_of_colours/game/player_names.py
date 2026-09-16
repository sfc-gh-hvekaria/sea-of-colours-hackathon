"""Deterministic Latin colour + animal names for bot / agent seats.

Human seats keep their seat-colour label (WHITE / YELLOW / MAGENTA /
CYAN); ``red_harvest`` and other non-human agents are given an evocative
``"<Colour> <Animal>"`` handle (e.g. ``"Aureus Vulpes"``) so the
end-of-game screen and scoreboards can name every competitor.

Names are derived from a stable SHA-256 hash of ``(seed, seat)`` so a
replay always reconstructs the same names, and collisions between seats
in one game are perturbed until unique.
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Mapping, Optional, Sequence

#: Latin colour adjectives (masculine nominative). Order is load-bearing
#: for determinism — append only, never reorder, or existing replays
#: would rename their players.
COLOURS: List[str] = [
    "Ruber", "Viridis", "Caeruleus", "Albus", "Niger", "Aureus",
    "Argenteus", "Purpureus", "Fuscus", "Flavus", "Roseus", "Cinereus",
    "Glaucus", "Croceus", "Ferrugineus", "Candidus", "Ater", "Luteus",
]

#: Latin animal nouns. Append-only for the same determinism reason.
ANIMALS: List[str] = [
    "Lupus", "Vulpes", "Corvus", "Ursus", "Aquila", "Felis", "Serpens",
    "Delphinus", "Lynx", "Falco", "Cervus", "Aper", "Leo", "Noctua",
    "Vipera", "Equus", "Taurus", "Canis", "Mustela", "Strix", "Accipiter",
    "Phoenix",
]

#: Seat-colour labels for human players (mirrors the frontend
#: ``NGM_SEAT_LABELS`` map so backend summaries can name humans too).
SEAT_LABEL: Dict[str, str] = {
    "p1": "WHITE",
    "p2": "YELLOW",
    "p3": "MAGENTA",
    "p4": "CYAN",
}


def _hash(seed: int, seat: str, salt: int = 0) -> int:
    raw = f"{int(seed)}:{seat}:{salt}".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest(), 16)


def generate_player_name(seed: int, seat: str, salt: int = 0) -> str:
    """Return a deterministic ``"<Colour> <Animal>"`` name."""
    h = _hash(seed, seat, salt)
    colour = COLOURS[h % len(COLOURS)]
    animal = ANIMALS[(h // len(COLOURS)) % len(ANIMALS)]
    return f"{colour} {animal}"


def generate_player_names(
    seed: int,
    seats: Sequence[str],
    agents: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Build a ``{seat: name}`` map for the **non-human** seats.

    Human seats are omitted (callers fall back to :data:`SEAT_LABEL`).
    Names are perturbed with an incrementing salt if two seats would
    otherwise collide, so every competitor reads distinctly.
    """
    agents = agents or {}
    names: Dict[str, str] = {}
    used: set[str] = set()
    for seat in seats:
        role = str(agents.get(seat, "human")).lower()
        if role == "human":
            continue
        salt = 0
        name = generate_player_name(seed, seat, salt)
        while name in used and salt < 64:
            salt += 1
            name = generate_player_name(seed, seat, salt)
        used.add(name)
        names[seat] = name
    return names


def generate_player_tag(strategy_or_name: str) -> str:
    """Generate a 3-letter tag from a bot strategy name or display name.
    
    v0.9.18 — converts bot agent strategy names (e.g. "red_harvest",
    "SOC_RED_REAPER_GRID_FAST") or Latin display names (e.g. "Aureus Vulpes")
    into compact 3-letter uppercase tags for UI display.
    
    Strategy name patterns:
    - "red_harvest" → "RHV" (initials of words)
    - "SOC_RED_REAPER_GRID_FAST" → "SRG" (skip prefix, take initials)
    - "Aureus Vulpes" → "AUV" (initials)
    
    Fallback: first 3 alphanumeric chars, uppercased.
    """
    s = str(strategy_or_name).strip()
    
    # Strip common prefixes
    for prefix in ("SOC_", "soc_"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    
    # Split on underscores, spaces, or camelCase
    import re
    # Split on _ or space, or insert space before capital letters for camelCase
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', s)
    words = [w for w in re.split(r'[_\s]+', s) if w]
    
    # Try initials first
    initials = "".join(w[0].upper() for w in words if w and w[0].isalnum())
    if len(initials) >= 3:
        return initials[:3]
    
    # For 2-word names like "red_harvest", take first letter + first 2 of second word
    if len(words) == 2 and len(words[1]) >= 2:
        return (words[0][0] + words[1][:2]).upper()
    
    # Fallback: first 3 alnum chars, pad with 'X' if too short
    alnum = "".join(c for c in s if c.isalnum())
    tag = (alnum[:3] if alnum else "BOT").upper()
    # Ensure exactly 3 chars
    return (tag + "XXX")[:3]
