"""The reader-facing docs are reachable over HTTP (v1.12).

`manual/` and `guide/` were built as self-contained ``file://`` pages and
still work that way — that is deliberate, since the guide has to open
before Python is installed. But nothing served them, so the only way to
share them was "clone the repo and open a folder", and a tunnel host had
no way to hand a newcomer the rules.

These tests pin the URLs *and* the containment: the Markdown route sits
at the URL root, so it is worth proving it cannot be talked into serving
anything outside the small allowlist.
"""

from __future__ import annotations

import os

os.environ.setdefault("SOC_BACKEND", "memory")

import pytest
from fastapi.testclient import TestClient

from server.app import app

client = TestClient(app)


@pytest.mark.parametrize(
    "url",
    [
        "/guide/",
        "/guide/index.html",
        "/manual/",
        "/manual/index.html",
        "/manual/agent.html",
        "/manual/agent.js",
        "/manual/agent.css",
        "/manual/agent-data.js",
        "/manual/manual.js",
        "/manual/manual.css",
    ],
)
def test_document_trees_are_served(url):
    assert client.get(url).status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "/README.md",
        "/RULEBOOK.md",
        "/AGENTS.md",
        "/docs/SNOWFLAKE_SETUP.md",
        "/docs/MULTIPLAYER.md",
    ],
)
def test_markdown_is_served_as_readable_text(url):
    """Not ``text/markdown`` — browsers download that instead of showing it."""
    res = client.get(url)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")


def test_guide_cross_links_resolve():
    """Every ``../`` link in the guide must work over HTTP, not just on disk.

    The guide is the one document written to be read start-to-finish, so
    a dead link in it is worse than a dead link anywhere else.
    """
    import re
    from pathlib import Path

    guide = Path(__file__).resolve().parent.parent / "guide" / "index.html"
    targets = {
        re.sub(r"#.*$", "", m)
        for m in re.findall(r'href="\.\./([^"]+)"', guide.read_text())
    }
    assert targets, "guide has no cross-links — did the file move?"
    for target in sorted(targets):
        res = client.get(f"/{target}")
        assert res.status_code == 200, f"/{target} is dead ({res.status_code})"


@pytest.mark.parametrize(
    "url",
    [
        "/pytest.md",            # not on the allowlist
        "/requirements.md",
        "/etc/passwd.md",
        "/server/app.py.md",
        "/manual/../server/app.py",
        "/docs/../../server/app.py",
        "/sea_of_colours/game/session.py",
    ],
)
def test_nothing_outside_the_allowlist_is_reachable(url):
    assert client.get(url).status_code == 404


def test_landing_page_links_to_the_docs():
    """The URLs are only useful if someone handed the URL can find them."""
    body = client.get("/").text
    for href in ('href="/guide/"', 'href="/manual/"'):
        assert href in body
