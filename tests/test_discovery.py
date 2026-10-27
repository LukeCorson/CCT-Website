"""Passive discovery and open-time identity checks; no real hardware calls."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cct.contracts import DEVICE_IDS
from cct.drivers.discovery import enumerate_adapters, resolve_profile
from cct.drivers.hardware import HardwareRig, _Worker


def profile():
    return {"schema_version": 1, "commissioned": False,
            "devices": {name: {"port": f"COM{index + 1}", "baud": 9600,
                               "expected_usb_serial": "SERIAL-" + name}
                        for index, name in enumerate(DEVICE_IDS)}}


def inventory():
    return [{"port": f"COM{index + 10}", "usb_serial": "SERIAL-" + name,
             "vid": 0x10C4, "pid": 0xEA60, "description": "USB serial adapter",
             "manufacturer": "Test", "location": str(index)}
            for index, name in enumerate(DEVICE_IDS)]


class PassiveInventoryTests(unittest.TestCase):
    def test_inventory_reads_os_metadata_without_opening_ports(self):
        native = SimpleNamespace(device="COM16", serial_number="TEST-16", vid=4292,
                                 pid=60000, description="USB serial", manufacturer="Test", location="1-2")
        with patch("serial.tools.list_ports.comports", return_value=[native]), patch("serial.Serial") as opened:
            result = enumerate_adapters()
        opened.assert_not_called()
        self.assertEqual(result, [{"port": "COM16", "usb_serial": "TEST-16", "vid": 4292,
                                   "pid": 60000, "description": "USB serial", "manufacturer": "Test", "location": "1-2"}])

    def test_enumeration_failure_has_explanatory_error(self):
        with patch("serial.tools.list_ports.comports", side_effect=OSError("inventory denied")):
            with self.assertRaisesRegex(RuntimeError, "inventory denied"):
                enumerate_adapters()
        with patch("cct.drivers.discovery.enumerate_adapters", side_effect=RuntimeError("enumeration unavailable")):
            report = resolve_profile(profile())
        self.assertTrue(report["blockers"])
        self.assertTrue(all(state["port"] is None for state in report["devices"].values()))


class PairingResolutionTests(unittest.TestCase):
    def test_changed_ports_are_remapped_without_mutating_saved_profile(self):
        saved, adapters = profile(), inventory()
        original = deepcopy(saved)
        original_inventory = deepcopy(adapters)
        result = resolve_profile(saved, adapters)
        self.assertEqual(result["blockers"], [])
        self.assertEqual(saved, original)
        self.assertEqual(adapters, original_inventory)
        for index, name in enumerate(DEVICE_IDS):
            self.assertEqual(result["devices"][name]["state"], "matched")
            self.assertEqual(result["profile"]["devices"][name]["port"], f"COM{index + 10}")

    def test_missing_serial_never_falls_back_to_saved_port(self):
        saved, adapters = profile(), inventory()
        adapters[0].update(port="COM1", usb_serial="SOME-OTHER-ADAPTER")
        result = resolve_profile(saved, adapters)
        self.assertEqual(result["devices"]["core2"]["state"], "missing")
        self.assertEqual(result["profile"]["devices"]["core2"]["port"], "")

    def test_duplicate_usb_serial_is_ambiguous_even_with_vid_filter(self):
        saved, adapters = profile(), inventory()
        saved["devices"]["core2"]["expected_usb_vid"] = 0x10C4
        extra = dict(adapters[0], port="COM55", vid=0x0403)
        result = resolve_profile(saved, adapters + [extra])
        self.assertEqual(result["devices"]["core2"]["state"], "ambiguous")
        self.assertEqual(result["profile"]["devices"]["core2"]["port"], "")

    def test_one_adapter_cannot_be_paired_to_multiple_roles(self):
        saved = profile()
        saved["devices"]["licor"]["expected_usb_serial"] = saved["devices"]["core2"]["expected_usb_serial"]
        result = resolve_profile(saved, inventory())
        self.assertEqual(result["devices"]["core2"]["state"], "ambiguous")
        self.assertEqual(result["devices"]["licor"]["state"], "ambiguous")

    def test_duplicate_paths_never_open_different_roles(self):
        adapters = inventory()
        adapters[1]["port"] = adapters[0]["port"]
        result = resolve_profile(profile(), adapters)
        self.assertEqual(result["devices"]["core2"]["state"], "ambiguous")
        self.assertEqual(result["devices"]["licor"]["state"], "ambiguous")

    def test_optional_vid_pid_are_enforced_and_hex_is_accepted(self):
        saved = profile()
        core = saved["devices"]["core2"]
        core.update(expected_usb_vid="0x10c4", expected_usb_pid="0xEA60")
        result = resolve_profile(saved, inventory())
        self.assertEqual(result["devices"]["core2"]["state"], "matched")
        self.assertEqual(result["profile"]["devices"]["core2"]["expected_usb_vid"], 0x10C4)
        core["expected_usb_pid"] = 1
        self.assertEqual(resolve_profile(saved, inventory())["devices"]["core2"]["state"], "missing")
        core["expected_usb_pid"] = True
        self.assertEqual(resolve_profile(saved, inventory())["devices"]["core2"]["state"], "unpaired")

    def test_unpaired_manual_profiles_are_never_assigned_by_description(self):
        saved = profile()
        saved["devices"]["core2"].pop("expected_usb_serial")
        result = resolve_profile(saved, inventory())
        self.assertEqual(result["devices"]["core2"]["state"], "unpaired")
        self.assertIsNone(result["devices"]["core2"]["port"])

    def test_malformed_profiles_and_inventory_fail_closed(self):
        for saved in (None, [], {"devices": []}, {"devices": {"core2": []}}, {"devices": {"unknown": {}}}):
            report = resolve_profile(saved, inventory())
            self.assertTrue(report["blockers"])
            self.assertTrue(all(state["port"] is None for state in report["devices"].values()))
        for adapters in ({}, [None], [{"port": None}], [{"port": "COM1\n", "usb_serial": "x"}], [{"port": "COM1", "vid": []}]):
            report = resolve_profile(profile(), adapters)
            self.assertTrue(report["blockers"])
            self.assertTrue(all(state["port"] is None for state in report["devices"].values()))


class HardwareResolutionIntegrationTests(unittest.TestCase):
    def rig_from(self, saved, directory):
        path = Path(directory) / "hardware.json"
        path.write_text(json.dumps(saved), encoding="utf-8")
        return HardwareRig(path)

    def test_connect_uses_resolved_ports_and_preserves_loaded_original(self):
        saved = profile()
        with tempfile.TemporaryDirectory() as directory:
            rig = self.rig_from(saved, directory)
            with patch("cct.drivers.discovery.enumerate_adapters", return_value=inventory()), patch("cct.drivers.hardware._Worker") as worker:
                rig.connect()
                self.assertEqual(worker.call_count, 4)
                opened_ports = {call.args[1]: call.args[2]["port"] for call in worker.call_args_list}
                self.assertEqual(opened_ports["core2"], "COM10")
                self.assertEqual(rig.snapshot()["configured_profile"], saved)
                self.assertEqual(rig.snapshot()["profile"]["devices"]["core2"]["port"], "COM10")
                rig.disconnect()

    def test_unpaired_or_missing_roles_never_create_workers(self):
        saved = profile()
        saved["devices"]["core2"].pop("expected_usb_serial")
        adapters = [item for item in inventory() if item["usb_serial"] != "SERIAL-licor"]
        with tempfile.TemporaryDirectory() as directory:
            rig = self.rig_from(saved, directory)
            with patch("cct.drivers.discovery.enumerate_adapters", return_value=adapters), patch("cct.drivers.hardware._Worker") as worker:
                rig.connect()
                self.assertEqual({call.args[1] for call in worker.call_args_list}, {"alicat", "calibrator"})
                self.assertIn("Pair this device", rig.snapshot()["devices"]["core2"]["message"])
                self.assertIn("missing", rig.snapshot()["devices"]["licor"]["message"])
                rig.disconnect()

    def test_worker_rechecks_identity_and_never_opens_moved_port(self):
        rig = HardwareRig("missing.json")
        rig._profile = resolve_profile(profile(), inventory())["profile"]
        adapters = inventory()
        adapters[0]["port"] = "COM99"
        worker = _Worker(rig, "core2", rig._profile["devices"]["core2"])
        with patch("cct.drivers.hardware.enumerate_adapters", return_value=adapters), patch("serial.Serial") as opened:
            worker.run()
        opened.assert_not_called()
        self.assertIn("identity changed", rig.snapshot()["devices"]["core2"]["message"])

    def test_worker_rechecks_vid_pid_before_open(self):
        rig = HardwareRig("missing.json")
        saved = profile()
        saved["devices"]["core2"]["expected_usb_vid"] = 0x10C4
        rig._profile = resolve_profile(saved, inventory())["profile"]
        adapters = inventory()
        adapters[0]["vid"] = 0x0403
        worker = _Worker(rig, "core2", rig._profile["devices"]["core2"])
        with patch("cct.drivers.hardware.enumerate_adapters", return_value=adapters), patch("serial.Serial") as opened:
            worker.run()
        opened.assert_not_called()

    def test_duplicate_json_roles_are_rejected_before_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            path.write_text('{"devices":{"core2":{},"core2":{}}}', encoding="utf-8")
            rig = HardwareRig(path)
            self.assertTrue(any("Duplicate" in error for error in rig.snapshot()["blockers"]))

    def test_windows_bom_profile_loads_without_losing_saved_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            path.write_text(json.dumps(profile()), encoding="utf-8-sig")
            rig = HardwareRig(path)
            self.assertFalse(rig._load_errors)
            self.assertEqual(rig.snapshot()["configured_profile"], profile())


if __name__ == "__main__":
    unittest.main()
