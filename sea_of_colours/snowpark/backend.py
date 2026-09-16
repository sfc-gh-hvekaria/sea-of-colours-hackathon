"""Backend factory for the snowpark engine layer.

Selects the :class:`SocStore` implementation that the FastAPI proxy and
the agent invoker share:

* ``memory`` — :class:`InMemorySocStore`, state lives in the process.
* ``file``   — :class:`FileSocStore`, one JSON file per season under
  ``SOC_STORE_DIR``; Snowflake-free but shared across processes, so a
  headless bot season and a watcher server can see the same seasons.
* ``snowflake`` — persists into the deployment named by
  :mod:`sea_of_colours.snowpark.naming` (``SOC_DATABASE`` /
  ``SOC_SCHEMA``, defaulting to ``SOC_HACKATHON_DB.SEA_OF_COLOURS``).
* ``multi`` — Snowflake primary + file secondary, merged.

``SOC_BACKEND`` picks one explicitly. **Unset means ``auto``**, which
resolves to ``snowflake`` only when the whole Snowpark path is actually
usable and falls back to ``memory`` otherwise.

v1.14 — that resolution is now only the *default*. Individual games may
pick their own backend in the New Game modal, and
:func:`store_for_session` routes each session to whichever store owns it.
Ask the store, never the :data:`SOC_BACKEND` constant, when the answer
should be about one game.

v1.12 — auto-detect replaces a bare ``snowflake`` default. That
default was the worst first-run bug in the repo: ``snowpark`` ships only
in the *optional* ``requirements-snowflake.txt``, so a by-the-book
``pip install -r requirements.txt && python run_web.py`` booted a
server, rendered the homepage, and died with ``ModuleNotFoundError`` on
the first game action — an install that looks fine right up until it
isn't.

Two rules keep auto-detection honest:

1. **Explicit is strict, auto is forgiving.** ``SOC_BACKEND=snowflake``
   never silently degrades — if it can't be honoured you get an error
   naming the fix. Only ``auto`` falls back, and it says why.
2. **Auto never picks Snowflake under pytest.** Detection keys off a
   config file that exists on any machine that has ever done the
   key-pair setup, and ``init_session`` *wipes the target schema*. A
   developer's own credentials must not turn a test run into a write
   against their account. Tests that genuinely want it set
   ``SOC_BACKEND`` explicitly.

The Snowpark session is created on first use and reused — Snowflake will
auto-suspend the warehouse after the idle interval declared on the
warehouse itself, so we don't need to keepalive / suspend manually
(matches AA4's pattern in
``agent_arena_v4/orchestrator/snowflake_client.py``).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from dataclasses import dataclass
from typing import Optional

from sea_of_colours.snowpark.store import InMemorySocStore, SocStore


VALID_BACKENDS = ("memory", "file", "multi", "snowflake")
_SNOWPARK_MODULES = ("snowflake.snowpark", "cryptography")


@dataclass(frozen=True)
class BackendResolution:
    """How the active backend was chosen, for logging and the UI.

    ``reason`` is a complete sentence a first-timer can act on; ``fix``
    is the single next command when there is one.
    """

    name: str
    requested: str
    reason: str
    fix: str = ""

    @property
    def persists(self) -> bool:
        return self.name in ("snowflake", "multi", "file")

    def summary(self) -> str:
        """One line for the boot banner."""
        from sea_of_colours.snowpark import naming

        if self.name == "snowflake":
            return (
                f"store backend: snowflake "
                f"({naming.database()}.{naming.schema()})"
            )
        if self.name == "multi":
            return "store backend: multi (snowflake + local files)"
        if self.name == "file":
            return (
                "store backend: file "
                f"({os.environ.get('SOC_STORE_DIR', '.soc_sessions')})"
            )
        return "store backend: memory — sessions will not persist"


def snowflake_readiness() -> tuple[bool, str, str]:
    """Can the Snowpark path work? Offline, cheap enough to run at import.

    Deliberately does **not** open a session: that costs seconds and a
    network round trip, and this runs on every boot. It checks the
    things whose absence is unambiguous — missing dependency, missing
    config, missing key file — and leaves genuine connection failures
    (bad key, suspended warehouse, no such schema) to surface later with
    their own messages, which need to stay distinguishable from
    "not set up yet".

    Returns ``(ready, reason, fix)``.
    """
    missing = [m for m in _SNOWPARK_MODULES if importlib.util.find_spec(m) is None]
    if missing:
        return (
            False,
            f"{', '.join(missing)} not installed",
            "pip install -r requirements-snowflake.txt",
        )

    # v1.45 — the standard Snowflake connection store, same as the CLI.
    from sea_of_colours.snowpark import sfconn

    try:
        props, config = sfconn.resolve_source()
    except Exception as exc:  # pragma: no cover - defensive
        return (False, f"could not read Snowflake config: {exc}", "")

    if not props:
        return (
            False,
            config,  # already says what is wrong: missing, or a bad name
            "snow connection add, or see docs/SNOWFLAKE_SETUP.md §2",
        )

    # Key-pair auth, not the PAT. A PAT-only config is the *Cortex* setup
    # (docs §1) and says nothing about wanting persistence — resolving to
    # snowflake off the back of it would surprise everyone who only ever
    # wanted to play against V12.
    key_file = props.get("private_key_file")
    if not key_file:
        return (
            False,
            f"{config} has no private_key_file (key-pair auth not set up)",
            "see docs/SNOWFLAKE_SETUP.md §2",
        )
    if not os.path.exists(os.path.expanduser(key_file)):
        return (
            False,
            f"private_key_file points at {key_file}, which does not exist",
            "",
        )
    return (True, "snowpark deps and key-pair config present", "")


def _resolve(requested: str) -> BackendResolution:
    if requested in VALID_BACKENDS:
        return BackendResolution(
            name=requested,
            requested=requested,
            reason=f"SOC_BACKEND={requested} set explicitly",
        )
    if requested and requested != "auto":
        return BackendResolution(
            name="memory",
            requested=requested,
            reason=(
                f"SOC_BACKEND={requested!r} is not one of "
                f"{', '.join(VALID_BACKENDS)} — falling back to memory"
            ),
            fix=f"unset SOC_BACKEND, or set it to one of {', '.join(VALID_BACKENDS)}",
        )

    # See rule 2 in the module docstring: a developer's own credentials
    # must never make `pytest` write to their Snowflake account.
    if "pytest" in sys.modules:
        return BackendResolution(
            name="memory",
            requested="auto",
            reason="running under pytest — auto never selects a live backend",
        )

    ready, why, fix = snowflake_readiness()
    if ready:
        return BackendResolution(
            name="snowflake", requested="auto", reason=f"auto-detected: {why}",
        )
    return BackendResolution(
        name="memory",
        requested="auto",
        reason=f"auto-detected memory: {why}",
        fix=fix,
    )


_resolution: Optional[BackendResolution] = None


def resolution() -> BackendResolution:
    """The resolved backend, decided once per process."""
    global _resolution
    if _resolution is None:
        _resolution = _resolve(
            os.environ.get("SOC_BACKEND", "auto").strip().lower()
        )
    return _resolution


def _active() -> str:
    return resolution().name


# Back-compat: callers import this as a constant. It now holds the
# *resolved* backend rather than the raw env var, which is what every
# existing comparison actually wanted.
SOC_BACKEND = resolution().name

_lock = threading.Lock()
_memory_store: Optional[InMemorySocStore] = None
_file_store: Optional[SocStore] = None
_snowflake_store: Optional[SocStore] = None
_multi_store: Optional[SocStore] = None
_snowpark_session = None


def _get_memory_store() -> InMemorySocStore:
    global _memory_store
    with _lock:
        if _memory_store is None:
            _memory_store = InMemorySocStore()
        return _memory_store


def _get_file_store() -> SocStore:
    global _file_store
    with _lock:
        if _file_store is None:
            from sea_of_colours.snowpark.file_store import FileSocStore

            _file_store = FileSocStore()
        return _file_store


def _max_connections() -> int:
    """How many Snowflake connections the store may hold open.

    **Defaults to 1 (pool off), because measurement says it does not
    help.** Kept because the mechanism is sound and the finding is
    worth not re-discovering:

    * ``save_session_full`` fans 7 writes across a thread pool sharing
      one session, which looked like connector queuing.
    * With 7 real connections, 7 concurrent ``SELECT 1`` cost 0.32s
      wall against 0.30s for one — perfect parallelism, so connections
      were never the constraint.
    * A turn stayed at ~12.8s against ~13.1s single-session, while
      opening the extra connections cost ~2.8s each.

    The cost is per-statement latency (~0.3s floor, ~1-1.7s for real
    DML) times a statement count that is mostly *sequential by design*.
    Fewer statements is the lever; more connections is not.
    """
    raw = os.environ.get("SOC_SF_CONNECTIONS", "").strip()
    try:
        return max(1, int(raw)) if raw else 1
    except ValueError:
        return 1


def _build_snowpark_session():
    """Create a Snowpark session from the resolved Snowflake connection.

    v1.45 — resolution is the standard connection store (see
    :mod:`sea_of_colours.snowpark.sfconn`), so passing no file is correct
    here rather than lazy.
    """
    # Lazy import so importing this module doesn't require snowpark on
    # the memory path.
    from scripts.deploy_soc_schema import create_snowpark_session

    return create_snowpark_session()


def _get_snowflake_store() -> SocStore:
    global _snowflake_store, _snowpark_session
    with _lock:
        if _snowflake_store is None:
            from sea_of_colours.snowpark.snowpark_store import SnowparkSocStore

            if _snowpark_session is None:
                _snowpark_session = _build_snowpark_session()
            # ``save_session_full`` fans seven writes across a thread
            # pool that all shared this one session, and the connector
            # serialises per connection — so the save was a queue
            # wearing a pool's clothes. Extra connections are opened
            # lazily and only if the fan-out actually contends, so a
            # single-threaded caller still uses exactly one.
            _snowflake_store = SnowparkSocStore(
                _snowpark_session,
                session_factory=_build_snowpark_session,
                max_connections=_max_connections(),
            )
            # v1.43 — optional write-coalescing / read-caching wrapper. Every
            # store call on Snowflake costs ~300ms of request handling before
            # it does any work, so the turn cost is statements x 300ms and the
            # engine issues 11-14 of them, several redundant. On since
            # v1.43; export SOC_BUFFERED_STORE=0 to go back. See
            # buffered_store.py for the durability model and what a crash
            # costs, and docs/SNOWFLAKE_LATENCY_BRIEF.md §T1.1 for the
            # measurement.
            from sea_of_colours.snowpark import buffered_store as _bufmod

            if _bufmod.buffering_enabled():
                _snowflake_store = _bufmod.BufferedSocStore(_snowflake_store)
        return _snowflake_store


def _get_multi_store() -> SocStore:
    """Composite store: Snowflake primary + local file secondary, merged.

    Lets one server surface both your durable Snowflake history *and* the
    offline file-backed bot seasons at once (each tagged with its source).
    The Snowflake session is built lazily through the factory, so a missing
    / unreachable warehouse degrades to local-only instead of failing the
    whole server.
    """
    global _multi_store
    # Build the secondary BEFORE taking ``_lock``: ``_get_file_store()``
    # acquires the same non-reentrant lock, so constructing it while we
    # already hold ``_lock`` would deadlock. The primary is passed as a
    # factory (not called here), so it adds no lock contention.
    secondary = _get_file_store()
    with _lock:
        if _multi_store is None:
            from sea_of_colours.snowpark.multi_store import CompositeSocStore

            _multi_store = CompositeSocStore(
                primary_factory=_get_snowflake_store,
                secondary=secondary,
            )
        return _multi_store


class BackendUnavailable(RuntimeError):
    """An explicitly-requested backend could not be opened.

    Carries the fix command so callers (the boot probe, the FastAPI
    error handler) can show it instead of a raw traceback.
    """

    def __init__(self, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.fix = fix


def get_store() -> SocStore:
    """Return the configured :class:`SocStore` for the current backend."""
    active = _active()
    if active == "snowflake":
        return _get_snowflake_store()
    if active == "file":
        return _get_file_store()
    if active == "multi":
        return _get_multi_store()
    return _get_memory_store()


# ── per-game backend routing (v1.14) ────────────────────────────────────
# The backend used to be a per-*process* decision, which split badly:
# anyone with key-pair credentials — i.e. everyone likely to demo this —
# auto-detected to Snowflake and paid a warehouse round-trip per action
# having never asked for one. The New Game modal now chooses per game and
# this registry remembers which store owns which session.
#
# In-process only, deliberately. A memory session cannot outlive the
# process *by definition*, so a registry that dies with the process loses
# nothing that still exists; ids we don't recognise fall back to the
# process default, which is the backend that wrote every session created
# before this existed.
_session_backends: dict[str, str] = {}
_session_backend_lock = threading.Lock()


def get_store_for(name: str) -> SocStore:
    """The store for an explicitly named backend, ignoring the default.

    Reuses the same singletons as :func:`get_store`, so serving a
    Snowflake game and a memory game from one process costs one Snowpark
    connection, not two.
    """
    key = (name or "").strip().lower()
    if key == "snowflake":
        return _get_snowflake_store()
    if key == "file":
        return _get_file_store()
    if key == "multi":
        return _get_multi_store()
    if key == "memory":
        return _get_memory_store()
    raise ValueError(
        f"unknown backend {name!r} — expected one of {', '.join(VALID_BACKENDS)}"
    )


def register_session_backend(session_id: str, name: str) -> None:
    """Record which backend owns ``session_id`` for the rest of this process."""
    with _session_backend_lock:
        _session_backends[str(session_id)] = (name or "").strip().lower()


def backend_for_session(session_id: str) -> str:
    """The backend owning ``session_id``, or the process default if unknown."""
    with _session_backend_lock:
        return _session_backends.get(str(session_id)) or _active()


def forget_session_backend(session_id: str) -> None:
    """Drop a deleted session's routing entry."""
    with _session_backend_lock:
        _session_backends.pop(str(session_id), None)


def store_for_session(session_id: str) -> SocStore:
    """The store that owns ``session_id``.

    Every engine entry point already takes its store as the first
    argument, so per-game routing needs nothing from the engine — only
    that callers ask this instead of :func:`get_store`.
    """
    return get_store_for(backend_for_session(session_id))


def stores_in_use() -> list[tuple[str, SocStore]]:
    """``(name, store)`` for the default backend plus any other one opened.

    Session listings have to merge across these or the picker shows half
    the games. Extras are included only once something has actually
    opened them, so listing never pays to build a Snowpark connection
    that no game asked for.
    """
    out: list[tuple[str, SocStore]] = []
    default = _active()
    try:
        out.append((default, get_store()))
    except Exception:
        pass
    for name, store in (
        ("memory", _memory_store),
        ("file", _file_store),
        ("snowflake", _snowflake_store),
        ("multi", _multi_store),
    ):
        if store is not None and name != default:
            out.append((name, store))
    return out


def snowpark_session_for(store: Optional[SocStore] = None):
    """The live Snowpark session behind ``store``, or ``None``.

    v1.14 — ask this instead of comparing against :data:`SOC_BACKEND`.
    That constant is frozen at import and, now that the backend is chosen
    per *game*, describes the process rather than the game whose turn is
    being taken. A server that auto-detected Snowflake but is serving a
    memory game would otherwise persist that game's agent memory into the
    account — memory that game can never read back.

    Best-effort by contract: every caller is a persistence side-path that
    must not crash a turn, so an unopenable store reads as "no session".
    """
    try:
        target = store if store is not None else get_store()
    except Exception:
        return None
    return getattr(target, "session", None)


def probe_store() -> BackendResolution:
    """Open the store once at boot so failures land next to the command.

    Previously the Snowpark session was built on first *use*, so a bad
    key or an undeployed schema surfaced as a 500 on whichever API call
    happened to be first — nowhere near the thing the operator had just
    run. Resolving here means the server either starts healthy or tells
    you why on the line after you launched it.

    An explicit ``SOC_BACKEND`` is strict and raises
    :class:`BackendUnavailable`. Auto degrades to memory, because
    refusing to start would strand someone who never asked for
    Snowflake in the first place.
    """
    global _resolution
    res = resolution()
    if res.name == "memory":
        return res
    try:
        get_store()
        return res
    except Exception as exc:
        fix = _fix_for(exc)
        if res.requested != "auto":
            raise BackendUnavailable(
                f"SOC_BACKEND={res.requested} was requested but the store "
                f"could not be opened: {exc}",
                fix=fix,
            ) from exc
        _resolution = BackendResolution(
            name="memory",
            requested="auto",
            reason=f"auto-detected {res.name}, but opening it failed: {exc}",
            fix=fix,
        )
        return _resolution


def _fix_for(exc: Exception) -> str:
    """Map a connection failure to the one command that addresses it.

    These states need distinguishing: "never set up" and "set up but the
    schema isn't deployed" look identical in a traceback and have
    completely different fixes.
    """
    text = str(exc).lower()
    if "config not found" in text or "missing 'private_key_file'" in text:
        return "see docs/SNOWFLAKE_SETUP.md §2 for the sf_config keys"
    if "does not exist" in text and ("schema" in text or "database" in text):
        return "python scripts/deploy_soc_schema.py"
    if "table" in text and "does not exist" in text:
        return "python scripts/deploy_soc_schema.py"
    if "private key" in text or "jwt" in text or "authentication" in text:
        return (
            "check user= / private_key_file= in your sf_config, and that the "
            "public key is registered on the Snowflake user"
        )
    if "warehouse" in text:
        return (
            "check warehouse= in your sf_config; "
            "python scripts/deploy_soc_schema.py creates one if absent"
        )
    if "modulenotfounderror" in text or "no module named" in text:
        return "pip install -r requirements-snowflake.txt"
    return "python scripts/quickstart_check.py"


def reset_for_tests() -> None:
    """Reset module-level singletons. Tests only — never call from app code."""
    global _memory_store, _file_store, _snowflake_store, _multi_store
    global _snowpark_session, _resolution, SOC_BACKEND
    with _session_backend_lock:
        _session_backends.clear()
    with _lock:
        _memory_store = None
        _file_store = None
        _snowflake_store = None
        _multi_store = None
        _snowpark_session = None
        _resolution = None
    # Keep the back-compat constant in step with the re-resolution, or a
    # test that flips SOC_BACKEND reads a stale value.
    SOC_BACKEND = resolution().name
