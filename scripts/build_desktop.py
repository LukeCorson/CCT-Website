"""Build a native one-folder app on the current OS (not a cross-compiler).

Install development tooling once: python -m pip install 'PyInstaller>=6.12,<7'
Then run: python scripts/build_desktop.py
Build output is new and timestamped; previous packages and user data stay intact.
"""

import hashlib
import importlib.util
import os
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cct import __version__


def main():
    if importlib.util.find_spec("PyInstaller") is None:
        raise SystemExit("Install build tooling first: python -m pip install 'PyInstaller>=6.12,<7'")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    system = platform.system().lower()
    output = ROOT / "output" / f"desktop-build-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-m", "PyInstaller", "--onedir", "--windowed", "--noupx",
               "--name", "CCTControl", "--distpath", str(output / "dist"),
               "--workpath", str(output / "work"), "--specpath", str(output),
               "--paths", str(ROOT), "--add-data", f"{ROOT / 'config' / 'hardware.example.json'}:config",
               "--add-data", f"{ROOT / 'docs'}:docs", "--add-data", f"{ROOT / 'THIRD_PARTY_NOTICES.txt'}:."]
    for package in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "pyqtgraph", "numpy", "pyserial", "shiboken6"):
        command.extend(["--copy-metadata", package])
    for module in ("tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick",
                   "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtMultimedia", "matplotlib", "pandas", "scipy", "IPython", "pytest"):
        command.extend(["--exclude-module", module])
    command.append(str(ROOT / "scripts" / "desktop_entry.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    folder = output / "dist" / "CCTControl"
    if not folder.is_dir():
        raise RuntimeError("The native build did not produce the expected application folder.")
    # Some build environments put a different ICU distribution on PATH. Qt's
    # Windows build may use the OS ICU ABI instead; accidentally bundling the
    # unrelated DLL produces a missing-procedure error before the app starts.
    # Match the library actually loaded by Qt, retaining rejected copies only
    # in the build work area rather than the distributable application.
    if sys.platform == "win32":
        import ctypes
        import PySide6.QtCore
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        path_buffer = ctypes.create_unicode_buffer(32768)
        handle = kernel.GetModuleHandleW("icuuc.dll")
        if handle and kernel.GetModuleFileNameW(ctypes.c_void_p(handle), path_buffer, len(path_buffer)):
            system_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32"
            if Path(path_buffer.value).resolve().parent == system_directory.resolve():
                candidate = folder / "_internal" / "icuuc.dll"
                if candidate.is_file() and candidate.resolve().is_relative_to(folder.resolve()):
                    rejected = output / "excluded-build-dependencies"
                    rejected.mkdir(exist_ok=True)
                    candidate.rename(rejected / "icuuc.dll")
    # A compile is not a working package. Verify the frozen executable before
    # producing a ZIP; this check never connects equipment or starts a run.
    if sys.platform == "win32":
        screenshot = output / "validation-startup.png"
        environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        subprocess.run([str(folder / "CCTControl.exe"), "--data-dir", str(output / "validation-data"),
                        "--screenshot", str(screenshot)], cwd=output, env=environment, check=True, timeout=45)
        if not screenshot.is_file() or screenshot.stat().st_size < 1000:
            raise RuntimeError("The frozen executable did not render its startup screen; no package was published.")
    quickstart = (
        f"CCT Control {__version__} — {system} native build\n\n"
        "Extract the entire ZIP before opening the application. Keep the application and _internal folder together.\n"
        "Windows: double-click CCTControl.exe. Python and Codex are not required.\n"
        "USB adapter drivers may still need installation by your lab administrator.\n\n"
        "Select devices in Connections, then import your reviewed profile through Connection options,\n"
        "check cable labels and explicitly connect. Alicat and LI-COR offer manual modes without app readings.\n"
        "There is no simulator in the operator app. No real apparatus profile or experiment data\n"
        "is included. Pairing alone does not commission valves, gas selection, heating or shutdown.\n\n"
        "Records are stored in your OS user-data folder, not beside the executable. Use Results to export\n"
        "or import finished tests. Never unplug the control computer as the normal way to end a test.\n\n"
        "This build is unsigned and has not been tested on another physical computer.\n"
        "Verify the package through your lab's normal software approval process before lab use.\n"
    )
    (folder / "READ ME FIRST.txt").write_text(quickstart, encoding="utf-8")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.txt", folder / "THIRD_PARTY_NOTICES.txt")
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    manifest = {"version": __version__, "system": system, "machine": platform.machine(), "created_utc": stamp,
                "files": [{"path": path.relative_to(folder).as_posix(), "size": path.stat().st_size,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files]}
    (folder / "PACKAGE-MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = ROOT / "output" / f"CCT-Control-{__version__}-{system}-{platform.machine()}-{stamp}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                handle.write(path, "CCTControl/" + path.relative_to(folder).as_posix())
    print(json.dumps({"folder": str(folder), "archive": str(archive), "source_files_only": False,
                      "machine": platform.machine(), "build_os": system}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
