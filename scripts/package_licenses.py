"""BUILD ONLY: verified sources + license texts, never imported by the application."""

import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
import tomllib
import zipfile
from importlib.metadata import distribution
from pathlib import Path
from urllib.request import urlopen

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parents[1]
QT_VERSION = "6.12.0"
QT_SOURCES = (
    (
        f"https://download.qt.io/official_releases/qt/6.12/{QT_VERSION}/submodules/qtbase-everywhere-src-{QT_VERSION}.tar.xz",
        "a951bd163c7b80fc6b8c88d7668fb56abf91c152373e13c10666763238131307",
    ),
    (
        f"https://download.qt.io/official_releases/qt/6.12/{QT_VERSION}/submodules/qtsvg-everywhere-src-{QT_VERSION}.tar.xz",
        "e4ab39534ec97987b1b9b60ef7f4d3253d5912a69f8c713a01753174a4029331",
    ),
    (
        f"https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{QT_VERSION}-src/pyside-setup-everywhere-src-{QT_VERSION}.tar.xz",
        "099c1a597cf33c1b000e11cf68a0b82d216fb802bf81726efc720394f10c7751",
    ),
)
LICENSE_NAME = re.compile(r"licen[sc]e|copying|copyright|notice|qt_attribution", re.I)


def download(url: str, sha256: str, folder: Path) -> Path:
    if not url.startswith(("https://files.pythonhosted.org/", "https://download.qt.io/")):
        raise RuntimeError("Unapproved source host")
    target = folder / url.rsplit("/", 1)[1]
    if not target.exists():
        temp = target.with_suffix(target.suffix + ".tmp")
        try:
            with urlopen(url, timeout=90) as response, temp.open("wb") as output:
                shutil.copyfileobj(response, output)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
    with target.open("rb") as stream:
        actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual_hash != sha256:
        raise RuntimeError(f"Source hash mismatch: {target.name}")
    return target


def extract_notices(archive: Path, folder: Path) -> int:
    """Copy text only, with generated filenames; never extract untrusted paths."""
    folder.mkdir(parents=True, exist_ok=True)
    count = 0

    def save(name: str, data: bytes) -> None:
        nonlocal count
        if len(data) > 4 * 1024 * 1024:
            raise RuntimeError("License entry too large")
        digest = hashlib.sha256(name.encode()).hexdigest()[:12]
        (folder / f"{digest}-{Path(name).name}").write_bytes(data)
        count += 1

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                if not item.is_dir() and LICENSE_NAME.search(item.filename):
                    save(item.filename, z.read(item))
    else:
        with tarfile.open(archive) as t:
            for member in t:
                if member.isfile() and LICENSE_NAME.search(member.name):
                    stream = t.extractfile(member)
                    assert stream is not None
                    save(member.name, stream.read(4 * 1024 * 1024 + 1))
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination
    sources = destination / "third-party-sources"
    notices = destination / "third-party-licenses"
    sources.mkdir(parents=True, exist_ok=True)
    notices.mkdir(parents=True, exist_ok=True)
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {p["name"]: p for p in lock["package"]}
    pending = [d["name"] for d in packages["coredescriptionw2e"]["dependencies"]] + ["pyinstaller"]
    selected = set()
    while pending:
        name = pending.pop()
        if name not in selected:
            selected.add(name)
            pending.extend(
                d["name"]
                for d in packages[name].get("dependencies", [])
                if not d.get("marker") or Marker(d["marker"]).evaluate()
            )
    inventory = []
    for name in sorted(selected):
        item = packages[name]
        installed = distribution(name)
        if installed.version != item["version"]:
            raise RuntimeError("Installed package does not match lock")
        row = {
            "name": name,
            "version": installed.version,
            "license": installed.metadata.get(
                "License-Expression", installed.metadata.get("License", "see source")
            ),
        }
        if name in {"pyside6-essentials", "shiboken6"}:
            if installed.version != QT_VERSION:
                raise RuntimeError("Update and review pinned Qt sources before building")
        elif "sdist" in item:
            source = item["sdist"]
            archive = download(source["url"], source["hash"].removeprefix("sha256:"), sources)
            count = extract_notices(archive, notices / name)
            if not count:
                raise RuntimeError(f"Missing license text: {name}")
            row.update({"source": archive.name, "sha256": source["hash"]})
        else:
            raise RuntimeError(f"No audited source distribution for {name}")
        # Wheel-specific bundled library notices, especially libxml2/libxslt.
        for file in installed.files or []:
            located = Path(str(installed.locate_file(file)))
            if LICENSE_NAME.search(str(file)) and located.is_file():
                target = (
                    notices
                    / name
                    / (hashlib.sha256(str(file).encode()).hexdigest()[:12] + "-" + Path(file).name)
                )
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(located, target)
        inventory.append(row)
    for url, sha256 in QT_SOURCES:
        archive = download(url, sha256, sources)
        count = extract_notices(archive, notices / archive.name)
        if not count:
            raise RuntimeError("Qt source has no license notices")
        inventory.append({"source": archive.name, "url": url, "sha256": sha256})
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.exists():
        # Developer Linux validation; official Windows Python has LICENSE.txt.
        python_license = (
            Path(sys.base_prefix)
            / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt"
        )
    if not python_license.exists():
        raise RuntimeError("Missing CPython license")
    shutil.copyfile(python_license, notices / "CPython-LICENSE.txt")
    for file in ("LICENSE", "README.md", "docs/THIRD_PARTY_NOTICES.md", "docs/RELEASE.md"):
        shutil.copyfile(ROOT / file, destination / Path(file).name)
    (destination / "third-party-inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
