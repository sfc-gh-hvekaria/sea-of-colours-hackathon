"""Handing a fork to a teammate — `soc share` and `soc grab`.

The thing worth pinning is not that a tarball round-trips. It is the
three properties the format promises, because each of them is something
somebody will lean on at a hackathon and none of them is visible from
the outside: a parcel writes only inside the one directory it declares,
a damaged one says so instead of installing half an agent, and packing
the same code twice gives the same fingerprint so two people can settle
"are we in sync" by reading eight characters to each other.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import tarfile

import pytest

from sea_of_colours.orchestrator_2 import agent_manifest, fork_parcel


def _fork(root, team="alpha", name="probe", *, body=None):
    """A minimal but real fork on disk. Returns its manifest."""
    label = f"{team}_{name}"
    d = root / label
    (d / "tests").mkdir(parents=True)
    (d / agent_manifest.MANIFEST_NAME).write_text(json.dumps({
        "team": team, "name": name,
        "participants": ["Ada Lovelace"],
        "menu_label": f"{label.upper()} — a fork",
        "entry": "harness:run", "needs_llm": True,
    }), encoding="utf-8")
    (d / "harness.py").write_text(
        body if body is not None else
        "from sea_of_colours.orchestrator_2.harnesses."
        f"{label}.doctrine import RULE\n"
        f"AGENT = \"{label.upper()}\"\n\n\ndef run():\n    return RULE\n",
        encoding="utf-8",
    )
    (d / "doctrine.py").write_text("RULE = 'fire the EMP'\n", encoding="utf-8")
    (d / "README.md").write_text(f"# {label}\n", encoding="utf-8")
    (d / "tests" / "test_fork.py").write_text("def test_it(): pass\n",
                                              encoding="utf-8")
    return agent_manifest.load(d / agent_manifest.MANIFEST_NAME)


def _envelope(blob: bytes, *, agent="alpha_probe", **over) -> str:
    """Wrap arbitrary bytes as a parcel, for the hostile-input tests."""
    header = {
        "agent": agent, "team": "alpha", "name": "probe",
        "participants": "Ada", "files": "1", "bytes": str(len(blob)),
        "sha256": hashlib.sha256(blob).hexdigest(),
    }
    header.update(over)
    body = base64.b64encode(blob).decode("ascii")
    lines = [f"{fork_parcel.MAGIC} {fork_parcel.VERSION}"]
    lines += [f"{k}: {v}" for k, v in header.items()]
    lines += ["---", body]
    return "\n".join(lines) + "\n"


def _tar_of(entries: dict[str, bytes], *, kind=tarfile.REGTYPE) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name, data in entries.items():
                info = tarfile.TarInfo(name)
                info.type = kind
                info.size = len(data)
                if kind is tarfile.SYMTYPE:
                    info.linkname = "/etc/passwd"
                    info.size = 0
                    data = b""
                tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


# ── the round trip ────────────────────────────────────────────────────


def test_a_fork_survives_the_trip_intact(tmp_path):
    sender, receiver = tmp_path / "them", tmp_path / "us"
    sender.mkdir()
    receiver.mkdir()
    mine = _fork(sender)

    done = fork_parcel.install(
        fork_parcel.read(fork_parcel.pack(mine)), root=receiver
    )

    assert done.label == "alpha_probe" and not done.replaced
    for rel in fork_parcel.files_in(mine.directory):
        assert (done.directory / rel).read_bytes() == \
            (mine.directory / rel).read_bytes(), f"{rel} did not survive"
    # And it is a real agent on arrival, not just files.
    agent_manifest.load(done.directory / agent_manifest.MANIFEST_NAME)


def test_the_same_code_always_packs_to_the_same_fingerprint(tmp_path):
    """Two people compare eight characters instead of unpacking."""
    root = tmp_path / "h"
    root.mkdir()
    mine = _fork(root)

    first = fork_parcel.read(fork_parcel.pack(mine))
    second = fork_parcel.read(fork_parcel.pack(mine))
    assert first.fingerprint == second.fingerprint

    (mine.directory / "doctrine.py").write_text("RULE = 'hold'\n")
    assert fork_parcel.read(fork_parcel.pack(mine)).fingerprint != \
        first.fingerprint, "a changed fork must change its fingerprint"


def test_build_litter_is_left_behind(tmp_path):
    """__pycache__ is ten times the size of the fork and stale on arrival."""
    root = tmp_path / "h"
    root.mkdir()
    mine = _fork(root)
    (mine.directory / "__pycache__").mkdir()
    (mine.directory / "__pycache__" / "harness.pyc").write_bytes(b"\x00\x01")
    (mine.directory / "notes.sqlite").write_bytes(b"\x00")

    packed = [p.as_posix() for p in fork_parcel.files_in(mine.directory)]
    assert "harness.py" in packed and "tests/test_fork.py" in packed
    assert not any("pycache" in p or p.endswith(".sqlite") for p in packed)


# ── refusing what it should ───────────────────────────────────────────


def test_a_damaged_parcel_is_refused_rather_than_installed(tmp_path):
    root = tmp_path / "h"
    root.mkdir()
    text = fork_parcel.pack(_fork(root))
    lines = text.splitlines()
    lines[-2] = "A" + lines[-2][1:]  # one byte of the payload

    with pytest.raises(fork_parcel.ParcelError) as exc:
        fork_parcel.read("\n".join(lines))
    assert "checksum" in str(exc.value).lower()


def test_something_that_is_not_a_parcel_says_so(tmp_path):
    with pytest.raises(fork_parcel.ParcelError) as exc:
        fork_parcel.read("here is my agent, hope it works\n")
    assert fork_parcel.MAGIC in str(exc.value)


def test_a_truncated_parcel_is_not_read_as_an_empty_one():
    with pytest.raises(fork_parcel.ParcelError) as exc:
        fork_parcel.read(f"{fork_parcel.MAGIC} 1\nagent: alpha_probe\n")
    assert "truncated" in str(exc.value)


@pytest.mark.parametrize("escape", [
    "../../../evil.py",
    "/etc/soc_evil.py",
    "sub/../../evil.py",
])
def test_a_parcel_cannot_write_outside_its_own_directory(tmp_path, escape):
    """The one guarantee that has to hold whoever sent it."""
    receiver = tmp_path / "us"
    receiver.mkdir()
    parcel = fork_parcel.read(_envelope(_tar_of({escape: b"import os\n"})))

    with pytest.raises(fork_parcel.ParcelError) as exc:
        fork_parcel.install(parcel, root=receiver)
    assert "outside" in str(exc.value)
    assert list(receiver.iterdir()) == [], "it left something behind"


def test_a_parcel_carries_source_and_prose_and_nothing_else(tmp_path):
    receiver = tmp_path / "us"
    receiver.mkdir()
    parcel = fork_parcel.read(_envelope(_tar_of({"payload.so": b"\x7fELF"})))

    with pytest.raises(fork_parcel.ParcelError) as exc:
        fork_parcel.install(parcel, root=receiver)
    assert "source or prose" in str(exc.value)


def test_a_symlink_out_of_the_tree_is_refused(tmp_path):
    receiver = tmp_path / "us"
    receiver.mkdir()
    parcel = fork_parcel.read(
        _envelope(_tar_of({"secrets.py": b""}, kind=tarfile.SYMTYPE))
    )

    with pytest.raises(fork_parcel.ParcelError) as exc:
        fork_parcel.install(parcel, root=receiver)
    assert "not a plain file" in str(exc.value)


def test_a_parcel_that_is_not_an_agent_leaves_nothing_behind(tmp_path):
    """Half an agent is worse than none — it registers and then fails."""
    receiver = tmp_path / "us"
    receiver.mkdir()
    parcel = fork_parcel.read(_envelope(_tar_of({"harness.py": b"x = 1\n"})))

    with pytest.raises(fork_parcel.ParcelError) as exc:
        fork_parcel.install(parcel, root=receiver)
    assert agent_manifest.MANIFEST_NAME in str(exc.value)
    assert list(receiver.iterdir()) == []


# ── landing next to what you already have ─────────────────────────────


def test_your_own_copy_is_not_replaced_by_accident(tmp_path):
    sender, receiver = tmp_path / "them", tmp_path / "us"
    sender.mkdir()
    receiver.mkdir()
    text = fork_parcel.pack(_fork(sender))
    _fork(receiver)  # you have your own alpha_probe
    (receiver / "alpha_probe" / "doctrine.py").write_text("RULE = 'mine'\n")

    with pytest.raises(fork_parcel.ParcelError) as exc:
        fork_parcel.install(fork_parcel.read(text), root=receiver)
    assert "--force" in str(exc.value) and "--as-team" in str(exc.value)
    assert "mine" in (receiver / "alpha_probe" / "doctrine.py").read_text()

    done = fork_parcel.install(
        fork_parcel.read(text), root=receiver, force=True
    )
    assert done.replaced
    assert "EMP" in (receiver / "alpha_probe" / "doctrine.py").read_text()


def test_a_grabbed_fork_can_be_renamed_to_sit_beside_your_own(tmp_path):
    """Renaming has to repoint the imports, or it lands broken.

    A fork self-references by absolute dotted path — the minter rewrites
    those when it copies V12, and arriving under a new label is the same
    problem. Missing the uppercase form is the subtle half: the agent
    would file its turns in the audit trail under the sender's name,
    which is precisely the comparison you grabbed it to make.
    """
    sender, receiver = tmp_path / "them", tmp_path / "us"
    sender.mkdir()
    receiver.mkdir()
    text = fork_parcel.pack(_fork(sender))
    _fork(receiver)  # your own is already here, untouched

    done = fork_parcel.install(
        fork_parcel.read(text), root=receiver, rename=("beta", "borrowed")
    )

    assert done.label == "beta_borrowed"
    assert done.renamed_from == "alpha_probe"
    harness = (done.directory / "harness.py").read_text()
    assert "harnesses.beta_borrowed.doctrine" in harness
    assert 'AGENT = "BETA_BORROWED"' in harness
    assert "alpha_probe" not in harness.lower()

    landed = agent_manifest.load(
        done.directory / agent_manifest.MANIFEST_NAME
    )
    assert (landed.team, landed.name) == ("beta", "borrowed")
    assert landed.label == "beta_borrowed"
    assert (receiver / "alpha_probe").is_dir(), "it stepped on your own fork"


# ── the bit that would silently cost somebody their push ──────────────


def test_a_parcel_left_in_the_repo_cannot_block_a_push():
    """`soc push` refuses while anything outside your folder has changed."""
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    probe = f"scratch_probe{fork_parcel.SUFFIX}"
    out = subprocess.run(
        ["git", "check-ignore", "-q", probe], cwd=repo, check=False
    )
    assert out.returncode == 0, (
        f"{fork_parcel.SUFFIX} is not gitignored, so a shared fork sitting "
        f"in the repo would show as a stray change and block `soc push`"
    )
