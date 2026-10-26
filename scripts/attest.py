"""Capture sanitized evidence for handoff; raw process output is never retained."""

import argparse
import json
import os
import platform
import re
import subprocess
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from scripts.handoff import default_receipts, encode, snapshot
from scripts.release import digest, verify_bundle

ROOT = Path(__file__).resolve().parents[1]
REPO = "GavrisAS/CoreDescriptionW2E"


def run(argv: list[str], receipts: dict) -> tuple[int, str]:
    result = subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    receipts["COMMANDS"]["items"].append(
        {
            "argv": argv,
            "exit_code": result.returncode,
            "os": platform.system(),
            "utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    return result.returncode, result.stdout


def remote(receipts: dict, head: str) -> None:
    code, output = run(
        [
            "gh",
            "run",
            "list",
            "--commit",
            head,
            "--limit",
            "30",
            "--json",
            "databaseId,workflowName,headSha,status,conclusion,url",
        ],
        receipts,
    )
    if code:
        return
    runs = json.loads(output)
    for gate, workflow in [("CI", "CI"), ("WINDOWS", "Windows portable")]:
        candidates = [r for r in runs if r["workflowName"] == workflow and r["headSha"] == head]
        if not candidates:
            continue
        latest = candidates[0]
        code, output = run(
            [
                "gh",
                "run",
                "view",
                str(latest["databaseId"]),
                "--json",
                "databaseId,headSha,status,conclusion,url,jobs",
            ],
            receipts,
        )
        if code:
            continue
        view = json.loads(output)
        evidence = {
            "kind": "REMOTE DIRECT EVIDENCE",
            "id": view["databaseId"],
            "sha": view["headSha"],
            "url": view["url"],
            "status": view["status"],
            "conclusion": view["conclusion"],
            "jobs": [{k: j[k] for k in ("name", "status", "conclusion")} for j in view["jobs"]],
        }
        status = (
            "PENDING"
            if view["status"] != "completed"
            else "PASS"
            if view["conclusion"] == "success"
            else "FAIL"
        )
        receipts["GATES"][gate] = {"status": status, "evidence": [evidence]}
    code, output = run(["gh", "api", f"repos/{REPO}/releases?per_page=100"], receipts)
    if code:
        return
    # Inspect actual tag object/target, not the release target branch field.
    for release in json.loads(output):
        if release["draft"]:
            continue
        tag = release["tag_name"]
        code, output = run(["gh", "api", f"repos/{REPO}/git/ref/tags/{tag}"], receipts)
        if code:
            continue
        target = json.loads(output)["object"]
        if target["type"] == "tag":
            code, output = run(["gh", "api", target["url"]], receipts)
            if code:
                continue
            target = json.loads(output)["object"]
        if target["sha"] != head:
            continue
        version = tag.removeprefix("v")
        name = f"CoreDescriptionW2E-v{version}-windows-x64.zip"
        assets = release["assets"]
        expected = {name, name + ".sha256", "manifest.json"}
        if {a["name"] for a in assets} != expected or not all(a.get("digest") for a in assets):
            receipts["GATES"]["RELEASE"] = {"status": "FAIL", "evidence": [release["html_url"]]}
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="w2e-public-assets-") as temp:
                folder = Path(temp)
                for asset in assets:
                    with urllib.request.urlopen(
                        asset["browser_download_url"], timeout=120
                    ) as stream:
                        destination = folder / asset["name"]
                        with destination.open("wb") as output_file:
                            while chunk := stream.read(1024 * 1024):
                                output_file.write(chunk)
                    if "sha256:" + digest(destination) != asset["digest"]:
                        raise ValueError("Public asset digest mismatch")
                verify_bundle(folder, version, head)
        except Exception:
            receipts["GATES"]["RELEASE"] = {"status": "FAIL", "evidence": [release["html_url"]]}
            receipts["RELEASE"] = {
                "status": "FAIL",
                "publication": "PUBLISHED_UNVERIFIED",
                "url": release["html_url"],
                "source_sha": head,
            }
            break
        receipts["RELEASE"] = {
            "status": "PASS",
            "publication": "PUBLISHED",
            "version": version,
            "tag": tag,
            "source_sha": head,
            "url": release["html_url"],
            "prerelease": release["prerelease"],
            "user_acceptance": "PENDING",
            "artifacts": [
                {k: a[k] for k in ("name", "size", "digest", "browser_download_url")}
                for a in assets
            ],
            "verification": "Public unauthenticated downloads, asset SHA, ZIP/manifest/source binding",
        }
        receipts["GATES"]["RELEASE"] = {
            "status": "PASS",
            "evidence": [
                {"kind": "REMOTE DIRECT EVIDENCE", "url": release["html_url"], "sha": head}
            ],
        }
        break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--diagnostics", type=Path, help="Private aggregate-only JSON")
    args = parser.parse_args()
    _, state, _ = snapshot(ROOT)
    receipts = default_receipts()
    for tool, argv in {
        "uv": ["uv", "--version"],
        "git": ["git", "--version"],
        "ruff": ["uv", "run", "ruff", "--version"],
        "ty": ["uv", "run", "ty", "--version"],
        "pytest": ["uv", "run", "pytest", "--version"],
    }.items():
        code, output = run(argv, receipts)
        receipts["COMMANDS"]["tools"][tool] = output.strip() if code == 0 else "UNVERIFIED"
    good = True
    commands = [
        ["uv", "sync", "--locked"],
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "ruff", "format", "--check", "."],
        ["uv", "run", "ty", "check"],
        ["uv", "run", "pytest"],
    ]
    for argv in commands:
        code, output = run(argv, receipts)
        good &= code == 0
        if argv[-1] == "pytest":
            match = re.search(r"(\d+) passed", output)
            receipts["TESTS"] = {
                "status": "PASS" if code == 0 else "FAIL",
                "count": int(match[1]) if match else 0,
                "runner": platform.system(),
                "evidence": [{"argv": argv, "exit_code": code}],
            }
    receipts["GATES"]["LOCAL"] = {
        "status": "PASS" if good else "FAIL",
        "evidence": [{"argv": c} for c in commands],
    }
    if args.remote:
        remote(receipts, state["head"])
    if args.diagnostics:
        receipts["DIAGNOSTICS"] = json.loads(args.diagnostics.read_text(encoding="utf-8"))
    if snapshot(ROOT)[1] != state:
        raise ValueError("Source changed during attestation")
    evidence = {
        "schema_version": 1,
        "source_fingerprint": state["source_fingerprint"],
        "source_head": state["head"],
        "receipts": receipts,
    }
    destination = ROOT / "artifacts/handoff-evidence.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encode(evidence))
    print(
        json.dumps(
            {
                "local": receipts["GATES"]["LOCAL"]["status"],
                "tests": receipts["TESTS"]["count"],
                "gates": {k: v["status"] for k, v in receipts["GATES"].items()},
            }
        )
    )
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
