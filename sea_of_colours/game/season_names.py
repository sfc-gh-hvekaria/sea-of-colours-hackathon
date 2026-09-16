"""Season name generator — Latin word + English noun combinations.

Each season carries a human-friendly name alongside its UUID
``session_id``. Names are deterministic from the seed so the same
``--seed`` always produces the same name (useful for repro and CLI
piping), and the dictionary is large enough (~40 × ~40 ≈ 1.6k pairs)
that collisions across short demo runs are unlikely.

The name is intended for:

- **CLI output** — ``Season Aurora_Falcon (seed=42, id=ab12…)``
- **Watcher frontend** — season picker dropdown labels
- **Replay URLs** — ``/?season=aurora-falcon`` (slug form via
  :func:`season_name_to_slug`)

Naming format: ``<LatinWord>_<EnglishNoun>`` (title case, single
underscore). Both halves are restricted to letters so the slug is
URL-safe.
"""

from __future__ import annotations

import hashlib
from typing import Tuple


# Latin pool — astronomical / elemental / colour / animal vocab so the
# combinations read like ship names or operation codenames. Keep this
# list tasteful and lower-case singular nouns (the generator title-cases
# them). Adding more words is fine but keep the cardinality close to
# powers-of-2 so the modulo distribution stays roughly uniform.
_LATIN_POOL: Tuple[str, ...] = (
    "aurora", "lux", "tempus", "sol", "luna", "stella", "nox", "ignis",
    "aqua", "terra", "ventus", "ferrum", "aurum", "argentum", "corvus",
    "ursus", "vulpes", "lupus", "aquila", "leo", "draco", "serpens",
    "mons", "silva", "nubes", "fulgur", "glacies", "nova", "vetus",
    "alba", "niger", "ruber", "viridis", "aequinox", "solstitium",
    "oceanus", "caelum", "lumen", "umbra", "veritas",
)

# English nouns — short, concrete, slightly poetic. Avoid duplicates of
# the Latin pool ("luna"/"moon", "stella"/"star") so the two halves stay
# semantically distinct when paired.
_ENGLISH_POOL: Tuple[str, ...] = (
    "falcon", "harvester", "vault", "reaper", "drift", "beacon",
    "anchor", "compass", "forge", "lantern", "cipher", "ember",
    "prism", "ledger", "sigil", "raven", "watch", "bastion", "atlas",
    "cradle", "gambit", "tide", "herald", "vector", "threshold",
    "lighthouse", "lattice", "archive", "fissure", "halo", "marrow",
    "helix", "hollow", "mantle", "summit", "talon", "cinder", "echo",
    "spire", "kestrel",
)


def generate_season_name(seed: int, salt: str = "") -> str:
    """Deterministic ``LatinWord_EnglishNoun`` pair from ``seed``.

    ``salt`` lets callers de-collide on the same seed (e.g. CLI runner
    appending a timestamp) without breaking the "same seed = same name"
    contract for plain uses. The hash is SHA-256 truncated to two bytes
    so the index distribution is uniform across the pools.
    """
    digest = hashlib.sha256(f"{int(seed)}:{salt}".encode("utf-8")).digest()
    latin = _LATIN_POOL[digest[0] % len(_LATIN_POOL)].capitalize()
    english = _ENGLISH_POOL[digest[1] % len(_ENGLISH_POOL)].capitalize()
    return f"{latin}_{english}"


def season_name_to_slug(name: str) -> str:
    """Lower-case URL-safe slug of a season name (``Aurora_Falcon`` →
    ``aurora-falcon``)."""
    return name.lower().replace("_", "-")
