"""Cable pairing stays passive and separate from actual instrument verification."""

import json
import os
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cct.ui.connections import ConnectionSettingsPage as DevicesPage, ROLES


def inventory():
    return [{"port": f"COM{index + 7}", "usb_serial": f"USB-{role}", "vid": 1027, "pid": 24577, "description": "USB serial adapter", "manufacturer": "Adapter maker", "location": str(index)} for index, role in enumerate(ROLES)]


def profile():
    return {"schema_version": 1, "commissioned": True, "commissioning": {"operator": "Existing reviewer", "plumbing_evidence": "Verified physical routes"}, "routes": {"vent": [{"mask": 0, "wait_ms": 100}]}, "devices": {role: {"port": f"COM{index + 1}", "expected_usb_serial": f"USB-{role}", "baud": 9600} for index, role in enumerate(ROLES)}}


class ConnectionsOperatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.enumerations = []

        def passive_scan():
            self.enumerations.append(QThread.currentThread() != self.app.thread())
            return inventory()

        self.page = DevicesPage(discovery_callback=passive_scan)
        self.requests = []
        self.saves = []
        self.page.connect_requested.connect(lambda mode, path: self.requests.append((mode, path)))
        self.page.save_pairing_requested.connect(self.saves.append)

    def wait_for_workers(self):
        deadline = time.monotonic() + 4
        while self.page._workers and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertFalse(self.page._workers)

    def tearDown(self):
        self.wait_for_workers()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def hardware(self):
        self.page.mode.setCurrentIndex(self.page.mode.findData("hardware"))
        self.page.set_profile(profile(), path="reviewed-profile.json")

    def scan(self):
        self.page.scan_btn.click()
        self.wait_for_workers()

    def test_startup_never_scans_or_opens_a_port_and_has_no_simulator_option(self):
        with patch("serial.Serial", side_effect=AssertionError("No serial port may be opened")):
            self.app.processEvents()
            self.assertEqual(self.enumerations, [])
            self.page.connect_btn.click()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.page.mode.findData("simulation"), -1)

    def test_explicit_scan_runs_off_ui_thread_and_matches_stable_identities(self):
        self.hardware()
        self.assertEqual(self.enumerations, [])
        self.assertFalse(self.page.connect_btn.isEnabled())
        with patch("serial.Serial", side_effect=AssertionError("Enumeration must not probe instruments")):
            self.scan()
        self.assertEqual(self.enumerations, [True])
        self.assertTrue(self.page.connect_btn.isEnabled())
        for index, role in enumerate(ROLES):
            self.assertEqual(self.page.adapter_selectors[role].currentData()["port"], f"COM{index + 7}")
            self.assertIn("not yet verified", self.page.device_labels[role][0].text())
            self.assertIn("not verified", self.page.device_labels[role][1].text())
        self.assertEqual(self.requests, [])

    def test_changed_pairing_requires_label_confirmation_and_loses_commissioned_flag(self):
        original = profile()
        self.page._discover = lambda: inventory() + [{"port": "COM20", "usb_serial": "USB-new", "vid": 1027, "pid": 24577, "description": "Replacement adapter"}]
        self.page.mode.setCurrentIndex(self.page.mode.findData("hardware"))
        self.page.set_profile(original, path="existing-profile.json")
        self.scan()
        self.page.adapter_selectors["core2"].setCurrentIndex(5)
        self.assertFalse(self.page._draft["commissioned"])
        self.assertTrue(original["commissioned"])
        self.assertEqual(self.page._draft["commissioning"], original["commissioning"])
        self.assertFalse(self.page.connect_btn.isEnabled())
        self.assertFalse(self.page.save_btn.isEnabled())
        self.page.save_btn.click()
        self.assertEqual(self.saves, [])
        self.page.labels_confirmed.setChecked(True)
        self.page.save_btn.click()
        self.assertEqual(len(self.saves), 1)
        self.assertFalse(self.saves[0]["commissioned"])
        self.assertEqual(self.saves[0]["devices"]["core2"]["expected_usb_serial"], "USB-new")
        self.assertEqual(self.page.profile_path, "existing-profile.json")

    def test_import_and_save_request_never_overwrite_the_imported_file(self):
        with tempfile.TemporaryDirectory() as folder:
            imported = Path(folder) / "apparatus.json"
            source = json.dumps(profile())
            imported.write_text(source, encoding="utf-8")
            self.page.set_profile(imported)
            self.wait_for_workers()
            self.page.mode.setCurrentIndex(self.page.mode.findData("hardware"))
            self.scan()
            self.page.labels_confirmed.setChecked(True)
            self.page.save_btn.click()
            self.assertEqual(len(self.saves), 1)
            self.assertEqual(imported.read_text(encoding="utf-8"), source)

    def test_ambiguous_or_missing_adapters_cannot_be_saved_or_connected(self):
        self.hardware()
        self.page._discover = lambda: inventory()[:-1] + [dict(inventory()[0], port="COM30")]
        self.scan()
        self.assertIn("ambiguous", self.page.device_labels["core2"][0].text())
        self.assertIn("missing", self.page.device_labels["calibrator"][0].text())
        self.assertFalse(self.page.labels_confirmed.isEnabled())
        self.assertFalse(self.page.save_btn.isEnabled())
        self.assertFalse(self.page.connect_btn.isEnabled())
        self.assertEqual(self.requests, [])

    def test_active_connection_locks_pairing_and_distinguishes_real_verification(self):
        self.hardware()
        self.scan()
        self.page.update_state({"connected": True, "running": True, "mode": "hardware", "devices": {role: {"connected": True, "fresh": True, "message": "Verified response"} for role in ROLES}})
        self.assertFalse(self.page.scan_btn.isEnabled())
        self.assertFalse(self.page.profile_btn.isEnabled())
        self.assertFalse(self.page.disconnect_btn.isEnabled())
        self.assertFalse(self.page.adapter_selectors["core2"].isEnabled())
        self.assertIn("Instrument communicating", self.page.device_labels["core2"][1].text())
        self.assertFalse(self.page.set_profile(profile()))

    def test_scan_failure_invalidates_previous_matching_without_modal_dialogs(self):
        self.hardware()
        self.scan()
        self.assertTrue(self.page.connect_btn.isEnabled())

        def fail():
            raise RuntimeError("Enumeration unavailable")

        self.page._discover = fail
        self.scan()
        self.assertFalse(self.page.connect_btn.isEnabled())
        self.assertIn("Enumeration unavailable", self.page.error.text())

    def test_slow_scan_does_not_block_the_operator_thread(self):
        release = threading.Event()
        self.page._discover = lambda: (release.wait(2), inventory())[1]
        self.hardware()
        started = time.monotonic()
        self.page.scan_btn.click()
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertFalse(self.page.scan_btn.isEnabled())
        release.set()
        self.wait_for_workers()

    def test_import_rejects_duplicate_json_roles_without_rewriting_them(self):
        with tempfile.TemporaryDirectory() as folder:
            imported = Path(folder) / "ambiguous-profile.json"
            source = '{"schema_version":1,"devices":{"core2":{"port":"COM1"},"core2":{"port":"COM2"}}}'
            imported.write_text(source, encoding="utf-8")
            self.page.set_profile(imported)
            self.wait_for_workers()
            self.assertIn("Duplicate", self.page.error.text())
            self.assertIsNone(self.page._draft)
            self.assertEqual(imported.read_text(encoding="utf-8"), source)

    def test_report_is_explicit_and_teardown_wait_is_available(self):
        reports = []
        self.page.diagnostic_requested.connect(lambda: reports.append(True))
        self.assertEqual(reports, [])
        self.page.diagnostic_btn.click()
        self.assertEqual(reports, [True])
        self.assertEqual(self.enumerations, [])
        self.assertIn("excludes", self.page.diagnostic_btn.toolTip())
        self.assertTrue(self.page.wait_for_done(1))


if __name__ == "__main__":
    unittest.main()
