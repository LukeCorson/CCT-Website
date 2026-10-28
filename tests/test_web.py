import tempfile
import unittest
from datetime import datetime, timezone

from cct.service import LabService
from cct.web.controller import WebController
from cct.web.naming import build_test_name


class WebNamingTests(unittest.TestCase):
    def test_test_name_is_stable_and_safe(self):
        when = datetime(2026, 10, 27, tzinfo=timezone.utc)
        self.assertEqual(build_test_name("Luke R.", "breakthrough", "Carbon #4", when),
                         "20261027-breakthrough-Carbon-4-Luke-R")

    def test_web_controller_requires_operator_and_presents_core2_simulator(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = WebController(LabService(folder))
            controller.connect("simulation")
            snapshot = controller.snapshot()
            self.assertIn("core2", snapshot["devices"])
            self.assertEqual(snapshot["controller_target"], "simulation")
            with self.assertRaisesRegex(ValueError, "who is running"):
                controller.start_test({"test_type": "zero", "operator": ""})
            controller.service.disconnect()

    def test_repeating_cycle_selection_uses_automatic_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = WebController(LabService(folder))
            controller.connect("simulation")
            controller.start_test({"test_type": "breakthrough_cycles", "operator": "LR",
                                   "sample_id": "cycles", "cycles": 2,
                                   "breakthrough_pct": 20, "initial_treatment": "none",
                                   "final_purge": False})
            snapshot = controller.service.snapshot()
            self.assertTrue(snapshot["automatic_cutoff_enabled"])
            self.assertEqual(controller.service.config["test_type"], "breakthrough")
            self.assertEqual(controller.service.config["cycles"], 2)
            controller.service.disconnect()

    def test_core2_controls_use_named_routes_and_local_heater_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = WebController(LabService(folder))
            controller.connect("simulation")
            controller.manual("route", {"name": "column"})
            controller.manual("heater", {"target_c": 150})
            state = controller.snapshot()
            self.assertEqual(state["outputs"]["route"], "column")
            self.assertEqual(state["outputs"]["heater_target_c"], 150)
            self.assertEqual(state["controller_target"], "simulation")
            controller.manual("safe_state", {})
            state = controller.snapshot()
            self.assertEqual(state["outputs"]["route"], "vent")
            self.assertIsNone(state["outputs"]["heater_target_c"])

    def test_generated_name_is_saved_with_run(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = WebController(LabService(folder))
            controller.connect("simulation")
            run_id = controller.start_test({"test_type": "zero", "operator": "LR", "sample_id": "A1"})
            self.assertIn("zero-A1-LR", run_id)
            controller.stop_test()
            for _ in range(20):
                controller.service.update(1)
                if not controller.service.snapshot()["running"]:
                    break
            controller.service.disconnect()


if __name__ == "__main__":
    unittest.main()
