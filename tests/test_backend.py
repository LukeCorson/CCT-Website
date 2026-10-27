"""Behavioral tests for cycle automation, data recovery, and failure containment."""

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cct.contracts import DEFAULT_CONFIG
from cct.engine import ExperimentEngine, validate_config
from cct.service import LabService
from cct.simulator import SimulationRig
from cct.storage import RunStore


class EngineTests(unittest.TestCase):
    def make_engine(self, **changes):
        self.rig = SimulationRig()
        self.rig.connect()
        self.events = []
        config = {**DEFAULT_CONFIG, **changes}
        self.engine = ExperimentEngine(self.rig, config, self.events.append)
        self.engine.start()
        return self.engine

    def step_until(self, stage, limit=5000):
        for _ in range(limit):
            if self.engine.stage == stage:
                return
            self.rig.tick(1, self.engine.stage)
            self.engine.step(1, self.rig.snapshot())
        self.fail(f"Did not reach {stage}; actual {self.engine.stage}: {self.engine.waiting_for}")

    def test_full_repeated_cycle_and_initial_procedure(self):
        engine = self.make_engine(cycles=2)
        self.step_until("initial_heat")
        self.assertTrue(any(event["event"] == "initial_nitrogen_baseline" for event in self.events))
        self.assertEqual(self.rig.outputs["gas"], "n2", "Initial desorption must keep nitrogen flowing during heating")
        self.step_until("complete")
        self.assertEqual(engine.cycles_completed, 2)
        self.assertEqual(len(engine.summary["cycles"]), 2)
        self.assertTrue(all(419 < row["inlet_co2_ppm"] < 421 for row in engine.summary["cycles"]))
        self.assertEqual(self.rig.outputs["gas"], "off")
        self.assertIsNone(self.rig.outputs["heater_target_c"])

    def test_initial_purge_heats_without_nitrogen(self):
        self.make_engine(initial_treatment="purge")
        self.step_until("initial_heat")
        self.assertEqual(self.rig.outputs["gas"], "off")
        self.assertEqual(self.rig.outputs["heater_target_c"], 150)
        self.step_until("initial_release")
        self.assertEqual(self.rig.outputs["gas"], "n2")

    def test_final_purge_can_be_omitted_without_turning_nitrogen_back_on(self):
        self.make_engine(cycles=1, initial_treatment="none", final_purge=False)
        self.step_until("final_cooldown")
        self.assertEqual(self.rig.outputs["gas"], "off")
        self.step_until("complete")
        self.assertFalse(any(event.get("details", {}).get("stage") == "purge_heat" for event in self.events))

    def test_threshold_requires_fresh_distinct_measurements(self):
        engine = self.make_engine(breakthrough_hold_s=2)
        engine.baseline = 420
        engine.summary["cycles"].append({"cycle": 1})
        engine._enter("breakthrough")
        snapshot = self.rig.snapshot()
        snapshot["values"]["co2_ppm"] = 100
        snapshot["values"]["flow_sccm"] = 500
        for age in (0, 1, 2, 3, 4):
            snapshot["ages"]["co2_ppm"] = age
            engine.step(1, snapshot)
        self.assertEqual(engine.stage, "breakthrough")
        self.assertEqual(engine.cycles_completed, 0)
        snapshot["ages"]["co2_ppm"] = 6
        engine.step(1, snapshot)
        self.assertEqual(engine.stage, "fault")

    def test_noisy_single_crossing_does_not_end_measurement(self):
        engine = self.make_engine(breakthrough_hold_s=3)
        engine.baseline = 420
        engine.summary["cycles"].append({"cycle": 1})
        engine._enter("breakthrough")
        snapshot = self.rig.snapshot()
        for co2 in (20, 50, 20, 50, 50, 20, 20):
            snapshot["values"]["co2_ppm"] = co2
            snapshot["values"]["flow_sccm"] = 500
            engine.step(1, snapshot)
        self.assertEqual(engine.stage, "breakthrough")
        for _ in range(4):
            snapshot["values"]["co2_ppm"] = 50
            engine.step(1, snapshot)
        self.assertEqual(engine.cycles_completed, 1)

    def test_overtemperature_turns_outputs_off(self):
        engine = self.make_engine()
        self.rig.inject_fault("overtemp")
        self.rig.tick(1, engine.stage)
        engine.step(1, self.rig.snapshot())
        self.assertEqual(engine.stage, "fault")
        self.assertEqual(self.rig.outputs["gas"], "off")
        self.assertIsNone(self.rig.outputs["heater_target_c"])

    def test_command_pending_cannot_acquire_or_satisfy_dwell(self):
        engine = self.make_engine()
        snapshot = self.rig.snapshot()
        snapshot["values"]["analyzer_temp_c"] = 51.5
        snapshot["outputs"]["pending_count"] = 1
        self.rig.outputs["pending_count"] = 1
        for _ in range(20):
            engine.step(1, snapshot)
        self.assertEqual(engine.stage, "warmup")
        self.assertEqual(engine.stage_elapsed, 0)
        snapshot["outputs"]["pending_count"] = 0
        self.rig.outputs["pending_count"] = 0
        for _ in range(10):
            engine.step(1, snapshot)
        self.assertEqual(engine.stage, "initial_baseline")

    def test_heating_waits_for_gas_and_route_acknowledgements(self):
        class PendingRig(SimulationRig):
            def __init__(self):
                super().__init__()
                self.sent = []

            def execute(self, action, **kwargs):
                super().execute(action, **kwargs)
                self.sent.append((action, kwargs))
                self.outputs["pending_count"] = 1

        rig = PendingRig()
        rig.connect()
        engine = ExperimentEngine(rig, dict(DEFAULT_CONFIG), lambda event: None)
        engine.running = True
        engine._enter("initial_heat")
        self.assertEqual(rig.sent[-1], ("gas", {"name": "off"}))
        self.assertIsNone(rig.outputs["heater_target_c"])
        for expected in ("route", "gas", "heater"):
            rig.outputs["pending_count"] = 0
            engine.step(1, rig.snapshot())
            self.assertEqual(rig.sent[-1][0], expected)
            self.assertEqual(engine.stage_elapsed, 0)
        self.assertEqual(rig.sent[-2], ("gas", {"name": "n2"}))
        self.assertEqual(rig.sent[-1], ("heater", {"target_c": 150.0}))

    def test_lost_purge_flow_faults_before_heated_timeout(self):
        engine = self.make_engine()
        self.step_until("initial_heat")
        self.rig.inject_fault("flow_loss")
        self.step_until("fault", limit=10)
        self.assertIsNone(self.rig.outputs["heater_target_c"])

    def test_stage_timeout_is_a_fault(self):
        engine = self.make_engine(stage_timeout_s=130)
        engine._enter("stabilize")
        self.rig.inject_fault("flow_loss")
        self.step_until("fault", limit=140)
        self.assertIn("timeout", engine.reason)


class ValidationTests(unittest.TestCase):
    def test_nonfinite_and_invalid_settings(self):
        for key, value in (("sample_mass_g", float("nan")), ("breakthrough_pct", float("inf")),
                           ("cycles", 1.5), ("cycles", True), ("notebook_id", "../other"),
                           ("sample_id", ""), ("final_purge", "yes"), ("desorption_target_c", 250)):
            with self.subTest(key=key, value=value):
                self.assertTrue(validate_config({key: value}))
        self.assertFalse(validate_config(DEFAULT_CONFIG))

    def test_reference_cannot_be_shorter_than_stability_window(self):
        self.assertTrue(validate_config({"baseline_s": 10, "stable_window_s": 30}))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = LabService(Path(self.temporary.name))
        self.service.connect()

    def tearDown(self):
        self.service.disconnect()
        self.temporary.cleanup()  # Also proves SQLite connections release Windows file handles.

    def test_record_complete_cycle_and_portable_exports(self):
        service = self.service
        run_id = service.start({"cycles": 1, "initial_treatment": "none", "final_purge": False})
        service.set_speed(120)
        for _ in range(10):
            service.update(1)
            if not service.snapshot()["running"]:
                break
        self.assertEqual(service.snapshot()["outcome"], "complete")
        run = service.load_run(run_id)
        self.assertEqual(run["metadata"]["status"], "complete")
        self.assertEqual(run["metadata"]["cycles_completed"], 1)
        self.assertGreater(len(run["samples"]), 200)
        self.assertTrue(any(row["stage"] == "baseline" for row in run["samples"]))
        self.assertEqual(run["metadata"]["mode"], "simulation")
        export = service.export_run(run_id)
        with (export / "measurements_1Hz.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), len(run["samples"]))
        self.assertIn("ages_json", rows[0])
        self.assertIn("quality_json", rows[0])
        self.assertTrue((export / "events.csv").is_file())

    def test_controlled_stop_is_aborted_and_saved(self):
        service = self.service
        run_id = service.start({"initial_treatment": "none"})
        service.update(1)
        service.stop()
        self.assertTrue(service.snapshot()["running"])
        service.set_speed(120)
        service.update(1)
        self.assertEqual(service.snapshot()["outcome"], "aborted")
        self.assertEqual(service.load_run(run_id)["metadata"]["status"], "aborted")

    def test_stale_reading_becomes_gap_and_fault(self):
        service = self.service
        run_id = service.start({})
        service.inject_fault("stale")
        for _ in range(7):
            service.update(1)
        run = service.load_run(run_id)
        self.assertEqual(run["metadata"]["status"], "fault")
        self.assertIsNone(run["samples"][-1]["co2_ppm"])
        self.assertEqual(run["samples"][-1]["quality"]["co2_ppm"], "missing_or_stale")

    def test_automatic_run_locks_manual_controls(self):
        self.service.start({})
        with self.assertRaises(RuntimeError):
            self.service.manual("gas", name="air")
        with self.assertRaises(ValueError):
            self.service.start({})
        with self.assertRaises(RuntimeError):
            self.service.connect()

    def test_disk_write_failure_stops_outputs_and_does_not_claim_saved(self):
        service = self.service
        run_id = service.start({})
        with patch.object(service.store, "append_sample", side_effect=OSError("Disk full")):
            service.update(1)
        self.assertEqual(service.snapshot()["stage"], "fault")
        self.assertIn("Disk full", service.snapshot()["save_status"])
        self.assertIsNone(service.rig.outputs["heater_target_c"])
        self.assertEqual(service.rig.outputs["gas"], "off")
        self.assertEqual(service.load_run(run_id)["metadata"]["status"], "fault")

    def test_create_failure_never_starts_outputs(self):
        with patch.object(self.service.store, "create", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(RuntimeError):
                self.service.start({})
        self.assertFalse(self.service.snapshot()["running"])
        self.assertEqual(self.service.rig.outputs["gas"], "off")

    def test_recipe_and_run_paths_do_not_escape_storage(self):
        self.service.save_recipe("My recipe", {"cycles": 2})
        recipes = self.service.list_recipes()
        self.assertEqual(recipes[0]["config"]["cycles"], 2)
        with self.assertRaises(ValueError):
            self.service.load_run("../elsewhere")

    def test_interrupted_record_is_never_resumed(self):
        root = Path(self.temporary.name) / "recovery"
        store = RunStore(root)
        metadata = store.create(dict(DEFAULT_CONFIG), "simulation")
        store.append_sample({"elapsed_s": 0, "stage": "warmup", "cycle": 1})
        store.close()
        recovered = LabService(root)
        self.assertEqual(recovered.list_runs()[0]["status"], "interrupted")
        self.assertFalse(recovered.snapshot()["running"])
        self.assertFalse(recovered.snapshot()["connected"])
        self.assertEqual(recovered.load_run(metadata["id"])["metadata"]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
