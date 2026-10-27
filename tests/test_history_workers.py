"""Large history operations must not stall acquisition or replace newer selections."""

import os
import tempfile
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cct.contracts import DEFAULT_CONFIG
from cct.service import LabService
from cct.ui.window import MainWindow
from cct.ui.workers import bounded_rows


class HistoryResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = LabService(self.temp.name)
        self.window = MainWindow(self.service)
        self.window.timer.stop()

    def wait_for_workers(self):
        deadline = time.monotonic() + 4
        while self.window._workers and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertFalse(self.window._workers)

    def tearDown(self):
        self.wait_for_workers()
        self.window.refresh()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_background_read_does_not_block_acquisition_and_returns_on_ui_thread(self):
        release = threading.Event()
        received = []

        def slow_read():
            release.wait(2)
            return "finished"

        def on_result(value):
            received.append((value, QThread.currentThread() == self.app.thread()))

        started = time.monotonic()
        self.window._background(slow_read, (), on_result)
        self.assertLess(time.monotonic() - started, 0.2)
        self.service.connect()
        self.service.update(3)
        self.assertEqual(self.service.live_elapsed, 3)
        release.set()
        self.wait_for_workers()
        self.assertEqual(received, [("finished", True)])

    def test_older_history_selection_cannot_replace_the_newer_one(self):
        release_first = threading.Event()

        def load(run_id):
            if run_id == "first":
                release_first.wait(2)
            return {"metadata": {"notebook_id": run_id, "mode": "simulation", "recipe": dict(DEFAULT_CONFIG, notebook_id=run_id)},
                    "samples": [], "events": [], "summary": {}}

        self.service.load_run = load
        self.window.history._current = "first"
        self.window.load_run("first")
        self.window.history._current = "second"
        self.window.load_run("second")
        deadline = time.monotonic() + 2
        while "second" not in self.window.history.detail_title.text() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertIn("second", self.window.history.detail_title.text())
        release_first.set()
        self.wait_for_workers()
        self.assertIn("second", self.window.history.detail_title.text())

    def test_display_reduction_keeps_peaks_and_missing_data(self):
        original = [{"elapsed_s": i, "co2_ppm": 1.0} for i in range(30000)]
        original[12000]["co2_ppm"] = 900.0
        original[12001]["co2_ppm"] = -2.0
        original[15000]["co2_ppm"] = None
        display = bounded_rows(original, limit=1000)
        self.assertLessEqual(len(display), 1000)
        self.assertTrue(any(row["co2_ppm"] == 900 for row in display))
        self.assertTrue(any(row["co2_ppm"] == -2 for row in display))
        self.assertTrue(any(row["co2_ppm"] is None for row in display))
        self.assertEqual(len(original), 30000)

    def test_safe_state_closes_feed_and_turns_off_heat(self):
        self.window.connect_service("simulation", "")
        self.service.manual("gas", name="air")
        self.service.manual("heater", target_c=150)
        self.window.refresh()
        self.service.manual("safe_state")
        outputs = self.service.rig.snapshot()["outputs"]
        self.assertEqual(outputs["route"], "vent")
        self.assertEqual(outputs["gas"], "off")
        self.assertIsNone(outputs["heater_target_c"])


if __name__ == "__main__":
    unittest.main()
