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

    def test_web_controller_requires_operator_and_presents_s3_simulator(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = WebController(LabService(folder))
            controller.connect("simulation")
            snapshot = controller.snapshot()
            self.assertIn("s3", snapshot["devices"])
            self.assertNotIn("core2", snapshot["devices"])
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

    def test_direct_s3_exposes_six_valves_and_no_heating(self):
        class FakeLink:
            def __init__(self, port):
                self.port = port
                self.valves = {f"valve_{number}": False for number in range(1, 7)}
            def connect(self): pass
            def close(self): pass
            def set_valves(self, valves):
                self.valves = dict(valves)
                return self.snapshot()
            def all_off(self):
                self.valves = {key: False for key in self.valves}
                return self.snapshot()
            def drain_events(self): return []
            def snapshot(self):
                return {"connected": True, "fresh": True, "message": "confirmed",
                        "applied_valves": dict(self.valves), "heating_available": False}

        with tempfile.TemporaryDirectory() as folder:
            controller = WebController(LabService(folder), s3_factory=FakeLink)
            controller.connect("direct_s3", port="COM9")
            state = controller.snapshot()
            self.assertEqual(state["controller_target"], "direct_s3")
            self.assertFalse(state["devices"]["s3"]["heating_available"])
            valves = {f"valve_{number}": number in (1, 6) for number in range(1, 7)}
            controller.manual("set_valves", {"valves": valves})
            self.assertEqual(controller.snapshot()["outputs"]["valves"], valves)
            with self.assertRaisesRegex(RuntimeError, "manual Valve"):
                controller.start_test({"operator": "QA", "test_type": "zero"})

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
