"""``run_web.py --replace`` — taking port 8000 back from an older run (v1.26).

The feature is a kill switch, so the tests are weighted towards what it
must *refuse*. Being unable to restart is an annoyance; killing the wrong
process is someone's unrelated work gone with no undo, and the two live
one branch apart.

Three refusals in particular are load-bearing and each has its own test:
a foreign HTTP server on the port, an unidentifiable listener, and the
user's own shell (which is the parent of every ``--no-reload`` server and
would take the terminal down with it).
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import run_web
from sea_of_colours.snowpark import backend as soc_backend
from server import portguard

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _Stub(threading.Thread):
    """A throwaway HTTP server, so identification is tested for real.

    Mocking the probe would test the mock: the whole question is what a
    socket says back, and the interesting answers (wrong app, no route,
    not HTTP at all) are properties of a real server.
    """

    def __init__(self, body: dict | None, *, status: int = 200):
        super().__init__(daemon=True)
        self.port = _free_port()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — stdlib's spelling
                if outer.body is None:
                    self.send_error(404)
                    return
                payload = json.dumps(outer.body).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_a):
                pass

        self.body = body
        self.status = status
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), H)

    def run(self):
        self.httpd.serve_forever(poll_interval=0.05)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def stub():
    made: list[_Stub] = []

    def make(body, *, status=200):
        s = _Stub(body, status=status)
        s.start()
        made.append(s)
        for _ in range(100):  # it is up once the port stops being bindable
            if not portguard.port_is_free("127.0.0.1", s.port):
                break
            time.sleep(0.02)
        return s

    yield make
    for s in made:
        s.stop()


# ── the flag ─────────────────────────────────────────────────────────
def test_replace_is_off_unless_asked():
    """It kills a process; it can never be the default."""
    assert run_web.build_parser().parse_args([]).replace is False


def test_replace_parses():
    assert run_web.build_parser().parse_args(["--replace"]).replace is True


def test_the_server_says_who_it_is():
    """The whole safety argument rests on this route existing."""
    soc_backend.reset_for_tests()
    from server import app as server_app

    body = TestClient(server_app.app).get("/api/meta/whoami").json()
    assert body["app"] == portguard.APP_IDENT
    assert body["pid"] == os.getpid()
    assert Path(body["root"]) == ROOT


# ── looking at a port ────────────────────────────────────────────────
def test_a_free_port_has_no_occupant():
    assert portguard.identify("127.0.0.1", _free_port()) is None


def test_a_bound_port_is_not_free(stub):
    s = stub({"app": portguard.APP_IDENT, "pid": 1})
    assert portguard.port_is_free("127.0.0.1", s.port) is False


def test_the_wildcard_bind_is_dialled_on_loopback():
    """0.0.0.0 is an address to bind, not one to connect to."""
    assert portguard.probe_host("0.0.0.0") == "127.0.0.1"
    assert portguard.probe_host("::") == "127.0.0.1"
    assert portguard.probe_host("192.168.1.9") == "192.168.1.9"


def test_our_own_server_is_identified_by_its_answer(stub):
    s = stub({"app": portguard.APP_IDENT, "pid": 4242, "root": "/tmp/soc"})
    occ = portguard.identify("127.0.0.1", s.port)
    assert occ is not None
    assert occ.how == "http" and occ.proved
    assert occ.pid == 4242
    assert occ.root == "/tmp/soc"


def test_a_foreign_http_server_is_not_ours(stub):
    """Something answers, but not with our marker — hands off."""
    s = stub({"app": "grafana", "pid": 4242})
    occ = portguard.identify("127.0.0.1", s.port)
    assert occ is not None and occ.how == "other"


def test_a_server_without_the_route_is_not_ours(stub):
    """A 404 is the shape any unrelated web app on 8000 will have."""
    s = stub(None)
    occ = portguard.identify("127.0.0.1", s.port)
    assert occ is not None and occ.how == "other"


def test_a_server_older_than_the_route_is_still_replaceable(stub, monkeypatch):
    """The upgrade path, and the case that matters on the day this ships.

    Every server already running was started before ``/api/meta/whoami``
    existed, so the flag's own evidence is unavailable for exactly the
    process people most want to replace. It 404s like a stranger, and
    only the command-line fallback tells the two apart — without it the
    flag would not work until after the restart it is meant to perform.
    """
    s = stub(None)
    monkeypatch.setattr(portguard, "listening_pids", lambda _p: [31337])
    monkeypatch.setattr(
        portguard, "_cmdline", lambda _p: "python run_web.py --no-reload",
    )
    occ = portguard.identify("127.0.0.1", s.port)
    assert occ is not None
    assert occ.how == "process" and occ.pid == 31337


def test_a_silent_listener_falls_back_to_the_process(monkeypatch):
    """The wedged-server case, and the only place matching is trusted."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        monkeypatch.setattr(portguard, "listening_pids", lambda _p: [31337])
        monkeypatch.setattr(
            portguard, "_cmdline",
            lambda _p: "python run_web.py --no-reload",
        )
        occ = portguard.identify("127.0.0.1", port, timeout=0.4)
    assert occ is not None
    assert occ.how == "process" and occ.pid == 31337
    assert not occ.proved


def test_a_silent_listener_we_do_not_recognise_is_left_alone(monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        monkeypatch.setattr(portguard, "listening_pids", lambda _p: [31337])
        monkeypatch.setattr(portguard, "_cmdline", lambda _p: "postgres -D /db")
        occ = portguard.identify("127.0.0.1", port, timeout=0.4)
    assert occ is not None and occ.how == "other"


def test_an_unidentifiable_listener_is_left_alone(monkeypatch):
    """No lsof, or a listener owned by another user."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        monkeypatch.setattr(portguard, "listening_pids", lambda _p: [])
        occ = portguard.identify("127.0.0.1", port, timeout=0.4)
    assert occ is not None and occ.how == "other"


# ── refusing ─────────────────────────────────────────────────────────
def test_replace_refuses_a_foreign_server_and_signals_nothing(stub, monkeypatch):
    s = stub({"app": "grafana"})
    fired: list[int] = []
    monkeypatch.setattr(portguard, "_signal", lambda pid, sig: fired.append(pid))
    with pytest.raises(portguard.ReplaceRefused, match="not a Sea of Colours"):
        portguard.replace("127.0.0.1", s.port, log=lambda _m: None)
    assert fired == []


def test_replace_is_quiet_when_the_port_is_already_free():
    said: list[str] = []
    assert portguard.replace(
        "127.0.0.1", _free_port(), log=said.append
    ) is False
    assert said == []


def test_replace_will_not_kill_itself(stub):
    """`free the port` must not be satisfiable by suicide."""
    s = stub({"app": portguard.APP_IDENT, "pid": os.getpid()})
    with pytest.raises(portguard.ReplaceRefused, match="this very process"):
        portguard.replace("127.0.0.1", s.port, log=lambda _m: None)


def test_the_launcher_exits_rather_than_starting_on_a_taken_port(monkeypatch):
    """A refusal must not fall through into uvicorn.run."""
    def boom(*_a, **_k):
        raise portguard.ReplaceRefused("nope")

    monkeypatch.setattr(portguard, "replace", boom)
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *_a, **_k: pytest.fail("started anyway"),
    )
    assert run_web.main(["--replace", "--port", str(_free_port())]) == 1


# ── the shell guard ──────────────────────────────────────────────────
def test_the_users_shell_is_never_treated_as_a_supervisor(monkeypatch):
    """The parent of a --no-reload server is the user's shell.

    `supervisor_of` exists for uvicorn's reloader, which respawns its
    worker and so must die first. Reach one level up without a test
    that means something and it closes the user's terminal instead.
    """
    monkeypatch.setattr(portguard, "_ppid", lambda _p: 501)
    monkeypatch.setattr(portguard, "listening_pids", lambda _p: [9999])
    monkeypatch.setattr(portguard, "_cmdline", lambda _p: "-zsh")
    assert portguard.supervisor_of(9999, 8000) is None


def test_a_wrapper_shell_that_names_the_script_is_not_a_supervisor(monkeypatch):
    """The one that made command-line matching insufficient.

    A server launched as ``zsh -c "... python run_web.py ..."`` — which
    is how every IDE task runner, `make` target and CI step does it —
    gives the *shell* a command line containing `run_web.py`. It passes
    every string test we could write and holds no socket, so socket
    ownership is what decides.
    """
    monkeypatch.setattr(portguard, "_ppid", lambda _p: 501)
    monkeypatch.setattr(portguard, "listening_pids", lambda _p: [9999])
    monkeypatch.setattr(
        portguard, "_cmdline",
        lambda _p: '/bin/zsh -c cd /repo && python run_web.py --no-reload',
    )
    assert portguard.supervisor_of(9999, 8000) is None


def test_a_reloader_parent_is_recognised(monkeypatch):
    """It shares the listening socket with its worker — that is the tell."""
    monkeypatch.setattr(portguard, "_ppid", lambda _p: 501)
    monkeypatch.setattr(portguard, "listening_pids", lambda _p: [501, 9999])
    monkeypatch.setattr(
        portguard, "_cmdline", lambda _p: "python run_web.py --reload",
    )
    assert portguard.supervisor_of(9999, 8000) == 501


def test_init_is_never_a_supervisor(monkeypatch):
    monkeypatch.setattr(portguard, "_ppid", lambda _p: 1)
    assert portguard.supervisor_of(9999, 8000) is None


def test_the_supervisor_is_signalled_before_its_worker(monkeypatch):
    """Order is the whole point — a live reloader replaces the worker."""
    order: list[int] = []

    def record(pid, _sig):
        order.append(pid)
        return True

    monkeypatch.setattr(portguard, "supervisor_of", lambda _p, _port: 100)
    monkeypatch.setattr(portguard, "_signal", record)
    monkeypatch.setattr(portguard, "_wait_free", lambda *_a, **_k: True)
    occ = portguard.Occupant(pid=200, root=None, how="http", detail="")
    assert portguard.stop(occ, "127.0.0.1", 8000) is True
    assert order == [100, 200]


def test_both_command_line_halves_of_a_uvicorn_pair_are_recognised():
    assert portguard.looks_like_soc("python run_web.py --no-reload")
    assert portguard.looks_like_soc("... -c from multiprocessing ... server.app")
    assert not portguard.looks_like_soc("-zsh")
    assert not portguard.looks_like_soc("node /usr/local/bin/http-server")


# ── end to end ───────────────────────────────────────────────────────
def test_a_real_server_is_stopped_and_the_port_handed_over():
    """The one test that exercises signal, wait and re-bind together.

    A stand-in rather than the real app: this needs a process that
    answers `/api/meta/whoami` with its own pid and dies on SIGTERM,
    and booting FastAPI (plus a backend) would add seconds and a store
    to a test about process control.
    """
    port = _free_port()
    src = (
        "import http.server, json, os, sys\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(s):\n"
        "        b=json.dumps({'app':'sea_of_colours','pid':os.getpid(),"
        "'root':'/tmp','uptime_s':1}).encode()\n"
        "        s.send_response(200)\n"
        "        s.send_header('Content-Length',str(len(b)))\n"
        "        s.end_headers(); s.wfile.write(b)\n"
        "    def log_message(s,*a): pass\n"
        f"http.server.HTTPServer(('127.0.0.1',{port}),H).serve_forever()\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", src])
    try:
        for _ in range(200):
            if not portguard.port_is_free("127.0.0.1", port):
                break
            time.sleep(0.02)
        assert not portguard.port_is_free("127.0.0.1", port), "stub never bound"

        said: list[str] = []
        assert portguard.replace("127.0.0.1", port, log=said.append) is True
        assert portguard.port_is_free("127.0.0.1", port)
        assert proc.poll() is not None, "the process is still alive"
        assert any(str(proc.pid) in m for m in said), (
            "the launcher must name the pid it stopped"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
