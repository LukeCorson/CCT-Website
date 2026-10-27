"""Small Qt integration checks for the operator workflow (no real devices)."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from cct.contracts import DEFAULT_CONFIG
from cct.service import LabService
from cct.ui.window import MainWindow


class OperatorWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = LabService(data_root=Path(self.temp.name))
        self.window = MainWindow(self.service)
        self.window.timer.stop()

    def tearDown(self):
        if self.service.snapshot().get("running"):
            self.service.stop()
            for _ in range(1000):
                if not self.service.snapshot().get("running"):
                    break
                self.service.update(1)
        self.window.refresh()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_startup_never_connects_or_runs(self):
        self.assertFalse(self.service.snapshot()["connected"])
        self.assertFalse(self.service.snapshot()["running"])
        self.assertFalse(self.window.setup.start_btn.isEnabled())
        self.assertEqual(self.window.live.metrics["co2_ppm"].value.text(), "—")

    def test_internal_fixture_workflow_and_connection_lock(self):
        self.window.connect_service("simulation", "")
        self.assertTrue(self.service.snapshot()["connected"])
        self.assertFalse(self.service.snapshot()["running"])
        self.window.show_page(0)
        self.window.setup.start_btn.click()
        self.assertTrue(self.service.snapshot()["running"])
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertFalse(self.window.devices.disconnect_btn.isEnabled())
        self.assertTrue(self.window.persistent_stop.isVisibleTo(self.window))

    def test_invalid_metadata_cannot_start(self):
        self.window.connect_service("simulation", "")
        self.window.setup.inputs["notebook_id"].setText("")
        self.window.setup.start_btn.click()
        self.assertFalse(self.service.snapshot()["running"])
        self.assertTrue(self.window.setup.validation.text())

    def test_test_selection_preserves_sample_and_defaults_to_continuous(self):
        self.window.setup.inputs["sample_id"].setText("Material batch 8")
        for name in ("zero", "adsorption", "breakthrough", "desorption"):
            self.window.setup.test_selector.setCurrentIndex(self.window.setup.test_selector.findData(name))
            config = self.window.setup.config()
            self.assertEqual(config["sample_id"], "Material batch 8")
            self.assertEqual(config["test_type"], name)
            self.assertFalse(config["automatic_cutoff_enabled"])
            self.assertEqual(config["cycles"], 1)
            self.assertEqual(config["initial_treatment"], "none")
            self.assertFalse(config["final_purge"])

    def test_automatic_cutoff_requires_explicit_opt_in(self):
        setup = self.window.setup
        self.window.show_page(0)
        setup.advanced_toggle.setChecked(True)
        self.assertFalse(setup.cutoff_controls.isVisibleTo(setup))
        setup.automatic.setChecked(True)
        self.assertTrue(setup.cutoff_controls.isVisibleTo(setup))
        self.assertTrue(setup.config()["automatic_cutoff_enabled"])
        setup.test_selector.setCurrentIndex(setup.test_selector.findData("zero"))
        self.assertFalse(setup.config()["automatic_cutoff_enabled"])
        self.assertFalse(setup.cutoff_controls.isVisibleTo(setup))

    def test_hardware_blockers_disable_run(self):
        self.window.setup.update_state({"connected": True, "running": False, "mode": "hardware", "blockers": ["Valve routes are not commissioned"]})
        self.assertFalse(self.window.setup.start_btn.isEnabled())
        self.assertIn("Valve routes", self.window.setup.status.text())

    def test_normalized_history_uses_each_cycles_recorded_reference(self):
        rows = [{"elapsed_s": 1, "co2_ppm": 100, "baseline_ppm": 400, "breakthrough_ratio": 0.25}, {"elapsed_s": 2, "co2_ppm": 100, "baseline_ppm": 200, "breakthrough_ratio": 0.5}]
        self.window.live.plots.update_data(rows, baseline=999, threshold=0.1)
        _, values = self.window.live.plots.curves["ratio"].getData()
        self.assertEqual(list(values), [0.25, 0.5])

    def test_cycle_overlay_excludes_other_stages_and_aligns_each_switch(self):
        rows = [{"elapsed_s": 20, "stage_elapsed_s": 2, "stage": "breakthrough", "cycle": 1, "breakthrough_ratio": 0.1}, {"elapsed_s": 100, "stage_elapsed_s": 2, "stage": "breakthrough", "cycle": 2, "breakthrough_ratio": 0.2}, {"elapsed_s": 200, "stage": "purge_release", "cycle": 2, "breakthrough_ratio": 0.5}]
        overlay = self.window.history.overlay
        overlay.set_rows(rows)
        curves = overlay.plot.listDataItems()
        self.assertEqual(len(curves), 2)
        self.assertEqual(list(curves[0].getData()[0]), [2 / 60])
        self.assertEqual(list(curves[1].getData()[1]), [0.2])

    def test_navigation_has_only_the_four_operator_pages(self):
        self.assertEqual([nav.accessibleName() for nav in self.window.nav], ["Tests", "Live test", "Results", "Connections"])
        self.assertFalse(hasattr(self.window, "manual"))
        self.assertFalse(hasattr(self.window, "recipes"))
        self.assertFalse(self.window.live.plots.plot_ratio.isVisibleTo(self.window.live.plots))

    def test_connection_options_are_separate_and_leave_main_controls_available(self):
        self.window.devices.mode.setCurrentIndex(self.window.devices.mode.findData("hardware"))
        self.window.show_connection_settings()
        dialog = self.window._connection_dialog
        self.assertFalse(dialog.isModal())
        self.assertTrue(self.window.devices.profile_btn.isVisibleTo(dialog))
        self.assertFalse(self.window.devices.isAncestorOf(self.window.devices.profile_btn))
        self.window.close()
        self.assertFalse(dialog.isVisible())

    def test_basic_results_have_no_invented_percentage_threshold(self):
        self.window.history._current = "basic-test"
        self.window.history.show_run({"metadata": {"mode": "simulation", "recipe": dict(DEFAULT_CONFIG, test_type="breakthrough", automatic_cutoff_enabled=False, breakthrough_pct=None)}, "samples": [{"elapsed_s": 1, "co2_ppm": 50, "baseline_ppm": 400, "breakthrough_ratio": 0.125}], "events": [], "summary": {"cycles": [{"cycle": 1, "inlet_co2_ppm": 400, "threshold_ratio": None, "status": "complete"}]}})
        self.assertFalse(self.window.history.plots.threshold_line.isVisible())
        self.assertFalse(self.window.history.cycle_table.isVisibleTo(self.window.history))
        self.assertFalse(self.window.history.plots.ratio.isVisibleTo(self.window.history.plots))


if __name__ == "__main__":
    unittest.main()
