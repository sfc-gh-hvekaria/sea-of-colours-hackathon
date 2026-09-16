"""Packing a fork into one file, and unpacking it again.

v1.43. Two people on a team improving one agent had no way to hand it
between them. ``soc push`` publishes to your GitHub fork, which is the
move that puts you in the league and far too heavy for "try this, does
it still crash on Vetus Lantern?" — it commits, and it makes a
half-finished idea part of what gets collected. Git is the other obvious
answer and it is worse: each attendee is on their own fork, so a
teammate's work is a remote you do not have, most are driving this
through a coding assistant, and "add my laptop as a remote" is not a
thing to be doing at hour four.

A fork is already the right unit. It is one self-contained directory
whose entire footprint is that directory (see :mod:`agent_manifest` for
why), so passing one is copying a folder — and this is that, as a single
file you can drop in a chat window.

**Why a text envelope rather than a tarball.** ``head -8`` on a parcel
tells you who made it, which agent it is and how many files it contains,
before you extract anything. That matters because grabbing a fork means
running somebody else's Python, and the decision to do that should be
possible to make while looking at the thing. It also survives the
transports a room actually uses — chat, email, a pasted gist — none of
which are reliably kind to binaries.

**Why the allowlist.** ``scripts/new_agent.py`` copies only ``.py`` and
``.md`` when it mints a fork, so a fork is Python and prose and nothing
else. Stating that here turns an accident of the minter into a property
of the format: a parcel cannot carry an executable, a symlink, a device
node or a path with ``..`` in it, and unpacking cannot write outside the
one directory it declares. That is not a substitute for trusting the
sender — the ``.py`` files run — but it does mean a parcel cannot touch
the engine, your other forks, or anything outside the repo.

**Reproducible bytes.** mtimes, uids and modes are normalised, so
packing the same fork twice gives the same checksum. Two people can
compare eight characters to settle "are we on the same version" without
either of them extracting anything.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import shutil
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import agent_manifest

MAGIC = "SOC-FORK"
VERSION = 1
SUFFIX = ".socfork"

#: What a fork is made of. See the module docstring — this mirrors what
#: the minter copies, plus ``.json`` because the manifest has to travel.
ALLOWED_SUFFIXES = frozenset({".py", ".md", ".json", ".txt"})

#: Build litter. Shipping ``__pycache__`` would bloat a parcel tenfold
#: (2.9M against 300K for one real fork) and hand the receiver stale
#: bytecode compiled against a different Python.
SKIP_DIR_NAMES = frozenset({
    "__pycache__", ".git", ".pytest_cache", ".mypy_cache",
    ".ipynb_checkpoints", ".venv", "node_modules",
})

#: Caps, so a malformed or hostile parcel fails instead of filling a
#: disk. A real fork is ~50 files and ~300KB; these are far above that.
MAX_FILES = 500
MAX_UNPACKED_BYTES = 16 * 1024 * 1024

_SEPARATOR = "---"
_B64_LINE = 76


class ParcelError(ValueError):
    """A parcel cannot be made, read or trusted.

    Every message names the fix, because — as with :mod:`agent_manifest`
    — the reader is often an assistant with no other context.
    """


# ── packing ───────────────────────────────────────────────────────────


def files_in(directory: Path) -> list[Path]:
    """Every shippable file in a fork, sorted, relative to it."""
    out: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(directory)
        if SKIP_DIR_NAMES.intersection(rel.parts):
            continue
        if path.suffix not in ALLOWED_SUFFIXES:
            continue
        out.append(rel)
    return out


def pack(manifest: agent_manifest.AgentManifest) -> str:
    """Render one fork as a parcel. Returns the file's text."""
    directory = manifest.directory
    members = files_in(directory)
    if not members:
        raise ParcelError(
            f"{directory} has nothing to send — no .py or .md files were "
            f"found. Is this the right agent?"
        )
    if len(members) > MAX_FILES:
        raise ParcelError(
            f"{directory} holds {len(members)} files, more than the "
            f"{MAX_FILES} a parcel carries. A fork should be one directory "
            f"of Python; check for data or output that belongs elsewhere."
        )

    raw = io.BytesIO()
    # mtime=0 on the gzip header as well as on every member, or the
    # checksum changes every time you pack and comparing them is useless.
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for rel in members:
                data = (directory / rel).read_bytes()
                info = tarfile.TarInfo(rel.as_posix())
                info.size = len(data)
                info.mtime = 0
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                tar.addfile(info, io.BytesIO(data))

    blob = raw.getvalue()
    header = {
        "agent": manifest.label,
        "team": manifest.team,
        "name": manifest.name,
        "participants": ", ".join(manifest.participants),
        "files": str(len(members)),
        "bytes": str(len(blob)),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "packed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    body = base64.b64encode(blob).decode("ascii")
    lines = [f"{MAGIC} {VERSION}"]
    lines += [f"{k}: {v}" for k, v in header.items()]
    lines.append(_SEPARATOR)
    lines += [body[i:i + _B64_LINE] for i in range(0, len(body), _B64_LINE)]
    return "\n".join(lines) + "\n"


# ── reading ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Parcel:
    """A parcel that has been read and checksummed, but not installed."""

    header: dict[str, str]
    blob: bytes

    @property
    def label(self) -> str:
        return self.header.get("agent", "")

    @property
    def fingerprint(self) -> str:
        """First eight of the checksum — enough to compare out loud."""
        return self.header.get("sha256", "")[:8]

    @property
    def participants(self) -> str:
        return self.header.get("participants", "")


def read(text: str) -> Parcel:
    """Parse and verify a parcel. Nothing is written."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith(MAGIC):
        raise ParcelError(
            f"this is not a {SUFFIX} parcel — it should begin "
            f"{MAGIC!r}. If it came through a chat client, make sure you "
            f"saved the file rather than copying the preview."
        )
    try:
        version = int(lines[0].split()[1])
    except (IndexError, ValueError):
        raise ParcelError(
            f"cannot read the format version from {lines[0]!r}"
        ) from None
    if version > VERSION:
        raise ParcelError(
            f"this parcel is format {version} and this repo understands "
            f"{VERSION}. Whoever sent it is on a newer build — git pull."
        )

    header: dict[str, str] = {}
    body_at = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == _SEPARATOR:
            body_at = i + 1
            break
        key, _, value = line.partition(":")
        if key.strip():
            header[key.strip()] = value.strip()
    if body_at is None:
        raise ParcelError(
            f"the parcel has no {_SEPARATOR!r} line, so its header and "
            f"its contents cannot be told apart. It is probably truncated."
        )

    try:
        blob = base64.b64decode("".join(lines[body_at:]), validate=True)
    except Exception as exc:
        raise ParcelError(
            f"the parcel's contents are not valid base64 ({exc}). This "
            f"usually means it was pasted rather than sent as a file, and "
            f"something re-wrapped the lines."
        ) from None

    want = header.get("sha256", "")
    got = hashlib.sha256(blob).hexdigest()
    if want and want != got:
        raise ParcelError(
            f"checksum mismatch — the parcel says {want[:8]} and its "
            f"contents are {got[:8]}. It was damaged in transit; ask for "
            f"it again."
        )
    if not header.get("agent"):
        raise ParcelError("the parcel does not say which agent it holds.")
    return Parcel(header=header, blob=blob)


# ── installing ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Installed:
    label: str
    directory: Path
    files: int
    replaced: bool
    renamed_from: Optional[str] = None


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Every member, having refused anything that is not a plain file.

    The checks are what make "unpacking writes only inside the fork's own
    directory" true rather than hoped-for: no absolute paths, no ``..``,
    no links of either kind, no devices, nothing outside the allowlist.
    Python 3.14 does this by default via ``filter='data'``, but the room
    will not all be on 3.14.
    """
    out: list[tarfile.TarInfo] = []
    total = 0
    for info in tar.getmembers():
        if info.isdir():
            continue
        name = info.name
        if not info.isfile():
            raise ParcelError(
                f"{name!r} in the parcel is not a plain file (it is a link "
                f"or a device). Parcels carry source, nothing else."
            )
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or name.startswith("/"):
            raise ParcelError(
                f"{name!r} in the parcel points outside the fork. "
                f"Refusing to unpack it."
            )
        if SKIP_DIR_NAMES.intersection(path.parts):
            continue
        if path.suffix not in ALLOWED_SUFFIXES:
            raise ParcelError(
                f"{name!r} in the parcel is not source or prose. A fork is "
                f"{', '.join(sorted(ALLOWED_SUFFIXES))} and nothing else."
            )
        total += info.size
        if total > MAX_UNPACKED_BYTES or len(out) >= MAX_FILES:
            raise ParcelError(
                "the parcel unpacks to more than a fork should be. "
                "Refusing it."
            )
        out.append(info)
    if not out:
        raise ParcelError("the parcel is empty.")
    return out


def _rewrite(text: str, old: str, new: str) -> str:
    """Repoint a fork's self-references at its new label.

    The same two substitutions the minter makes, for the same two
    reasons: every module imports itself by absolute dotted path, and
    the uppercase form is the agent's identity in the audit trail and
    the namespace for its env toggles. See ``new_agent._copy_harness``.
    """
    return text.replace(old, new).replace(old.upper(), new.upper())


def _remanifest(text: str, team: str, name: str) -> str:
    """Set team and name in an ``agent.json`` being renamed on arrival.

    The label never appears literally in a manifest — it is derived from
    the two parts — so the blanket rewrite above cannot reach it, and a
    manifest still naming the sender's team fails discovery with a
    directory-mismatch error that reads like a bug in the tool.
    """
    data = json.loads(text)
    data["team"] = team
    data["name"] = name
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def install(
    parcel: Parcel,
    *,
    root: Optional[Path] = None,
    force: bool = False,
    rename: Optional[tuple[str, str]] = None,
) -> Installed:
    """Unpack a parcel into the harness root.

    Extracts to a hidden staging directory and swaps it in only once the
    manifest loads, so a parcel that turns out to be unusable leaves
    nothing behind — and a ``--force`` over a working fork cannot half
    replace it. Discovery skips dot-directories, so even a crash midway
    leaves the staging copy invisible rather than half-registered.
    """
    base = root or agent_manifest.harness_root()
    sent = parcel.label
    if rename:
        team, name = rename
        label = f"{team}_{name}"
    else:
        team = parcel.header.get("team", "")
        name = parcel.header.get("name", "")
        label = sent

    dest = base / label
    if dest.exists() and not force:
        raise ParcelError(
            f"you already have an agent called {label!r}. Use --force to "
            f"replace it with this parcel (your copy is not backed up — "
            f"commit first if you want it), or --as-team/--as-name to "
            f"install this one alongside under a different label."
        )

    staging = base / f".socgrab_{label}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        with tarfile.open(fileobj=io.BytesIO(parcel.blob), mode="r:gz") as tar:
            members = _safe_members(tar)
            for info in members:
                handle = tar.extractfile(info)
                if handle is None:  # pragma: no cover — defensive
                    raise ParcelError(f"{info.name!r} could not be read.")
                out = staging / info.name
                out.parent.mkdir(parents=True, exist_ok=True)
                raw = handle.read()
                if rename and info.name == agent_manifest.MANIFEST_NAME:
                    raw = _remanifest(
                        _rewrite(raw.decode("utf-8"), sent, label), team, name
                    ).encode("utf-8")
                elif rename and info.name.endswith((".py", ".md")):
                    raw = _rewrite(
                        raw.decode("utf-8"), sent, label
                    ).encode("utf-8")
                out.write_bytes(raw)

        if not (staging / agent_manifest.MANIFEST_NAME).is_file():
            raise ParcelError(
                f"the parcel has no {agent_manifest.MANIFEST_NAME}, so "
                f"nothing in it can be registered as an agent."
            )
        # Validate against the name it will *land* under. A manifest has
        # to agree with the directory holding it, and in staging it does
        # not, so check a copy placed where it is going.
        holder = base / f".socgrab_check_{label}"
        if holder.exists():
            shutil.rmtree(holder)
        holder.mkdir(parents=True)
        try:
            shutil.copytree(staging, holder / label)
            agent_manifest.load(
                holder / label / agent_manifest.MANIFEST_NAME
            )
        finally:
            shutil.rmtree(holder, ignore_errors=True)

        replaced = dest.exists()
        if replaced:
            shutil.rmtree(dest)
        staging.rename(dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return Installed(
        label=label,
        directory=dest,
        files=len(members),
        replaced=replaced,
        renamed_from=sent if rename else None,
    )
