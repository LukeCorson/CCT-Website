"""Cross-layer regressions for shutdown ordering and traceable experiment data."""

import csv
from pathlib import Path
import queue
import tempfile
import time
from types import SimpleNamespace
import unittest

from cct.contracts import DEFAULT_CONFIG
from cct.drivers.hardware import HardwareRig
from cct.drivers.protocols import LicorFramer, parse_licor
from cct.service import LabService


class GasTransactionTests(unittest.TestCase):
    def setUp(self):
        # No serial ports or worker threads. These queues model acknowledgements.
        self.rig = HardwareRig("does-not-exist.json")
        self.rig._profile_errors = []
        self.rig._active = self.rig._handshake = True
        self.rig._outputs["owned"] = True
        self.rig._workers = {
            name: SimpleNamespace(outbound=queue.Queue(32), urgent=queue.Queue(32))
            for name in ("core2", "calibrator", "alicat")
        }
        for name in self.rig._workers:
            self.rig._devices[name]["connected"] = True

    def take(self, device, priority=False):
        worker = self.rig._workers[device]
        return (worker.urgent if priority else worker.outbound).get_nowait()

    def begin_change(self):
        group = self.rig.execute("gas", name="n2")
        close = self.take("core2")
        self.assertEqual((close[2], close[3]["name"]), ("gas", "off"))
        self.assertTrue(self.rig._workers["calibrator"].outbound.empty())
        self.assertTrue(self.rig._workers["alicat"].outbound.empty())
        return group

    def test_source_opens_only_after_both_instruments_acknowledge(self):
        group = self.begin_change()
        self.rig._part_done(group, 0)  # Physical sources closed.
        self.assertEqual(self.take("calibrator")[3]["name"], "n2")
        self.assertEqual(self.take("alicat")[3]["name"], "n2")
        self.rig._part_done(group, 0)  # Calibrator alone is insufficient.
        self.assertTrue(self.rig._workers["core2"].outbound.empty())
        self.rig._part_done(group, 1)
        opened = self.take("core2")
        self.assertEqual(opened[3]["name"], "n2")
        self.assertEqual(opened[3]["_phase"], 3)
        self.assertIn(group, self.rig._pending)
        self.rig._part_done(group, 0)
        self.assertNotIn(group, self.rig._pending)

    def test_late_gas_acknowledgements_cannot_reopen_after_stop(self):
        group = self.begin_change()
        self.rig._part_done(group, 0)
        self.take("calibrator")
        self.take("alicat")
        stop = self.rig.execute("emergency_stop")
        self.assertNotIn(group, self.rig._pending)
        self.rig._part_done(group, 0)
        self.rig._part_done(group, 1)
        self.assertTrue(self.rig._workers["core2"].outbound.empty())
        self.assertEqual(self.take("core2", priority=True)[2], "safe_state")
        self.assertEqual(self.take("calibrator", priority=True)[3]["name"], "off")
        self.assertIn(stop, self.rig._pending)

    def test_instrument_rejection_never_opens_the_source(self):
        group = self.begin_change()
        self.rig._part_done(group, 0)
        self.take("calibrator")
        self.take("alicat")
        self.rig._part_done(group, 0, "Gas selection did not verify")
        self.rig._part_done(group, 1)
        self.assertTrue(self.rig._workers["core2"].outbound.empty())
        self.assertNotIn(group, self.rig._pending)
        self.assertIn("did not verify", self.rig._outputs["command_fault"])


class RecordTraceabilityTests(unittest.TestCase):
    def test_bad_xml_is_recoverable_before_the_next_valid_frame(self):
        frames = LicorFramer().feed(b"<LI840><DATA><CO2>bad</DATA></LI840><LI840><DATA><CO2>420</CO2></DATA></LI840>")
        self.assertEqual(len(frames), 2)
        with self.assertRaises(ValueError):
            parse_licor(frames[0])
        self.assertEqual(parse_licor(frames[1]), {"co2_ppm": 420})

    def test_export_identifies_simulation_and_raw_frames_do_not_hide_phase_events(self):
        with tempfile.TemporaryDirectory() as root:
            service = LabService(Path(root))
            service.connect()
            service.start(dict(DEFAULT_CONFIG))
            for index in range(200):
                service._emit({"event": "raw_frame", "device": "licor", "raw_ascii": str(index)})
            self.assertTrue(any(event["event"] == "stage" for event in service.snapshot()["events"]))
            self.assertFalse(any(event["event"] == "raw_frame" for event in service.snapshot()["events"]))
            service.inject_fault("overtemp")
            service.update(1)
            run_id = service.snapshot()["run_id"]
            run = service.load_run(run_id)
            self.assertEqual(sum(event["event"] == "raw_frame" for event in run["events"]), 200)
            csv_path = service.export_run(run_id) / "measurements_1Hz.csv"
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(rows)
            self.assertTrue(all(row["mode"] == "simulation" for row in rows))
            self.assertTrue(all(row["run_id"] == run_id for row in rows))
            self.assertTrue(all(row["sample_id"] == DEFAULT_CONFIG["sample_id"] for row in rows))
            service.disconnect()

    def test_partial_device_telemetry_is_not_ready_for_an_experiment(self):
        rig = HardwareRig("does-not-exist.json")
        rig._profile_errors = []
        rig._handshake = True
        rig._outputs.update(owned=True, s3_online=True)
        for device in rig._devices:
            rig._set_device(device, True, "Connected")
            rig._device_received[device] = time.monotonic()
        rig._sample("licor", {"co2_ppm": 420})
        snapshot = rig.snapshot()
        self.assertFalse(snapshot["ready"])
        self.assertTrue(any("Analyzer temperature" in blocker for blocker in snapshot["blockers"]))
        self.assertFalse(any("H₂O" in blocker for blocker in snapshot["blockers"]))


if __name__ == "__main__":
    unittest.main()
