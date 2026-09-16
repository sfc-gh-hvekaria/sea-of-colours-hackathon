"""Multiplayer seat links resolve to the game, not the title screen (v1.12).

``buildSeatUrl()`` used to inherit the path from whatever URL it was
handed. Same-origin links were fine — you are already on ``/play`` when
you open the invite modal — but LAN links are built from
``fetchLanOrigin()``, which returns a bare origin like
``http://192.168.1.42:8000``. Its path is ``/``: the landing page, which
has no session-loading code. So every invite QR a phone scanned on the
same Wi-Fi opened the title screen, and the README documented that same
broken shape.

Generation is fixed in ``app.js``, but links live in other people's chat
history and on printed QR codes, so ``/`` also redirects. These tests
pin the redirect and the two shapes that must not change.
"""

from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "memory")

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import app

client = TestClient(app)

_APP_JS = Path(__file__).resolve().parents[1] / "server" / "static" / "app.js"


def test_bare_landing_page_is_untouched() -> None:
    """No ``?session=`` means an ordinary visitor, who still gets the title
    screen — the redirect must not swallow the front door."""
    r = client.get("/")
    assert r.status_code == 200
    assert "landing" in r.text.lower()


def test_legacy_seat_link_redirects_into_the_game() -> None:
    r = client.get("/?session=abc123&player=p2", follow_redirects=False)
    assert r.status_code == 307
    dest = r.headers["location"]
    assert dest.startswith("/play?")
    # The seat must survive the hop, or the player lands on a seat picker.
    assert "session=abc123" in dest
    assert "player=p2" in dest


def test_redirect_lands_on_the_command_centre() -> None:
    r = client.get("/?session=abc123&player=p2")
    assert r.status_code == 200
    assert "command centre" in r.text.lower()


def test_play_deep_link_is_served_directly() -> None:
    """The canonical shape should not bounce through a redirect."""
    r = client.get("/play?session=abc123&player=p2", follow_redirects=False)
    assert r.status_code == 200


def test_seat_url_builder_pins_the_play_path() -> None:
    """Guard the generator itself: a regression here silently reintroduces
    broken QR codes, which no server-side test would notice."""
    src = _APP_JS.read_text(encoding="utf-8")
    body = re.search(
        r"function buildSeatUrl\([^)]*\)\s*\{(.*?)\n  \}", src, re.S
    )
    assert body, "buildSeatUrl() not found — did it get renamed?"
    assert 'u.pathname = "/play"' in body.group(1)


@pytest.mark.parametrize("doc", ["README.md", "docs/MULTIPLAYER.md"])
def test_docs_do_not_advertise_the_broken_url(doc: str) -> None:
    """The old shape may be *described* (both docs explain the bug), but it
    must never appear as a bare copyable link."""
    text = (Path(__file__).resolve().parents[1] / doc).read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("http") and "?session=" in stripped:
            assert "/play?session=" in stripped, f"{doc}: stale join URL: {stripped}"
