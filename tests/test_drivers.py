import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cct.drivers.hardware import HardwareRig, _Worker
from cct.drivers.profile import profile_blockers
from cct.drivers.protocols import (LineFramer, LicorFramer, calibrator_frame,
                                   core_message, finite, parse_alicat, parse_licor)


def commissioned_profile():
    """Synthetic configuration only, not a plumbing recommendation."""
    profile = json.loads((Path(__file__).resolve().parents[1] / "config" / "hardware.example.json").read_text())
    profile["commissioned"] = True
    profile.update(alicat_mode="automatic", licor_mode="automatic", calibrator_mode="automatic")
    for key in profile["commissioning"]:
        profile["commissioning"][key] = "SYNTHETIC TEST ONLY"
    for i, device in enumerate(profile["devices"].values()):
        device["port"] = f"COM{i + 1}"
        device["expected_usb_serial"] = f"TEST-{i}"
    profile["safe_vent_mask"] = 0
    profile["routes"] = {name: [{"mask": 0, "wait_ms": 0}] for name in profile["routes"]}
    cal = profile["devices"]["calibrator"]
    cal["status_identity_contains"] = "TEST146"
    for name, command in (("air", "sel gas a span 0"), ("n2", "sel gas a span 1"), ("off", "sel gas off")):
        cal["gas_actions"][name] = {"command": command, "status_contains": "TEST " + name}
    profile["devices"]["alicat"]["gas_selection_verified"] = True
    return profile


class DriverProtocolTests(unittest.TestCase):
    def test_licor_partial_and_coalesced_frames(self):
        framer = LicorFramer()
        self.assertEqual(framer.feed(b"noise<LI8"), [])
        self.assertEqual(framer.feed(b"40><DATA><CO2>4"), [])
        frames = framer.feed(b"20</CO2><H2O>1.5</H2O><CELLTEMP>51.5</CELLTEMP></DATA></LI840><LI840><ACK>TRUE</ACK></LI840>")
        self.assertEqual(len(frames), 2)
        self.assertEqual(parse_licor(frames[0]), {"co2_ppm": 420, "analyzer_temp_c": 51.5})
        self.assertEqual(parse_licor(frames[1]), {})

    def test_licor_configuration_echo_not_measurement(self):
        self.assertEqual(parse_licor(b"<LI840><RS232><CO2>TRUE</CO2></RS232></LI840>"), {})
        for text in (b"<LI840><DATA><CO2>NaN</CO2></DATA></LI840>",
                     b"<LI840><DATA><CO2>1</CO2><CO2>2</CO2></DATA></LI840>",
                     b"<LI840><!DOCTYPE x><DATA/></LI840>"):
            with self.assertRaises(ValueError):
                parse_licor(text)

    def test_bounded_parser_recovers(self):
        lines = LineFramer(limit=8)
        self.assertEqual(lines.feed(b"x" * 500 + b"\rOK\r\n"), [b"OK"])
        licor = LicorFramer(limit=32)
        licor.feed(b"<LI840>" + b"x" * 1000)
        self.assertLessEqual(len(licor.buffer), 32)
        self.assertEqual(licor.feed(b"<LI840></LI840>"), [b"<LI840></LI840>"])

    def test_basis_meter_labelled_frame_and_conversion(self):
        self.assertEqual(parse_alicat(b"A 32.1C 0454.2SCCM Air")[0], {"flow_sccm": 454.2})
        self.assertEqual(parse_alicat(b"A 32.1C 0.4542SLPM 0.0SP N2")[0], {"flow_sccm": 454.2})
        for text in (b"A 14.7 25 400 400 Air", b"B 32.1C 1SCCM Air", b"A NaNC 1SCCM Air", b"A 25C infSCCM Air"):
            with self.assertRaises(ValueError):
                parse_alicat(text)

    def test_command_injection_and_nan_rejected(self):
        self.assertEqual(calibrator_frame("status"), b"\xb3status\r")
        with self.assertRaises(ValueError):
            calibrator_frame("sel gas off\rsel gas a span 1")
        with self.assertRaises(ValueError):
            core_message(b'{"protocol":"cct-usb","version":1,"type":"status","x":NaN}')
        for value in (True, "inf", "nan"):
            with self.assertRaises(ValueError):
                finite(value)


class DriverCommissioningTests(unittest.TestCase):
    def make_connected_fake_rig(self):
        rig = HardwareRig("missing.json")
        rig._profile = commissioned_profile()
        rig._profile_errors = []
        rig._active = rig._handshake = True
        rig._outputs["owned"] = True
        for name, settings in rig._profile["devices"].items():
            rig._workers[name] = _Worker(rig, name, settings)
            rig._devices[name]["connected"] = True
        return rig

    def test_gas_change_closes_sources_configures_then_opens(self):
        rig = self.make_connected_fake_rig()
        command_id = rig.execute("gas", name="n2")
        first = rig._workers["core2"].outbound.get_nowait()
        self.assertEqual(first[2:][0], "gas")
        self.assertEqual(first[3]["name"], "off")
        self.assertTrue(rig._workers["calibrator"].outbound.empty())
        rig._part_done(command_id, first[1])
        cal = rig._workers["calibrator"].outbound.get_nowait()
        meter = rig._workers["alicat"].outbound.get_nowait()
        self.assertTrue(rig._workers["core2"].outbound.empty())
        self.assertEqual(rig.snapshot()["outputs"]["pending_count"], 1)
        rig._part_done(command_id, cal[1])
        self.assertTrue(rig._workers["core2"].outbound.empty())
        rig._part_done(command_id, meter[1])
        final = rig._workers["core2"].outbound.get_nowait()
        self.assertEqual(final[3]["name"], "n2")
        self.assertNotEqual(first[3]["_phase"], final[3]["_phase"])
        rig._part_done(command_id, final[1])
        self.assertEqual(rig.snapshot()["outputs"]["pending_count"], 0)

    def test_stop_cancels_deferred_source_on_and_uses_priority_lane(self):
        rig = self.make_connected_fake_rig()
        old_id = rig.execute("gas", name="air")
        first = rig._workers["core2"].outbound.get_nowait()
        rig._part_done(old_id, first[1])
        old_cal = rig._workers["calibrator"].outbound.get_nowait()
        old_meter = rig._workers["alicat"].outbound.get_nowait()
        stop_id = rig.execute("safe_state")
        rig._part_done(old_id, old_cal[1])
        rig._part_done(old_id, old_meter[1])
        self.assertNotIn(old_id, rig._pending)
        self.assertIn(stop_id, rig._pending)
        self.assertTrue(rig._workers["core2"].outbound.empty())
        urgent = rig._workers["core2"].urgent.get_nowait()
        self.assertEqual(urgent[2], "safe_state")
        self.assertTrue(any(event["kind"] == "command_cancelled" for event in rig.drain_events()))

    def test_optional_routes_are_backward_compatible_and_fail_closed(self):
        profile = commissioned_profile()
        profile["routes"].pop("zero", None)
        profile["routes"].pop("adsorption", None)
        self.assertEqual(profile_blockers(profile), [])
        rig = self.make_connected_fake_rig()
        rig._profile = profile
        rig.execute("route", name="zero")
        self.assertEqual(rig.drain_events()[-1]["kind"], "command_error")
        profile["routes"]["zero"] = [{"mask": 7, "wait_ms": 1000}]
        self.assertEqual(profile_blockers(profile), [])
        rig.execute("route", name="zero")
        self.assertEqual(rig._workers["core2"].outbound.get_nowait()[3]["name"], "zero")
        profile["routes"]["adsorption"] = [{"mask": 48, "wait_ms": 1000}]
        self.assertTrue(profile_blockers(profile))

    def test_individual_valve_command_is_named_not_a_raw_mask(self):
        rig = self.make_connected_fake_rig()
        rig.execute("valve", name="exhaust", on=True)
        wire = rig._workers["core2"].outbound.get_nowait()
        self.assertEqual(wire[2], "valve")
        self.assertEqual({"name": wire[3]["name"], "on": wire[3]["on"]},
                         {"name": "exhaust", "on": True})
        self.assertNotIn("mask", wire[3])
        rig.execute("valve", name="made_up", on=True)
        self.assertEqual(rig.drain_events()[-1]["kind"], "command_error")

    def test_serial_port_comparison_uses_host_platform_case_rules(self):
        profile = commissioned_profile()
        profile["devices"]["core2"]["port"] = "/dev/ttyUSB0"
        profile["devices"]["licor"]["port"] = "/dev/ttyusb0"
        with patch("cct.drivers.profile.os.path.normcase", side_effect=lambda value: value):
            self.assertEqual(profile_blockers(profile), [])
        with patch("cct.drivers.profile.os.path.normcase", side_effect=lambda value: value.lower()):
            self.assertTrue(any("distinct" in blocker for blocker in profile_blockers(profile)))

    def test_example_never_arms(self):
        path = Path(__file__).resolve().parents[1] / "config" / "hardware.example.json"
        rig = HardwareRig(path)
        self.assertFalse(rig.snapshot()["ready"])
        rig.execute("heater", target_c=150)
        self.assertEqual(rig.drain_events()[-1]["kind"], "command_error")
        self.assertEqual(rig._workers, {})

    def test_profile_requires_meaningful_fields_not_only_flag(self):
        valid = commissioned_profile()
        self.assertEqual(profile_blockers(valid), [])
        for mutate in (
            lambda p: p["devices"]["calibrator"]["gas_actions"]["n2"].update(command=None),
            lambda p: p.update(safe_vent_mask=32),
            lambda p: p["routes"]["vent"][0].update(mask=48),
            lambda p: p["devices"]["licor"].update(port="COM1"),
            lambda p: p["commissioning"].update(plumbing_evidence=""),
        ):
            changed = copy.deepcopy(valid)
            mutate(changed)
            self.assertTrue(profile_blockers(changed))

    def test_malformed_profile_shapes_fail_closed(self):
        malformed = ([], None, {"devices": None}, {"devices": {"core2": []}},
                     {"commissioning": []}, {"routes": None},
                     {"devices": {"calibrator": {"gas_actions": {"air": []}}}})
        for value in malformed:
            self.assertTrue(profile_blockers(value))
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "profile.json"
                path.write_text(json.dumps(value))
                rig = HardwareRig(path)
                with patch("cct.drivers.discovery.enumerate_adapters", return_value=[]):
                    rig.connect()
                rig.execute("gas", name="n2")
                self.assertFalse(rig.snapshot()["ready"])
                rig.disconnect()

    def test_native_frame_timestamps_preserved_by_sample_age(self):
        rig = HardwareRig("missing.json")
        rig._sample("core2", {"ambient_temp_c": 25}, {"ambient_temp_c": 1.5})
        self.assertGreaterEqual(rig.snapshot()["ages"]["ambient_temp_c"], 1.5)
        rig._sample("core2", {"ambient_temp_c": None})
        self.assertIsNone(rig.snapshot()["values"]["ambient_temp_c"])

    def test_completed_ack_only_completes_matching_command(self):
        rig = HardwareRig("missing.json")
        worker = _Worker(rig, "core2", {})
        rig._pending["abc"] = {"action": "route", "remaining": {0}, "errors": []}
        worker.active = ("abc", 0, "route", {"name": "column"})
        worker._receive(b'{"protocol":"cct-usb","version":1,"type":"ack","id":"abc-0-0","complete":false}', 0)
        self.assertIsNotNone(worker.active)
        worker._receive(b'{"protocol":"cct-usb","version":1,"type":"ack","id":"other","complete":true}', 0)
        self.assertIsNotNone(worker.active)
        worker._receive(b'{"protocol":"cct-usb","version":1,"type":"ack","id":"abc-0-0","complete":true}', 0)
        self.assertIsNone(worker.active)
        self.assertEqual(rig.drain_events()[-1]["kind"], "command_ack")


if __name__ == "__main__":
    unittest.main()
