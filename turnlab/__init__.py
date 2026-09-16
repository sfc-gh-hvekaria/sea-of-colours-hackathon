"""Turn Lab — one frozen night, opened in the real game interface.

A board here is a **snapshot**: a complete, inert session the engine
wrote just before a seat planned. Pick one on ``/lab``, press go, and
the lab clones it and hands the clone to the ordinary game UI. From
there it is the real interface — the real board, the real fog toggles,
the real hour transport, the real replay — sitting on a night that
already happened, with nobody's orders in yet. Invoke an agent into a
seat and watch it plan; invoke both and the engine resolves the night.

The lab renders nothing and simulates nothing. Two earlier versions of
it did, and both were wrong in the way a second implementation always
eventually is — the worst of them drew an hour strip showing the two
seats acting in sequence, when a night resolves them together. If you
are ever tempted to draw a game in here, open the game instead.

Everything it owns is in this folder, including its data. See
:mod:`turnlab.store` for why it does not use ``SOC_BACKEND``, and
README.md for the layout.
"""

from __future__ import annotations

from . import boards, cast, isolation, mint, readonly, rewalk, store, turn

__all__ = [
    "boards", "cast", "isolation", "mint", "readonly", "rewalk", "store",
    "turn",
]
