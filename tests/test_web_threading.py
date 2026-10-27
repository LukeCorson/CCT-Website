import tempfile
import threading
import time
import unittest

from cct.service import LabService
from cct.web.controller import WebController


class WebThreadingTests(unittest.TestCase):
    def test_acquisition_and_http_style_actions_share_run_safely(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = WebController(LabService(folder))
            controller.connect("simulation")
            controller.service.set_speed(120)
            controller.start_loop()
            try:
                controller.start_test({"test_type": "zero", "operator": "QA", "sample_id": "thread"})
                time.sleep(0.35)
                errors = []

                def stop_from_request_thread():
                    try:
                        controller.stop_test()
                    except Exception as error:
                        errors.append(error)

                request = threading.Thread(target=stop_from_request_thread)
                request.start()
                request.join()
                deadline = time.monotonic() + 5
                while controller.snapshot().get("running") and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(errors)
                self.assertFalse(controller.snapshot().get("running"))
            finally:
                controller.close()
                controller.service.disconnect()


if __name__ == "__main__":
    unittest.main()
