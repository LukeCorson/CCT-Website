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

    def test_web_controller_requires_operator_and_presents_s3(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = WebController(LabService(folder))
            controller.connect("simulation")
            snapshot = controller.snapshot()
            self.assertIn("s3", snapshot["devices"])
            self.assertNotIn("core2", snapshot["devices"])
            with self.assertRaisesRegex(ValueError, "who is running"):
                controller.start_test({"test_type": "zero", "operator": ""})
            controller.service.disconnect()

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
