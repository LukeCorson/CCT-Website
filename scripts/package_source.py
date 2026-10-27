"""Build and verify a source-only ZIP for another desktop computer.

Run with ordinary Python 3.11+: python scripts/package_source.py
The allowlist deliberately excludes experiment data and local hardware profiles.
"""

import argparse
import hashlib
import json
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


VERSION = "0.5.0"
ROOT_FILES = frozenset({
    "THIRD_PARTY_NOTICES.txt",
    "requirements-build.txt",
    "README.md", "pyproject.toml", "requirements.txt", "requirements-lock.txt",
    "Setup CCT Control.cmd", "Start CCT Control.cmd",
    "Setup CCT Control.command", "Start CCT Control.command",
})
EXCLUDED_PARTS = frozenset({
    "__pycache__", ".git", ".pio", ".venv", "venv", "data", "logs", "log",
    "output", "artifacts", "tmp", "temp", ".pytest_cache", ".mypy_cache",
})


def allowed_source_name(name):
    """Only explicit source areas/extensions; archive names always use '/'."""
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        return False
    if any(part.casefold() in EXCLUDED_PARTS for part in path.parts):
        return False
    if path.name.casefold() == "hardware.local.json":
        return False
    if len(path.parts) == 1:
        return name in ROOT_FILES
    top = path.parts[0]
    if top == "config":
        return name == "config/hardware.example.json"
    if top == "cct":
        return path.suffix == ".py"
    if top == "docs":
        return path.suffix == ".md"
    if top == "tests":
        return path.suffix == ".py"
    if top == "scripts":
        return path.suffix in {".py", ".ps1", ".sh"}
    if top == "firmware":
        return name == "firmware/platformio.ini" or (len(path.parts) >= 3 and path.parts[1] in {"src", "include"} and path.suffix in {".c", ".cpp", ".h", ".hpp"})
    return False


def source_files(root):
    root = Path(root).resolve()
    candidates = [root / name for name in ROOT_FILES]
    for folder in ("cct", "firmware", "config", "docs", "tests", "scripts"):
        directory = root / folder
        if directory.is_dir() and not directory.is_symlink():
            candidates.extend(directory.rglob("*"))
    selected = []
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            continue
        name = path.relative_to(root).as_posix()
        if allowed_source_name(name):
            selected.append(path)
    return sorted(set(selected), key=lambda path: path.relative_to(root).as_posix())


def verify_package(path):
    """Validate privacy/path allowlist and exact hash coverage, without extracting."""
    with zipfile.ZipFile(path, "r") as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError("Archive contains duplicate names.")
        if len(entries) > 10000 or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
            raise ValueError("Source archive exceeds the expected size limits.")
        for entry in entries:
            name = entry.filename
            if name != "MANIFEST.json" and not allowed_source_name(name):
                raise ValueError(f"Unexpected or private file in source archive: {name}")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Source archives must not contain symbolic links.")
        if "MANIFEST.json" not in names:
            raise ValueError("Source manifest is missing.")
        manifest = json.loads(archive.read("MANIFEST.json").decode("utf-8"))
        if manifest.get("format") != "cct-control-source-v1":
            raise ValueError("Unsupported source manifest format.")
        files = manifest.get("files")
        if not isinstance(files, list) or not all(isinstance(record, dict) for record in files):
            raise ValueError("Source manifest file records are invalid.")
        listed = [record.get("path") for record in files]
        if len(listed) != len(set(listed)) or set(listed) != set(names) - {"MANIFEST.json"}:
            raise ValueError("Manifest does not match archive contents exactly.")
        for record in files:
            content = archive.read(record["path"])
            if record.get("size") != len(content) or record.get("sha256") != hashlib.sha256(content).hexdigest():
                raise ValueError(f"Source file failed integrity verification: {record['path']}")
        return manifest


def build_package(root=None, destination=None):
    root = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]
    destination = Path(destination) if destination is not None else root / "output" / f"CCT-Control-{VERSION}-source.zip"
    destination = destination.resolve()
    if destination.suffix.lower() != ".zip":
        raise ValueError("Source package destination must end in .zip.")
    paths = source_files(root)
    if not any(path.relative_to(root).as_posix() == "cct/app.py" for path in paths):
        raise ValueError("The chosen folder does not contain cct/app.py.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".zip.tmp")
    manifest = {"format": "cct-control-source-v1", "version": VERSION,
                "created_utc": datetime.now(timezone.utc).isoformat(), "files": []}
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source in paths:
                name = source.relative_to(root).as_posix()
                content = source.read_bytes()
                info = zipfile.ZipInfo(name, date_time=tuple(datetime.now().timetuple())[:6])
                info.create_system = 3
                mode = 0o100755 if source.suffix in {".sh", ".command"} else 0o100644
                info.external_attr = mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
                manifest["files"].append({"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
            archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        verify_package(temporary)
        temporary.replace(destination)
    except Exception:
        if temporary.is_file():
            temporary.unlink()
        raise
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Project root; defaults to this script's parent project")
    parser.add_argument("--output", type=Path, help="Destination .zip; defaults to output/CCT-Control-0.4.0-source.zip")
    args = parser.parse_args(argv)
    destination = build_package(args.root, args.output)
    manifest = verify_package(destination)
    print(f"Verified {len(manifest['files'])} source files: {destination}")
    print("Excluded: run data, recipes, logs, local hardware profiles, build outputs, caches, and virtual environments.")
    for record in manifest["files"]:
        print(record["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
