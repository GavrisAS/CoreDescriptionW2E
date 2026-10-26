"""Private, self-checking single-archive source handoff (stdlib, Python 3.14)."""

import argparse
import hashlib
import io
import json
import lzma
import os
import platform
import re
import stat
import subprocess
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

SCHEMA = 1
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "PENDING"}
RECEIPTS = {"GATES", "COMMANDS", "TESTS", "RELEASE", "DIAGNOSTICS"}
DENIED = {
    "data",
    "dist",
    "build",
    "artifacts",
    "outputs",
    "output",
    "reports",
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".ty_cache",
}
ROOT_FILES = {
    "AGENTS.md",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
    ".python-version",
}
SYNTHETICS = {"tests/fixtures/synthetic.doc", "tests/fixtures/synthetic.docx"}
# Public, audited fixtures; a private replacement cannot masquerade under the same name.
SYNTHETIC_HASHES: dict[str, str] = {
    "tests/fixtures/synthetic.doc": "4191e79659aaee125da6dcf841fa2262d7715a81f9025e0f211097d9b6950838",
    "tests/fixtures/synthetic.docx": "3948abbb0595592efdf012cebc6844aaf17e42418313ac56d01a24f52f973c1d",
}
SECRET = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?i:(?:password|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[\"']?"
    r"[A-Za-z0-9/+_=.-]{16,}))"
)
PRIVATE_PATH = re.compile(
    r"(?i)(?:/(?:home|Users|data/projects|tmp|mnt)/[^\s\"'<>]+|"
    r"[A-Z]:[\\/](?:Users|data|projects)[\\/][^\s\"'<>]+|"
    r"(?:^|[\s\"'=])(?:\.?\.?[/\\])?data[/\\][^\s\"'<>]+)",
    re.M,
)


class Unsafe(ValueError):
    """The package cannot be certified safe or complete."""


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or "\\" in name
        or ":" in name
        or any(p in {"", ".", ".."} for p in name.split("/"))
        or str(path) != name
        or any(ord(c) < 32 for c in name)
    ):
        raise Unsafe("Unsafe relative path")
    return path


def allowed_source(name: str) -> None:
    path = relative(name)
    lower = [p.casefold() for p in path.parts]
    if any(p in DENIED or p.startswith(".env") or p.endswith("_cache") for p in lower):
        raise Unsafe("Private/generated source path")
    if any(
        p.endswith((".pem", ".key", ".pfx", ".p12"))
        or p.startswith(("id_rsa", "id_ed25519", "credentials"))
        for p in lower
    ):
        raise Unsafe("Key path")
    if name in SYNTHETICS:
        return
    if path.suffix.casefold() in {".doc", ".docx", ".xlsx", ".xls", ".zip", ".xz", ".exe"}:
        raise Unsafe("Private or binary source")
    if name in ROOT_FILES:
        return
    if path.parts[0] in {"src", "scripts", "tests", "docs", ".github"} and path.suffix in {
        ".py",
        ".md",
        ".toml",
        ".json",
        ".yml",
        ".yaml",
        ".ps1",
        ".txt",
    }:
        return
    raise Unsafe("Source outside audited allowlist")


def scan(name: str, data: bytes) -> None:
    relative(name)
    if len(data) > MAX_FILE:
        raise Unsafe("Oversized file")
    if name.startswith("source/"):
        source = name.removeprefix("source/")
        allowed_source(source)
        if source in SYNTHETICS:
            if sha(data) != SYNTHETIC_HASHES[source]:
                raise Unsafe("Synthetic fixture fingerprint mismatch")
            return
    text = data.decode("utf-8")
    # Literal public documentation placeholders and ignore globs are not private inputs.
    for placeholder in (
        "/data/**",
        "data/.gitkeep",
        "data/template.xlsx",
        "data/check",
        "data/conversion-status.json",
        "data/input.doc",
        "data/output/input.xlsx",
        "data/expected.json",
        "data/reference.docx",
    ):
        text = re.sub(re.escape(placeholder) + r"(?=$|[\s`\"'<>),;])", "PUBLIC_PLACEHOLDER", text)
    if SECRET.search(text) or PRIVATE_PATH.search(text) or "\x00" in text:
        raise Unsafe("Sensitive text/path detected; redact locally before packaging")


def file_manifest(files: dict[str, bytes]) -> list[dict]:
    return [
        {"path": name, "size": len(data), "sha256": sha(data)}
        for name, data in sorted(files.items())
    ]


def fingerprint(files: dict[str, bytes]) -> str:
    return sha(encode(file_manifest({k: v for k, v in files.items() if k.startswith("source/")})))


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()


def snapshot(root: Path) -> tuple[dict[str, bytes], dict, bytes]:
    tracked = git(root, "ls-files", "-z").split("\0")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    files: dict[str, bytes] = {}
    deleted: list[str] = []
    for name in sorted(set(tracked + untracked) - {""}):
        if name == "data/.gitkeep":
            continue
        allowed_source(name)
        path = root / name
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise Unsafe("Symlink source")
        if not path.exists():
            if name in tracked:
                deleted.append(name)
            continue
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > MAX_FILE:
            raise Unsafe("Special/oversized source")
        content = path.read_bytes()
        scan("source/" + name, content)
        files["source/" + name] = content
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    head = git(root, "rev-parse", "HEAD")
    try:
        origin = git(root, "rev-parse", "origin/main")
    except subprocess.CalledProcessError:
        origin = None
    state = {
        "schema_version": SCHEMA,
        "head": head,
        "branch": git(root, "branch", "--show-current"),
        "origin_main": origin,
        "dirty": bool(status),
        "changes": status,
        "deleted": deleted,
        "source_fingerprint": fingerprint(files),
        "source_files": file_manifest(files),
    }
    # Diff against HEAD includes staged AND unstaged changes. No private raw stdout retained.
    patch = git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "HEAD",
        "--",
        ".",
        ":(exclude)data",
    ).encode()
    scan("review/DIFF.patch", patch)
    return files, state, patch


def validate_receipts(receipts: dict) -> None:
    if set(receipts) != RECEIPTS:
        raise Unsafe("Missing typed receipts")
    gates = receipts["GATES"]
    if set(gates) != {"LOCAL", "CI", "WINDOWS", "RELEASE"}:
        raise Unsafe("Missing gate")
    for gate in gates.values():
        if not isinstance(gate, dict) or gate.get("status") not in STATUSES:
            raise Unsafe("Invalid gate status")
        if gate["status"] == "PASS" and not gate.get("evidence"):
            raise Unsafe("PASS without evidence")
    commands = receipts["COMMANDS"]
    if not isinstance(commands.get("items"), list) or not isinstance(commands.get("tools"), dict):
        raise Unsafe("Invalid command receipt")
    for item in commands["items"]:
        if (
            not isinstance(item.get("argv"), list)
            or not item["argv"]
            or not all(isinstance(v, str) for v in item["argv"])
            or type(item.get("exit_code")) is not int
            or not item.get("os")
        ):
            raise Unsafe("Incomplete command receipt")
        if any(re.search(r"(?i)token|password|secret|credential", a) for a in item["argv"]):
            raise Unsafe("Secret-bearing command refused")
    tests = receipts["TESTS"]
    if (
        tests.get("status") not in STATUSES
        or type(tests.get("count")) is not int
        or tests["count"] < 0
        or not isinstance(tests.get("runner"), str)
    ):
        raise Unsafe("Invalid test receipt")
    if tests["status"] == "PASS" and (not tests.get("evidence") or not tests["count"]):
        raise Unsafe("Tests PASS without evidence")
    if receipts["RELEASE"].get("status") not in STATUSES:
        raise Unsafe("Invalid release receipt")
    diagnostics = receipts["DIAGNOSTICS"]
    if set(diagnostics) - {"codes", "counts", "checks", "limitations", "status"}:
        raise Unsafe("Unexpected diagnostic fields")
    if not isinstance(diagnostics.get("codes"), dict):
        raise Unsafe("Invalid diagnostic receipt")
    for code, count in diagnostics["codes"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", code) or type(count) is not int or count < 0:
            raise Unsafe("Diagnostics must contain codes/counts only")
    for key, value in diagnostics.get("counts", {}).items():
        values = value if isinstance(value, list) else [value]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not all(
            type(v) in {int, float} and v >= 0 for v in values
        ):
            raise Unsafe("Diagnostics must contain numeric counts only")
    if not all(type(v) is bool for v in diagnostics.get("checks", {}).values()):
        raise Unsafe("Invalid diagnostic checks")


def default_receipts() -> dict:
    return {
        "GATES": {
            k: {"status": "NOT_RUN", "evidence": []} for k in ("LOCAL", "CI", "WINDOWS", "RELEASE")
        },
        "COMMANDS": {
            "items": [],
            "os": platform.system(),
            "tools": {"python": platform.python_version()},
        },
        "TESTS": {"status": "NOT_RUN", "count": 0, "runner": platform.system(), "evidence": []},
        "RELEASE": {"status": "NOT_RUN", "publication": "NOT_PUBLISHED"},
        "DIAGNOSTICS": {"codes": {}, "limitations": ["Visual Excel acceptance PENDING"]},
    }


def report(receipts: dict) -> bytes:
    lines = [
        "# Private handoff audit",
        "",
        "Evidence is bound to SOURCE_STATE and manifest.",
        "No historical PASS is inherited. Missing checks are NOT_RUN.",
        "",
    ]
    topics = {
        "A": "Source state and remote evidence: SOURCE_STATE.json, GATES.json",
        "B": "Single archive, fresh extract, SHA and privacy checks",
        "C": "Version allocation and immutable build metadata: RELEASE.json",
        "D": "Publication evidence and asset checks: RELEASE.json",
        "E": "Private regression codes/counts only: DIAGNOSTICS.json",
        "F": "Quality commands and test runner: COMMANDS.json, TESTS.json",
        "G": "Final gate receipts below",
        "H": "Excluded: private inputs, outputs, paths, caches, Qt source bundles",
        "I": "Remaining: human visual Excel acceptance; inspect any non-PASS gate",
        "J": "Next prompt must create and verify a new local handoff",
    }
    lines += [f"- {key}: {value}" for key, value in topics.items()]
    lines += ["", *[f"- {k}: {v['status']}" for k, v in receipts["GATES"].items()]]
    return ("\n".join(lines) + "\n").encode()


def inspect_archive(archive: Path) -> dict:
    files: dict[str, bytes] = {}
    total = 0
    try:
        if archive.stat().st_size > MAX_TOTAL:
            raise Unsafe("Oversized compressed archive")
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        raw_tar = decoder.decompress(archive.read_bytes(), max_length=MAX_TOTAL + 2 * 1024 * 1024)
        if not decoder.eof or decoder.unused_data:
            raise Unsafe("Truncated/concatenated archive")
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as tar:
            folded: set[str] = set()
            for member in tar:
                relative(member.name)
                if (
                    not member.isfile()
                    or member.name.casefold() in folded
                    or member.pax_headers
                    or member.linkname
                    or member.uid != 0
                    or member.gid != 0
                    or member.uname
                    or member.gname
                    or member.mode != 0o600
                    or member.size > MAX_FILE
                    or member.size < 0
                ):
                    raise Unsafe("Unsafe/duplicate archive member or metadata")
                folded.add(member.name.casefold())
                total += member.size
                if total > MAX_TOTAL or len(folded) > 2000:
                    raise Unsafe("Archive exceeds bounds")
                stream = tar.extractfile(member)
                if stream is None:
                    raise Unsafe("Missing member")
                data = stream.read(MAX_FILE + 1)
                scan(member.name, data)
                files[member.name] = data
        canonical = io.BytesIO()
        with tarfile.open(fileobj=canonical, mode="w:", format=tarfile.USTAR_FORMAT) as tar:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o600
                tar.addfile(info, io.BytesIO(data))
        if canonical.getvalue() != raw_tar:
            raise Unsafe("Unexpected archive metadata/trailer")
        manifest = json.loads(files["manifest.json"])
        expected_review = {"review/" + key + ".json" for key in RECEIPTS} | {
            "review/REPORT.md",
            "review/SOURCE_STATE.json",
            "review/DIFF.patch",
            "review/FILE_MANIFEST.json",
        }
        if {p for p in files if not p.startswith("source/")} != expected_review | {"manifest.json"}:
            raise Unsafe("Unexpected/missing review files")
        payload = {p: d for p, d in files.items() if p != "manifest.json"}
        if manifest["schema_version"] != SCHEMA or manifest["files"] != file_manifest(payload):
            raise Unsafe("Manifest checksum/size mismatch")
        if not re.fullmatch(
            r"coredescriptionw2e-handoff-\d{8}T\d{6}Z-[0-9a-f]{12}-[0-9a-f]{12}",
            manifest["handoff_id"],
        ):
            raise Unsafe("Invalid handoff identity")
        datetime.strptime(manifest["created_utc"], "%Y-%m-%dT%H:%M:%SZ")
        if manifest["source_fingerprint"] != fingerprint(files):
            raise Unsafe("Source fingerprint mismatch")
        state = json.loads(files["review/SOURCE_STATE.json"])
        if (
            state["source_fingerprint"] != manifest["source_fingerprint"]
            or state["head"] != manifest["head"]
            or state["source_files"]
            != file_manifest({p: d for p, d in files.items() if p.startswith("source/")})
        ):
            raise Unsafe("Source binding mismatch")
        if not re.fullmatch(r"[0-9a-f]{40}", state["head"]) or type(state["dirty"]) is not bool:
            raise Unsafe("Invalid source state")
        file_list = json.loads(files["review/FILE_MANIFEST.json"])
        if file_list != file_manifest(
            {p: d for p, d in payload.items() if p != "review/FILE_MANIFEST.json"}
        ):
            raise Unsafe("File manifest mismatch")
        receipts = {k: json.loads(files[f"review/{k}.json"]) for k in RECEIPTS}
        validate_receipts(receipts)
        # Explicit fresh extraction of validated regular files only; no tar metadata applied.
        with tempfile.TemporaryDirectory(prefix="w2e-verify-") as temp:
            base = Path(temp).resolve()
            for name, data in files.items():
                target = base.joinpath(*relative(name).parts)
                if not target.resolve().is_relative_to(base):
                    raise Unsafe("Escaping extraction")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(data)
            extracted = {
                p.relative_to(base).as_posix(): p.read_bytes()
                for p in base.rglob("*")
                if p.is_file()
            }
            if file_manifest(extracted) != file_manifest(files):
                raise Unsafe("Fresh extract differs")
        return manifest
    except (
        KeyError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
        tarfile.TarError,
        EOFError,
        lzma.LZMAError,
        OSError,
    ) as exc:
        raise Unsafe("Invalid/incomplete archive") from exc


def checksum(archive: Path) -> str:
    with archive.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(archive: Path) -> dict:
    sidecar = Path(str(archive) + ".sha256")
    if sidecar.is_symlink() or archive.is_symlink():
        raise Unsafe("Symlink archive")
    if sidecar.read_text(encoding="ascii").strip() != f"{checksum(archive)}  {archive.name}":
        raise Unsafe("External checksum mismatch")
    manifest = inspect_archive(archive)
    if archive.name != manifest["handoff_id"] + ".tar.xz":
        raise Unsafe("Archive identity mismatch")
    return manifest


def create(root: Path, output: Path, evidence: Path | None = None) -> Path:
    files, state, patch = snapshot(root)
    receipts = default_receipts()
    automatic = root / "artifacts/handoff-evidence.json"
    if evidence is None and automatic.is_file():
        candidate = json.loads(automatic.read_text(encoding="utf-8"))
        if (
            candidate.get("source_fingerprint") == state["source_fingerprint"]
            and candidate.get("source_head") == state["head"]
        ):
            evidence = automatic
        else:
            receipts["DIAGNOSTICS"]["codes"]["stale_evidence_ignored"] = 1
    if evidence:
        supplied = json.loads(evidence.read_text(encoding="utf-8"))
        if (
            supplied.get("source_fingerprint") != state["source_fingerprint"]
            or supplied.get("source_head") != state["head"]
        ):
            raise Unsafe("Stale evidence/source fingerprint")
        receipts = supplied["receipts"]
    validate_receipts(receipts)
    stamp = datetime.now(UTC)
    identity = (
        f"coredescriptionw2e-handoff-{stamp:%Y%m%dT%H%M%SZ}-"
        f"{state['head'][:12]}-{uuid.uuid4().hex[:12]}"
    )
    files["review/SOURCE_STATE.json"] = encode(state)
    files["review/DIFF.patch"] = patch
    files["review/REPORT.md"] = report(receipts)
    for key, value in receipts.items():
        files[f"review/{key}.json"] = encode(value)
    files["review/FILE_MANIFEST.json"] = encode(file_manifest(files))
    manifest = {
        "schema_version": SCHEMA,
        "handoff_id": identity,
        "created_utc": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "head": state["head"],
        "source_fingerprint": state["source_fingerprint"],
        "files": file_manifest(files),
    }
    files["manifest.json"] = encode(manifest)
    if sum(len(d) for d in files.values()) > MAX_TOTAL:
        raise Unsafe("Package too large")
    for name, data in files.items():
        scan(name, data)
    # Detect edits while collecting receipts; never bind an inconsistent snapshot.
    again, again_state, again_patch = snapshot(root)
    if again != {p: d for p, d in files.items() if p.startswith("source/")} or (
        again_state != state or again_patch != patch
    ):
        raise Unsafe("Source changed during handoff")
    output.mkdir(parents=True, exist_ok=True)
    final = output / (identity + ".tar.xz")
    sidecar = Path(str(final) + ".sha256")
    with tempfile.TemporaryDirectory(prefix=".handoff-", dir=output) as temp:
        staged = Path(temp) / final.name
        with tarfile.open(staged, "w:xz", format=tarfile.USTAR_FORMAT) as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(data))
        staged.chmod(0o600)
        staged_sum = Path(str(staged) + ".sha256")
        staged_sum.write_text(f"{checksum(staged)}  {final.name}\n", encoding="ascii")
        staged_sum.chmod(0o600)
        verify(staged)
        if final.exists() or sidecar.exists():
            raise Unsafe("Handoff identity collision")
        # Both are verified before publication. If interrupted between renames, verify fails closed.
        os.replace(staged_sum, sidecar)
        os.replace(staged, final)
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "verify"])
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--evidence",
        type=Path,
        help="Private typed receipts bound to the current source fingerprint",
    )
    args = parser.parse_args()
    try:
        if args.action == "create":
            root = Path(__file__).resolve().parents[1]
            archive = create(root, root / "artifacts/handoff", args.evidence)
        else:
            if args.archive is None:
                parser.error("verify requires --archive")
            archive = args.archive
        manifest = verify(archive)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "handoff_id": manifest["handoff_id"],
                    "archive": str(archive.resolve()),
                    "sidecar": str(Path(str(archive) + ".sha256").resolve()),
                    "size": archive.stat().st_size,
                    "sha256": checksum(archive),
                }
            )
        )
        return 0
    except Unsafe, OSError, ValueError, subprocess.CalledProcessError:
        # No exception contents, paths, raw diff or document text in failure stdout.
        print('{"status":"FAIL","code":"handoff_rejected"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
