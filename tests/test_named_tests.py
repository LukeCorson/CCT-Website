"""Regression coverage for the simplified, no-hidden-cutoff operator workflow."""

import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from cct.contracts import DEFAULT_CONFIG
from cct.engine import ExperimentEngine, validate_config
from cct.named_tests import normalize_test_config
from cct.service import LabService
from cct.simulator import SimulationRig


class NamedTestEngineTests(unittest.TestCase):
    def make_engine(self, test_type, **changes):
        self.rig = SimulationRig()
        self.rig.connect()
        self.events = []
        config = normalize_test_config({**DEFAULT_CONFIG, "test_type": test_type, **changes})
        self.assertFalse(validate_config(config))
        self.engine = ExperimentEngine(self.rig, config, self.events.append)
        self.engine.start()
        return self.engine

    def advance(self, seconds):
        for _ in range(seconds):
            self.rig.tick(1, self.engine.stage)
            self.engine.step(1, self.rig.snapshot())

    def until(self, stage, limit=2000):
        for _ in range(limit):
            if self.engine.stage == stage:
                return
            self.advance(1)
        self.fail(f"Expected {stage}, got {self.engine.stage}: {self.engine.reason}")

    def test_zero_is_nitrogen_bypass_until_operator_ends(self):
        engine = self.make_engine("zero")
        self.until("zero")
        self.assertEqual(self.rig.outputs["route"], "zero")
        self.assertEqual(self.rig.outputs["gas"], "n2")
        self.advance(100)
        self.assertEqual(engine.stage, "zero")
        engine.stop()
        self.until("complete")
        self.assertEqual(engine.cycles_completed, 1)
        self.assertEqual(self.rig.outputs["gas"], "off")

    def test_adsorption_uses_its_own_exhaust_route(self):
        self.make_engine("adsorption")
        self.until("adsorption")
        self.assertEqual(self.rig.outputs["route"], "adsorption")
        self.assertEqual(self.rig.outputs["gas"], "air")
        self.assertIsNone(self.rig.outputs["heater_target_c"])
        self.advance(100)
        self.assertEqual(self.engine.stage, "adsorption")

    def test_basic_breakthrough_does_not_stop_at_old_ten_percent(self):
        engine = self.make_engine("breakthrough")
        self.assertIsNone(engine.threshold)
        self.until("breakthrough")
        self.advance(500)
        self.assertGreater(engine.ratio, 0.99)
        self.assertEqual(engine.stage, "breakthrough")
        self.assertEqual(engine.cycles_completed, 0)
        engine.stop()
        self.until("complete")
        self.assertNotIn("breakthrough_time_s", engine.summary["cycles"][0])
        self.assertEqual(engine.summary["cycles"][0]["status"], "ended_by_operator")

    def test_desorption_keeps_nitrogen_during_heating_and_holds_until_end(self):
        engine = self.make_engine("desorption")
        self.until("initial_heat")
        self.assertEqual(self.rig.outputs["gas"], "n2")
        self.until("desorption")
        self.advance(500)
        self.assertEqual(engine.stage, "desorption")
        self.assertEqual(self.rig.outputs["heater_target_c"], 150)
        engine.stop()
        self.assertIsNone(self.rig.outputs["heater_target_c"])
        self.until("complete")

    def test_explicit_automatic_breakthrough_still_completes(self):
        engine = self.make_engine("breakthrough", automatic_cutoff_enabled=True, cycles=1,
                                  initial_treatment="none", final_purge=False, breakthrough_pct=30)
        self.assertEqual(engine.threshold, 0.3)
        self.until("complete")
        self.assertEqual(engine.cycles_completed, 1)
        self.assertIn("breakthrough_time_s", engine.summary["cycles"][0])

    def test_end_during_warmup_is_not_falsely_reported_completed(self):
        engine = self.make_engine("zero")
        engine.stop()
        self.until("aborted")
        self.assertEqual(engine.cycles_completed, 0)

    def test_flow_loss_and_timeout_still_protect_continuous_test(self):
        self.make_engine("zero")
        self.until("zero")
        self.rig.inject_fault("flow_loss")
        self.until("fault", limit=10)
        self.assertEqual(self.rig.outputs["gas"], "off")
        self.make_engine("zero", stage_timeout_s=200)
        self.until("zero")
        self.until("fault", limit=210)
        self.assertIn("timeout", self.engine.reason)

    def test_bad_test_settings_fail_with_explanations(self):
        for config in ({"test_type": []}, {"test_type": "unknown"},
                       {"test_type": "zero", "automatic_cutoff_enabled": True},
                       {"test_type": "breakthrough", "automatic_cutoff_enabled": "yes"}):
            self.assertTrue(validate_config(normalize_test_config(config)))

    def test_legacy_cannot_disable_cutoff_without_selecting_a_test(self):
        self.assertTrue(validate_config({"automatic_cutoff_enabled": False, "breakthrough_pct": None}))

    def test_end_while_measurement_commands_are_pending_is_not_completed(self):
        engine = self.make_engine("zero")
        engine._enter("zero")
        self.rig.outputs["pending_count"] = 1
        engine.stop()
        self.assertFalse(engine.operator_finished)
        self.assertNotIn("ended_by", engine.summary)

    def test_repeated_end_does_not_restart_shutdown_or_change_outcome(self):
        engine = self.make_engine("zero")
        self.until("zero")
        self.advance(10)
        engine.stop()
        self.until("shutdown")
        engine.stop()
        self.assertEqual(engine.stage, "shutdown")
        self.until("complete")


class NamedTestRecordTests(unittest.TestCase):
    def test_start_requires_fresh_measurements_even_when_rig_reports_ready(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = LabService(temporary)
            service.connect()
            service.rig.values["column_temp_c"] = None
            self.assertTrue(service.rig.snapshot()["ready"])
            errors = service.validate_setup({"test_type": "zero"})
            self.assertTrue(any("Column temperature" in message for message in errors))
            with self.assertRaises(ValueError):
                service.start({"test_type": "zero"})
            self.assertFalse(service.list_runs())
            service.disconnect()

    def test_record_is_named_shareable_and_has_no_hidden_threshold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "User data #1"
            service = LabService(root)
            service.connect()
            run_id = service.start({"test_type": "zero", "notebook_id": "CCT-SHARED"})
            service.set_speed(120)
            service.update(1)
            service.stop()
            service.update(1)
            run = service.load_run(run_id)
            self.assertEqual(run["metadata"]["test_type"], "zero")
            self.assertEqual(run["metadata"]["status"], "complete")
            self.assertIsNone(run["metadata"]["recipe"]["breakthrough_pct"])
            self.assertTrue(all(row["threshold_ratio"] is None for row in run["samples"]))
            export = service.export_run(run_id)
            with (export / "measurements_1Hz.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(all(row["test_type"] == "zero" for row in rows))
            self.assertTrue((export / "test_settings.json").is_file())
            service.disconnect()
            other_root = Path(temporary) / "Another computer"
            shutil.copytree(root / "runs", other_root / "runs")
            other = LabService(other_root)
            self.assertEqual(other.list_runs()[0]["test_type"], "zero")
            copied = other.load_run(run_id)
            self.assertEqual(len(copied["samples"]), len(run["samples"]))
            self.assertTrue(Path(copied["metadata"]["path"]).is_relative_to(other_root))
            self.assertFalse(other.connected)
            other.store.close()
