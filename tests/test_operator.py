"""Operator plans, read-only preflight and durable observations; no hardware."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from cct.contracts import DEFAULT_CONFIG
from cct.operator import recovery_hint, test_plan
from cct.service import LabService
from cct.ui.preflight import PreflightDialog


class OperatorGuidanceTests(unittest.TestCase):
    def test_basic_test_plans_have_no_inherited_concentration_cutoff(self):
        for test_type in ("zero", "adsorption", "breakthrough", "desorption"):
            with self.subTest(test_type=test_type):
                plan = test_plan({"test_type": test_type})
                self.assertIn("until you choose End test", plan["ending"])
                self.assertNotIn("%", plan["ending"])
                self.assertNotIn("10%", " ".join(plan["steps"]))
                self.assertIn("vent route for 10 s", plan["shutdown"])
                self.assertIn("maximum per step", plan["safety"])

    def test_explicit_automatic_and_legacy_plans_preserve_selected_cycles(self):
        named = test_plan({"test_type": "breakthrough", "automatic_cutoff_enabled": True,
                           "breakthrough_pct": 32, "cycles": 7})
        self.assertIn("32%", " ".join(named["steps"]))
        self.assertIn("7 cycle", " ".join(named["steps"]))
        self.assertIn("Automatic sequence", named["ending"])
        legacy = test_plan(dict(DEFAULT_CONFIG, breakthrough_pct=25, cycles=2))
        self.assertIn("25%", " ".join(legacy["steps"]))
        self.assertIn("2 cycle", " ".join(legacy["steps"]))

    def test_adsorption_exhaust_and_desorption_nitrogen_are_explained(self):
        adsorption = test_plan({"test_type": "adsorption"})
        self.assertIn("exhaust", " ".join(adsorption["steps"]))
        self.assertIn("not the column-outlet", adsorption["measurement_note"])
        desorption = test_plan({"test_type": "desorption", "desorption_target_c": 140})
        self.assertIn("Heat under nitrogen to 140", " ".join(desorption["steps"]))

    def test_recovery_guidance_addresses_cause_without_automatic_resume(self):
        examples = (("Column temperature reached limit", "hot column"),
                    ("Recording disk failed", "preserve the existing records"),
                    ("Nitrogen flow lost", "supply, regulator, tubing and meter"),
                    ("LI-COR disconnected", "never resumes"),
                    ("An unexpected issue", "confirm the physical apparatus is safe"))
        for reason, expected in examples:
            with self.subTest(reason=reason):
                self.assertIn(expected, recovery_hint(reason))


class OperatorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = LabService(Path(self.temporary.name))

    def tearDown(self):
        if self.service.engine and self.service.engine.running:
            self.service.stop()
            for _ in range(1000):
                if not self.service.engine.running:
                    break
                self.service.update(1)
        self.service.disconnect()
        self.temporary.cleanup()

    def test_disconnected_preflight_does_not_connect_or_start(self):
        with patch("serial.Serial") as serial, patch("cct.drivers.hardware._Worker") as worker:
            report = self.service.preflight_report({"test_type": "zero"})
        serial.assert_not_called()
        worker.assert_not_called()
        self.assertFalse(report["ready"])
        self.assertTrue(report["blockers"])
        self.assertFalse(self.service.connected)
        self.assertIsNone(self.service.engine)
        self.assertEqual(self.service.list_runs(), [])

    def test_ready_preflight_does_not_actuate_or_create_an_experiment(self):
        self.service.connect()
        before = deepcopy(self.service.rig.snapshot()["outputs"])
        with patch.object(self.service.rig, "execute") as execute, patch("serial.Serial") as serial:
            report = self.service.preflight_report({"test_type": "zero"})
        self.assertTrue(report["ready"], report["blockers"])
        self.assertTrue(all(check["state"] == "ready" for check in report["checks"]))
        execute.assert_not_called()
        serial.assert_not_called()
        self.assertEqual(self.service.rig.snapshot()["outputs"], before)
        self.assertEqual(self.service.list_runs(), [])

    def test_preflight_blocks_missing_or_stale_measurements(self):
        self.service.connect()
        snapshot = self.service.rig.snapshot()
        for missing in (True, False):
            changed = deepcopy(snapshot)
            if missing:
                changed["values"]["column_temp_c"] = None
            else:
                changed["ages"]["column_temp_c"] = DEFAULT_CONFIG["stale_after_s"] + 1
            with self.subTest(missing=missing), patch.object(self.service.rig, "snapshot", return_value=changed), patch.object(self.service.rig, "execute") as execute:
                report = self.service.preflight_report({"test_type": "zero"})
                self.assertFalse(report["ready"])
                self.assertTrue(any("fresh Column temperature" in blocker for blocker in report["blockers"]))
                execute.assert_not_called()

    def test_storage_probe_failure_blocks_start_without_actuation(self):
        self.service.connect()
        with patch("cct.service.tempfile.TemporaryFile", side_effect=OSError("folder read only")), patch.object(self.service.rig, "execute") as execute:
            report = self.service.preflight_report({"test_type": "zero"})
        self.assertFalse(report["ready"])
        self.assertTrue(any("Recording folder needs attention" in blocker for blocker in report["blockers"]))
        execute.assert_not_called()
        self.assertFalse(self.service._storage_failed)

    def test_starter_profile_and_saved_copy_never_connect_or_commission(self):
        with patch("serial.Serial") as serial, patch("cct.drivers.hardware._Worker") as worker:
            starter = self.service.new_apparatus_profile()
            original = deepcopy(starter)
            path = self.service.save_apparatus_profile(starter)
        serial.assert_not_called()
        worker.assert_not_called()
        self.assertFalse(starter["commissioned"])
        self.assertEqual(starter, original)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)
        self.assertTrue(path.is_relative_to(self.service.store.root))
        self.assertFalse(self.service.connected)
        self.assertIsNone(self.service.engine)

    def test_profile_edit_is_blocked_during_hardware_session(self):
        self.service.connect()
        self.service.mode = "hardware"  # A synthetic state; no physical rig exists.
        with patch("cct.portable.save_apparatus_profile") as save:
            with self.assertRaisesRegex(RuntimeError, "Disconnect hardware"):
                self.service.save_apparatus_profile({"devices": {}})
        save.assert_not_called()

    def test_observation_is_saved_with_current_experiment_time(self):
        self.service.connect()
        run_id = self.service.start({"test_type": "zero"})
        self.service.update(3)
        self.service.add_observation("  The sample holder is secure.  ")
        run = self.service.load_run(run_id)
        note = [event for event in run["events"] if event["event"] == "operator_note"][-1]
        self.assertEqual(note["message"], "The sample holder is secure.")
        self.assertEqual(note["details"]["note"], note["message"])
        self.assertEqual(note["elapsed_s"], self.service.engine.elapsed)
        self.assertEqual(note["stage"], self.service.engine.stage)
        self.assertTrue(note["timestamp_utc"])

    def test_invalid_notes_are_rejected_without_faulting_running_test(self):
        with self.assertRaisesRegex(RuntimeError, "Start a test"):
            self.service.add_observation("No experiment exists")
        self.service.connect()
        self.service.start({"test_type": "zero"})
        for invalid in (None, "", "   ", "x" * 2001):
            with self.subTest(invalid_type=type(invalid).__name__):
                with self.assertRaises(ValueError):
                    self.service.add_observation(invalid)
                self.assertTrue(self.service.engine.running)
                self.assertFalse(self.service._storage_failed)

    def test_failed_observation_write_faults_and_requests_shutdown(self):
        self.service.connect()
        run_id = self.service.start({"test_type": "zero"})
        with patch.object(self.service.store, "append_event", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "observation could not be saved"):
                self.service.add_observation("Important observation")
        self.assertFalse(self.service.engine.running)
        self.assertEqual(self.service.engine.outcome, "fault")
        self.assertTrue(self.service._storage_failed)
        outputs = self.service.rig.snapshot()["outputs"]
        self.assertEqual(outputs["gas"], "off")
        self.assertIsNone(outputs["heater_target_c"])
        self.assertEqual(self.service.load_run(run_id)["metadata"]["status"], "fault")


class PhysicalReviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def report(self, ready=True):
        return {"ready": ready, "mode": "hardware", "checks": [],
                "blockers": [] if ready else ["Missing instrument"],
                "plan": test_plan({"test_type": "zero"}), "data_folder": "Synthetic test folder"}

    def test_hardware_start_requires_explicit_operator_checkbox(self):
        dialog = PreflightDialog(self.report(), {"test_type": "zero"}, allow_start=True)
        try:
            self.assertFalse(dialog.confirm.isChecked())
            self.assertFalse(dialog.confirm.isHidden())
            self.assertFalse(dialog.start_btn.isEnabled())
            dialog.start_btn.click()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            dialog.confirm.setChecked(True)
            self.assertTrue(dialog.start_btn.isEnabled())
            dialog.start_btn.click()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_blockers_and_review_only_mode_cannot_offer_hardware_start(self):
        blocked = PreflightDialog(self.report(False), {}, allow_start=True)
        review = PreflightDialog(self.report(), {}, allow_start=False)
        try:
            blocked.confirm.setChecked(True)
            self.assertFalse(blocked.start_btn.isEnabled())
            self.assertTrue(review.start_btn.isHidden())
            self.assertTrue(review.confirm.isHidden())
            self.assertTrue(review.cancel_btn.isDefault())
        finally:
            for dialog in (blocked, review):
                dialog.close()
                dialog.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
