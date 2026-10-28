import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import release as r


@pytest.mark.parametrize(
    "tags,releases,expected",
    [
        ([], [], "0.1.0"),
        (["v0.1.0"], [], "0.1.1"),
        (["v0.1.0", "v0.1.4"], ["v0.1.0"], "0.1.5"),
        (["v0.1.03", "v0.1.x", "v0.2.9", "v0.1.-1"], [], "0.1.0"),
    ],
)
def test_next_patch(tags, releases, expected):
    assert r.next_version(tags, releases) == expected


def test_orphan_release_fails_closed():
    with pytest.raises(ValueError):
        r.next_version([], ["v0.1.0"])


def test_parallel_conflict():
    version = r.next_version([], [])
    with pytest.raises(ValueError):
        r.free_version(version, ["v0.1.0"], ["v0.1.0"])


@pytest.mark.parametrize(
    "event,ref,mode,expected",
    [
        ("pull_request", "refs/pull/1/merge", "production", False),
        ("push", "refs/heads/main", "production", True),
        ("push", "refs/tags/v0.1.0", "production", False),
        ("workflow_dispatch", "refs/heads/main", "diagnostic", False),
        ("workflow_dispatch", "refs/heads/main", "production", True),
    ],
)
def test_production_boundary(event, ref, mode, expected):
    assert r.production(event, ref, mode, 1) is expected


def test_production_rerun_requires_new_dispatch():
    with pytest.raises(ValueError):
        r.production("push", "refs/heads/main", "production", 2)


@pytest.fixture
def bundle(tmp_path):
    version, commit = "0.1.0", "a" * 40
    name = f"CoreDescriptionW2E-v{version}-windows-x64.zip"
    exe = b"synthetic exe, never executed"
    manifest = {
        "version": version,
        "commit": commit,
        "archive": name,
        "exe_sha256": hashlib.sha256(exe).hexdigest(),
        "smoke": {
            "status": "PASS",
            "frozen": True,
            "version": version,
            "documents": 2,
            "layers_each": 3,
            "core_interval_records": 3,
            "review_documents": 2,
            "core_interval_gate": "PASS",
            "review_policy_gate": "PASS",
            "business_rules_gate": "PASS",
            "output_schema": "core24-v1",
        },
    }
    with zipfile.ZipFile(tmp_path / name, "w") as z:
        z.writestr("CoreDescriptionW2E/CoreDescriptionW2E.exe", exe)
        z.writestr("CoreDescriptionW2E/manifest.json", json.dumps(manifest))
    manifest["sha256"] = r.digest(tmp_path / name)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / (name + ".sha256")).write_text(f"{manifest['sha256']}  {name}\n")
    return tmp_path, version, commit


def test_bundle_verified(bundle):
    assert r.verify_bundle(*bundle)["version"] == "0.1.0"


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "0.1.1"),
        ("commit", "b" * 40),
        ("sha256", "0" * 64),
        ("exe_sha256", "0" * 64),
        ("smoke", {"status": "FAIL"}),
    ],
)
def test_mismatched_bundle(bundle, field, value):
    folder, version, commit = bundle
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        r.verify_bundle(folder, version, commit)


def test_publish_failure_cannot_report_success(bundle):
    folder, version, commit = bundle
    calls = []

    def command(*argv):
        calls.append(argv)
        if argv[:3] == ("gh", "release", "create"):
            raise RuntimeError("synthetic upload failure")
        return commit if argv[:2] == ("git", "rev-parse") else ""

    with patch.object(r, "inventory", return_value=([], [])), patch.object(r, "command", command):
        with pytest.raises(RuntimeError):
            r.publish(folder, version, commit, "https://example.invalid/run")
    assert not any(c[:3] == ("gh", "release", "edit") for c in calls)


def test_workflow_production_is_queued_and_gated():
    text = Path(".github/workflows/windows-build.yml").read_text()
    assert "queue: max" in text and "cancel-in-progress: false" in text
    assert "needs: [quality, portable]" in text
    assert "tags:" not in text and "publish:" in text
    assert text.count("contents: write") == 1


@pytest.mark.parametrize("remote_type,remote_sha", [("commit", "a" * 40), ("tag", "b" * 40)])
def test_remote_tag_api_binding(bundle, remote_type, remote_sha):
    folder, version, commit = bundle
    tag = "v" + version

    def command(*argv):
        if argv[:3] == ("git", "rev-list", "-n"):
            return commit
        if argv[-1].endswith("/releases?per_page=100"):
            return json.dumps([[{"tag_name": tag, "id": 1}]])
        if argv[-1].endswith("/releases/1"):
            return json.dumps({"draft": True, "prerelease": True, "tag_name": tag})
        if "/git/ref/" in argv[-1]:
            return json.dumps({"object": {"type": remote_type, "sha": "c" * 40}})
        return json.dumps({"object": {"type": "commit", "sha": remote_sha}})

    with patch.object(r, "command", command), pytest.raises(ValueError):
        r.verify_remote(tag, commit, folder, draft=True)


def test_draft_id_lookup_and_verified_download(bundle):
    import shutil

    folder, version, commit = bundle
    tag = "v" + version
    calls = []
    metadata = {
        "id": 1,
        "tag_name": tag,
        "draft": True,
        "prerelease": True,
        "assets": [{"name": p.name, "digest": "sha256:" + r.digest(p)} for p in folder.iterdir()],
    }

    def command(*argv):
        calls.append(argv)
        if argv[:2] == ("git", "rev-list"):
            return commit
        if argv[:3] == ("gh", "release", "download"):
            for p in folder.iterdir():
                shutil.copyfile(p, Path(argv[-1]) / p.name)
            return ""
        if argv[-1].endswith("/releases?per_page=100"):
            return json.dumps([[metadata]])
        if argv[-1].endswith("/releases/1"):
            return json.dumps(metadata)
        if "/git/ref/" in argv[-1]:
            return json.dumps({"object": {"type": "tag", "sha": "c" * 40}})
        if "/git/tags/" in argv[-1]:
            return json.dumps({"object": {"type": "commit", "sha": commit}})
        raise AssertionError("Unexpected API")

    with patch.object(r, "command", command):
        assert r.verify_remote(tag, commit, folder, draft=True)["id"] == 1
    assert not any("/releases/tags/" in arg for call in calls for arg in call)
