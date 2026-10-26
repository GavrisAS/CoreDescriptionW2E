"""Only synthetic repositories and payloads; malicious strings are assembled locally."""

import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from scripts import handoff as h


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "config", "user.name", "Synthetic")
    (root / "README.md").write_text("Synthetic source\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "synthetic")
    return root


def read(tar, name):
    stream = tar.extractfile(name)
    assert stream is not None
    return stream.read()


def rewrite(archive, mutate):
    with tarfile.open(archive, "r:xz") as tar:
        members = [(m, read(tar, m)) for m in tar if m.isfile()]
    members = mutate(members)
    with tarfile.open(archive, "w:xz", format=tarfile.USTAR_FORMAT) as tar:
        for info, data in members:
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    Path(str(archive) + ".sha256").write_text(f"{h.checksum(archive)}  {archive.name}\n")


def test_fresh_extract_dirty_binding_and_unique_names(repo, tmp_path):
    (repo / "README.md").write_text("Changed synthetic source\n")
    (repo / "scripts").mkdir()
    (repo / "scripts/new.py").write_text("VALUE = 1\n")
    archive = h.create(repo, tmp_path / "packages")
    manifest = h.verify(archive)
    other = h.create(repo, tmp_path / "packages")
    assert other != archive
    with tarfile.open(archive) as tar:
        state = json.loads(read(tar, "review/SOURCE_STATE.json"))
        assert state["dirty"]
        assert b"Changed synthetic" in read(tar, "review/DIFF.patch")
        assert read(tar, "source/scripts/new.py") == (repo / "scripts/new.py").read_bytes()
    assert manifest["source_fingerprint"] == h.snapshot(repo)[1]["source_fingerprint"]


@pytest.mark.parametrize(
    "attack",
    [
        "traversal",
        "absolute",
        "symlink",
        "duplicate",
        "device",
        "metadata",
        "spoof",
        "manifest",
        "hash",
        "receipt",
    ],
)
def test_archive_attacks_rejected(repo, tmp_path, attack):
    archive = h.create(repo, tmp_path / "packages")

    def mutate(members):
        if attack in {"manifest", "hash", "receipt"}:
            target = {
                "manifest": "manifest.json",
                "hash": "source/README.md",
                "receipt": "review/TESTS.json",
            }[attack]
            return [(m, b"{}" if m.name == target else d) for m, d in members]
        info = tarfile.TarInfo(
            {
                "traversal": "../escape",
                "absolute": "/escape",
                "spoof": "source/docs/../private.doc",
            }.get(attack, "extra")
        )
        info.mode = 0o600
        if attack == "symlink":
            info.type, info.linkname = tarfile.SYMTYPE, "../escape"
        if attack == "device":
            info.type = tarfile.CHRTYPE
        if attack == "metadata":
            info.uid = 1
        if attack == "duplicate":
            return members + [members[0]]
        return members + [(info, b"")]

    rewrite(archive, mutate)
    with pytest.raises(h.Unsafe):
        h.verify(archive)


def test_corruption_and_external_checksum(repo, tmp_path):
    archive = h.create(repo, tmp_path / "packages")
    archive.write_bytes(archive.read_bytes()[:-40])
    with pytest.raises(h.Unsafe):
        h.verify(archive)
    Path(str(archive) + ".sha256").write_text(f"{h.checksum(archive)}  {archive.name}\n")
    with pytest.raises(h.Unsafe):
        h.verify(archive)


@pytest.mark.parametrize(
    "attack",
    [
        "secret",
        "private_path",
        "private_doc",
        "symlink",
        "fixture_spoof",
        "oversized",
        "untracked_private",
    ],
)
def test_creation_privacy_gate(repo, tmp_path, attack):
    target = repo / "README.md"
    if attack == "secret":
        target.write_text("gh" + "p_" + "x" * 36)
    elif attack == "private_path":
        target.write_text("/" + "home/person/private/input.doc")
    elif attack == "private_doc":
        (repo / "docs").mkdir()
        (repo / "docs/private.doc").write_bytes(b"private")
    elif attack == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "elsewhere")
    elif attack == "fixture_spoof":
        (repo / "tests/fixtures").mkdir(parents=True)
        (repo / "tests/fixtures/synthetic.doc").write_bytes(b"private")
    elif attack == "oversized":
        target.write_bytes(b"x" * (h.MAX_FILE + 1))
    else:
        (repo / "local.json").write_text("private")
    with pytest.raises(h.Unsafe):
        h.create(repo, tmp_path / "packages")
    assert not list((tmp_path / "packages").glob("*.tar.xz"))


def test_stale_evidence_rejected(repo, tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(h.encode({"source_fingerprint": "old", "receipts": h.default_receipts()}))
    with pytest.raises(h.Unsafe):
        h.create(repo, tmp_path / "packages", evidence)


def test_incomplete_receipts_rejected():
    receipts = h.default_receipts()
    receipts["GATES"]["CI"] = {"status": "PASS"}
    with pytest.raises(h.Unsafe):
        h.validate_receipts(receipts)


def test_synthetic_fixtures_accepted():
    for name in h.SYNTHETICS:
        h.scan("source/" + name, Path(name).read_bytes())


def test_cli_create_verify_fresh(repo, tmp_path):
    archive = h.create(repo, tmp_path / "packages")
    result = subprocess.run(
        ["uv", "run", "python", "scripts/handoff.py", "verify", "--archive", str(archive)],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "PASS"


def test_public_placeholder_cannot_hide_private_suffix():
    payload = ("data/" + "input.doc/private").encode()
    with pytest.raises(h.Unsafe):
        h.scan("review/REPORT.md", payload)


def test_dirty_secret_diff_is_rejected(repo, tmp_path):
    (repo / "README.md").write_text("gh" + "p_" + "x" * 36)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "synthetic secret")
    (repo / "README.md").write_text("Redacted current source\n")
    with pytest.raises(h.Unsafe):
        h.create(repo, tmp_path / "packages")


def test_failed_gate_still_produces_package(repo, tmp_path):
    receipts = h.default_receipts()
    receipts["GATES"]["LOCAL"] = {"status": "FAIL", "evidence": []}
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(
        h.encode(
            {
                "source_fingerprint": h.snapshot(repo)[1]["source_fingerprint"],
                "source_head": h.snapshot(repo)[1]["head"],
                "receipts": receipts,
            }
        )
    )
    assert h.verify(h.create(repo, tmp_path / "packages", evidence))["head"]


def test_evidence_cannot_cross_head_with_identical_source(repo, tmp_path):
    state = h.snapshot(repo)[1]
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(
        h.encode(
            {
                "source_fingerprint": state["source_fingerprint"],
                "source_head": state["head"],
                "receipts": h.default_receipts(),
            }
        )
    )
    git(repo, "commit", "--allow-empty", "-m", "new source identity")
    assert h.snapshot(repo)[1]["source_fingerprint"] == state["source_fingerprint"]
    with pytest.raises(h.Unsafe):
        h.create(repo, tmp_path / "packages", evidence)
