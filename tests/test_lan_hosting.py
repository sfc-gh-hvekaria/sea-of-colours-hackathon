"""LAN hosting and the invite-link reachability report (v1.15).

The multiplayer suite had a silent failure that survived because every
piece of it looked correct in isolation. `run_web.py` — the command every
doc teaches — binds 127.0.0.1. The invite modal separately asks the
server for its LAN IP, gets a perfectly accurate answer, and renders a QR
for it. The phone then gets connection-refused, with nothing on either
screen suggesting why, and multiplayer reads as broken.

Nothing here tests "can a phone connect" (a unit test can't). What it
pins is that the server stops *claiming* an address it isn't serving,
and that the two causes with opposite fixes are told apart.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import run_web
from sea_of_colours.snowpark import backend as soc_backend


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SOC_BACKEND", "memory")
    soc_backend.reset_for_tests()
    from server import app as server_app
    server_app._FIREWALL_CACHE.clear()
    yield TestClient(server_app.app)
    server_app._FIREWALL_CACHE.clear()


# ── run_web.py flags ─────────────────────────────────────────────────
def test_default_stays_loopback_only():
    """Binding the network is an exposure decision; it stays opt-in."""
    host, port, _reload = run_web.resolve([])
    assert host == "127.0.0.1"
    assert port == 8000


def test_lan_binds_every_interface():
    host, _port, _reload = run_web.resolve(["--lan"])
    assert host == "0.0.0.0"


def test_lan_turns_reload_off():
    """A stray file save must not reset a party.

    The cross-player submit lock is a per-process threading.Lock and a
    memory season lives in that process, so a reload mid-game is not a
    hiccup — it's everyone's season gone.
    """
    _host, _port, reload = run_web.resolve(["--lan"])
    assert reload is False


def test_reload_is_on_by_default_for_solo_dev():
    _host, _port, reload = run_web.resolve([])
    assert reload is True


def test_explicit_reload_still_wins_over_lan():
    _host, _port, reload = run_web.resolve(["--lan", "--reload"])
    assert reload is True


def test_port_is_settable():
    """8000 collides constantly; --port was previously ignored."""
    _host, port, _reload = run_web.resolve(["--port", "8123"])
    assert port == 8123


def test_host_and_lan_do_not_both_apply():
    """--lan is a preset, and it must win over a stale --host."""
    host, _port, _reload = run_web.resolve(["--host", "127.0.0.1", "--lan"])
    assert host == "0.0.0.0"


# ── the reachability report ──────────────────────────────────────────
def _lan(client, *, reachable: bool, firewalled=None, ip="192.168.1.50"):
    from server import app as server_app

    server_app._FIREWALL_CACHE.clear()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(server_app, "_lan_ip", lambda: ip)
        mp.setattr(server_app, "_lan_port_open", lambda *_a, **_k: reachable)
        mp.setattr(
            server_app, "_macos_firewall_blocks_incoming", lambda: firewalled
        )
        return client.get("/api/meta/lan").json()


def test_a_bound_and_clear_server_reports_no_problem(client):
    body = _lan(client, reachable=True, firewalled=False)
    assert body["lan_ip"] == "192.168.1.50"
    assert body["lan_reachable"] is True
    assert body["lan_hint"] == ""


def test_loopback_bind_is_reported_with_the_flag_that_fixes_it(client):
    body = _lan(client, reachable=False, firewalled=False)
    assert body["lan_reachable"] is False
    assert "--lan" in body["lan_hint"]


def test_a_blocking_firewall_is_not_told_to_use_the_lan_flag(client):
    """The regression that made the first version of this useless.

    Someone who already ran `--lan` and is blocked by the firewall must
    not be told to run `--lan`. Both causes look identical from the
    phone, so the server has to distinguish them.
    """
    body = _lan(client, reachable=False, firewalled=True)
    assert body["lan_firewalled"] is True
    assert "--lan" not in body["lan_hint"]
    assert "irewall" in body["lan_hint"]


def test_a_reachable_but_firewalled_host_still_gets_a_caution(client):
    """The self-test's blind spot, stated rather than hidden.

    A same-host connection never crosses the macOS filter, so the server
    can answer itself while refusing the phone.
    """
    body = _lan(client, reachable=True, firewalled=True)
    assert body["lan_reachable"] is True
    assert body["lan_hint"], "a known blind spot must still warn"


def test_no_network_is_not_reported_as_a_firewall(client):
    body = _lan(client, reachable=False, firewalled=None, ip=None)
    assert body["lan_ip"] is None
    assert body["lan_reachable"] is False


def test_the_port_reported_is_the_one_being_served(client):
    """The QR encodes host:port — a wrong port is a dead link."""
    body = _lan(client, reachable=True, firewalled=False)
    assert isinstance(body["lan_port"], int)
