"""Build-time release control. No part of this script runs during user conversion."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

VERSION = re.compile(r"0\.1\.(0|[1-9][0-9]*)\Z")
REPO = "GavrisAS/CoreDescriptionW2E"


def command(*args: str) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def next_version(tags: list[str], releases: list[str]) -> str:
    if set(releases) - set(tags):
        raise ValueError("Release without a tag: repair publication before allocating")
    patches = [int(tag[5:]) for tag in tags if tag.startswith("v") and VERSION.fullmatch(tag[1:])]
    return f"0.1.{max(patches, default=-1) + 1}"


def production(event: str, ref: str, mode: str, attempt: int) -> bool:
    enabled = ref == "refs/heads/main" and (
        event == "push" or (event == "workflow_dispatch" and mode == "production")
    )
    if enabled and attempt != 1:
        raise ValueError("Production re-run refused; dispatch a new production run")
    return enabled


def inventory() -> tuple[list[str], list[str]]:
    command("git", "fetch", "origin", "--tags", "--prune")
    tags = command("git", "tag", "--list").splitlines()
    pages = json.loads(
        command("gh", "api", "--paginate", "--slurp", f"repos/{REPO}/releases?per_page=100")
    )
    return tags, [r["tag_name"] for page in pages for r in page]


def free_version(version: str, tags: list[str], releases: list[str]) -> None:
    if not VERSION.fullmatch(version) or next_version(tags, releases) != version:
        raise ValueError("Version conflict: refusing tag/release mutation")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_bundle(folder: Path, version: str, commit: str) -> dict:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig"))
    name = f"CoreDescriptionW2E-v{version}-windows-x64.zip"
    archive = folder / name
    if not re.fullmatch(r"0\.1\.\d+(?:\+local)?", version):
        raise ValueError("Invalid build version")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid source commit")
    checksum = digest(archive)
    if (folder / (name + ".sha256")).read_text().strip() != f"{checksum}  {name}":
        raise ValueError("ZIP checksum mismatch")
    if any(
        manifest.get(k) != v
        for k, v in {
            "version": version,
            "commit": commit,
            "archive": name,
            "sha256": checksum,
        }.items()
    ):
        raise ValueError("ZIP/manifest/version/source mismatch")
    receipt = manifest.get("smoke", {})
    if any(
        receipt.get(k) != v
        for k, v in {
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
        }.items()
    ):
        raise ValueError("Missing frozen acceptance")
    with zipfile.ZipFile(archive) as bundle:
        names: set[str] = set()
        for info in bundle.infolist():
            path = PurePosixPath(info.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in info.filename
                or ":" in info.filename
                or info.filename.casefold() in names
                or (info.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("Unsafe ZIP")
            names.add(info.filename.casefold())
        inner = json.loads(bundle.read("CoreDescriptionW2E/manifest.json"))
        if inner["version"] != version or inner["commit"] != commit:
            raise ValueError("Embedded metadata mismatch")
        executable = bundle.read("CoreDescriptionW2E/CoreDescriptionW2E.exe")
        if hashlib.sha256(executable).hexdigest() != manifest["exe_sha256"]:
            raise ValueError("EXE mismatch")
    return manifest


def verify_remote(tag: str, commit: str, folder: Path, *, draft: bool) -> dict:
    if draft:
        # The tag endpoint excludes unpublished drafts, even for their author.
        pages = json.loads(
            command("gh", "api", "--paginate", "--slurp", f"repos/{REPO}/releases?per_page=100")
        )
        matches = [r for page in pages for r in page if r["tag_name"] == tag]
        if len(matches) != 1:
            raise ValueError("Missing/ambiguous draft release")
        release = json.loads(command("gh", "api", f"repos/{REPO}/releases/{matches[0]['id']}"))
    else:
        release = json.loads(command("gh", "api", f"repos/{REPO}/releases/tags/{tag}"))
    if release["draft"] != draft or not release["prerelease"] or release["tag_name"] != tag:
        raise ValueError("Release publication state mismatch")
    if command("git", "rev-list", "-n", "1", tag) != commit:
        raise ValueError("Local tag source mismatch")
    ref = json.loads(command("gh", "api", f"repos/{REPO}/git/ref/tags/{tag}"))["object"]
    if ref["type"] != "tag":
        raise ValueError("Expected annotated remote tag")
    target = json.loads(command("gh", "api", f"repos/{REPO}/git/tags/{ref['sha']}"))["object"]
    if target["type"] != "commit" or target["sha"] != commit:
        raise ValueError("Remote API tag source mismatch")
    expected = {p.name: digest(p) for p in folder.iterdir() if p.is_file()}
    if {a["name"] for a in release["assets"]} != set(expected):
        raise ValueError("Unexpected release assets")
    for asset in release["assets"]:
        if asset.get("digest") != "sha256:" + expected[asset["name"]]:
            raise ValueError("Remote asset digest mismatch")
    if draft:
        with tempfile.TemporaryDirectory() as temp:
            command("gh", "release", "download", tag, "--dir", temp)
            if {p.name: digest(p) for p in Path(temp).iterdir()} != expected:
                raise ValueError("Downloaded draft mismatch")
    else:
        # Public URLs must work without authentication; do not print signed redirects.
        for asset in release["assets"]:
            with urllib.request.urlopen(asset["browser_download_url"], timeout=120) as stream:
                downloaded = hashlib.file_digest(stream, "sha256").hexdigest()
            if downloaded != expected[asset["name"]]:
                raise ValueError("Public download mismatch")
    return release


def publish(folder: Path, version: str, commit: str, run_url: str) -> None:
    verify_bundle(folder, version, commit)
    name = f"CoreDescriptionW2E-v{version}-windows-x64.zip"
    if {p.name for p in folder.iterdir()} != {name, name + ".sha256", "manifest.json"}:
        raise ValueError("Unexpected publication files")
    tags, releases = inventory()
    free_version(version, tags, releases)
    if command("git", "rev-parse", "HEAD") != commit:
        raise ValueError("Checkout/source mismatch")
    tag = "v" + version
    command("git", "config", "user.name", "github-actions[bot]")
    command("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    command("git", "tag", "-a", tag, commit, "-m", f"Verified Windows portable {tag}")
    command("git", "push", "origin", f"refs/tags/{tag}")
    # A failed upload leaves a reserved tag/draft for investigation, never overwritten.
    previous = sorted(
        (t for t in tags if VERSION.fullmatch(t.removeprefix("v"))),
        key=lambda t: int(t.split(".")[-1]),
    )
    change_range = f"{previous[-1]}..{commit}" if previous else commit
    changes = command("git", "log", "-8", "--format=- %s (%h)", change_range)
    with tempfile.TemporaryDirectory() as temp:
        notes = Path(temp) / "notes.md"
        notes.write_text(
            f"Локально / без LLM / без Office.\n\nSource: `{commit}`\n\n"
            f"[Проверенный workflow]({run_url})\n\n{changes}\n\n"
            "Предварительный выпуск: визуальная приёмка Excel пользователем PENDING. "
            "CORE_INTERVAL: полные описания целиком или доказанными частями, без выдуманных глубин; REVIEW_REQUIRED с русским Контроль и реестром. "
            "K — керна/линейный вынос, коробки отдельно; полный дубль исключается с журналом; явный нулевой выход — отдельная строка. "
            "Вход core23-v1, выход core24-v1 с номером скважины из Word в X; каждый Word-кандидат имеет явный исход. "
            "DOC: основной текст Word 97–2003 без шифрования/полей; только доказанно пустые рамки; "
            "DOCX: без рамок/altChunk/исправлений. Неизвестные значения пусты; "
            "формулы пересчитывает Excel. См. README и docs/RELEASE.md.\n",
            encoding="utf-8",
        )
        command(
            "gh",
            "release",
            "create",
            tag,
            *[str(p) for p in sorted(folder.iterdir())],
            "--verify-tag",
            "--draft",
            "--prerelease",
            "--title",
            f"CoreDescriptionW2E {tag}",
            "--notes-file",
            str(notes),
        )
    verify_remote(tag, commit, folder, draft=True)
    command("gh", "release", "edit", tag, "--draft=false")
    release = verify_remote(tag, commit, folder, draft=False)
    print(json.dumps({"status": "PASS", "tag": tag, "url": release["html_url"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "verify", "publish"])
    parser.add_argument("--folder", type=Path, default=Path("release-files"))
    parser.add_argument("--version")
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    args = parser.parse_args()
    if args.action == "plan":
        is_production = production(
            os.environ.get("GITHUB_EVENT_NAME", ""),
            os.environ.get("GITHUB_REF", ""),
            os.environ.get("BUILD_MODE", "diagnostic"),
            int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
        )
        version = next_version(*inventory()) if is_production else "0.1.0+local"
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"version={version}\nproduction={str(is_production).lower()}\n")
    elif args.action == "verify":
        verify_bundle(args.folder, args.version, args.commit)
    else:
        if not production(
            os.environ["GITHUB_EVENT_NAME"],
            os.environ["GITHUB_REF"],
            os.environ.get("BUILD_MODE", "diagnostic"),
            int(os.environ["GITHUB_RUN_ATTEMPT"]),
        ):
            raise ValueError("Diagnostic builds cannot publish")
        run_url = f"https://github.com/{REPO}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        publish(args.folder, args.version, args.commit, run_url)


if __name__ == "__main__":
    main()
