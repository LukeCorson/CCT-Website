"""Platform data conventions and privacy checks for shared source packages."""

import importlib.util
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from cct.contracts import default_data_root


_SPEC = importlib.util.spec_from_file_location("cct_package_source", Path(__file__).resolve().parents[1] / "scripts" / "package_source.py")
packager = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(packager)


class DataDirectoryTests(unittest.TestCase):
    def data_path(self, platform, environment):
        with patch("sys.platform", platform), patch.dict(os.environ, environment, clear=True), patch.object(Path, "home", return_value=Path("C:/test-user")):
            return default_data_root()

    def test_windows_uses_local_application_data(self):
        self.assertEqual(self.data_path("win32", {"LOCALAPPDATA": "C:/LocalData"}), Path("C:/LocalData/CCTControl"))

    def test_windows_fallback_is_user_specific(self):
        self.assertEqual(self.data_path("win32", {}), Path("C:/test-user/AppData/Local/CCTControl"))

    def test_macos_uses_application_support(self):
        self.assertEqual(self.data_path("darwin", {}), Path("C:/test-user/Library/Application Support/CCTControl"))

    def test_linux_uses_xdg_when_absolute(self):
        # Use the host's absolute syntax: these branch tests also run on Windows.
        xdg = str(Path.cwd() / "xdg-user-data")
        self.assertEqual(self.data_path("linux", {"XDG_DATA_HOME": xdg}), Path(xdg) / "cct-control")

    def test_linux_ignores_relative_xdg(self):
        self.assertEqual(self.data_path("linux", {"XDG_DATA_HOME": "relative/folder"}), Path("C:/test-user/.local/share/cct-control"))

    def test_override_wins_and_blank_override_is_ignored(self):
        self.assertEqual(self.data_path("darwin", {"CCT_DATA_DIR": "C:/Experiment Data"}), Path("C:/Experiment Data"))
        self.assertEqual(self.data_path("darwin", {"CCT_DATA_DIR": "   "}), Path("C:/test-user/Library/Application Support/CCTControl"))


class SourcePackageTests(unittest.TestCase):
    def test_archive_contains_only_public_source_and_valid_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = {
                "cct/app.py", "cct/ui/window.py", "README.md", "requirements.txt", "requirements-lock.txt", "pyproject.toml",
                "Start CCT Control.cmd", "Setup CCT Control.command", "scripts/setup.sh", "scripts/package_source.py",
                "tests/test_portability.py", "docs/compatibility.md", "firmware/src/main.cpp", "firmware/include/local.h",
                "firmware/platformio.ini", "config/hardware.example.json",
            }
            private = {
                "data/runs/private/run.sqlite", "data/recipes/secret.json", "logs/app.log", "output/recording.csv",
                "config/hardware.local.json", "config/personal.json", "cct/__pycache__/app.pyc", "cct/data/secret.py",
                ".venv/Lib/site-packages/secret.py", "firmware/.pio/build/firmware.bin", "artifacts/plot.png",
                "tmp/sample.json", "docs/raw_measurements.csv", "scripts/local_settings.json", "private.txt",
            }
            for name in public | private:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("public source" if name in public else "PRIVATE_SENTINEL", encoding="utf-8")
            destination = packager.build_package(root)
            manifest = packager.verify_package(destination)
            self.assertEqual({record["path"] for record in manifest["files"]}, public)
            with zipfile.ZipFile(destination) as archive:
                self.assertTrue(all("\\" not in name and not name.startswith("/") for name in archive.namelist()))
                self.assertFalse(any(b"PRIVATE_SENTINEL" in archive.read(name) for name in archive.namelist()))
                self.assertEqual(archive.getinfo("scripts/setup.sh").external_attr >> 16 & 0o777, 0o755)
            self.assertFalse(destination.with_suffix(".zip.tmp").exists())

    def test_absolute_traversal_and_private_paths_are_rejected(self):
        for name in ("/cct/app.py", "../cct/app.py", "cct/../app.py", "cct\\app.py", "C:/cct/app.py", "config/hardware.local.json", "cct/__pycache__/private.py", "data/run.sqlite", "output/cct/app.py"):
            with self.subTest(name=name):
                self.assertFalse(packager.allowed_source_name(name))

    def test_verifier_rejects_unlisted_or_private_archive_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("config/hardware.local.json", "PRIVATE_SENTINEL")
                archive.writestr("MANIFEST.json", "{}")
            with self.assertRaisesRegex(ValueError, "private"):
                packager.verify_package(path)


if __name__ == "__main__":
    unittest.main()
