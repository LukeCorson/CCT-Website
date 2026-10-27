"""Core procedure parity and live-gas operation with a manually operated 146C."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cct.core_tests import CoreProcedureEngine
from cct.drivers.discovery import resolve_profile
from cct.drivers.hardware import HardwareRig, _Worker
from cct.drivers.profile import profile_blockers
from cct.service import LabService
from test_drivers import commissioned_profile


class CoreProcedureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = LabService(self.temporary.name)
        self.service.connect()

    def tearDown(self):
        self.service.disconnect()
        self.temporary.cleanup()

    def start(self, kind):
        self.service.start({"test_type": kind, "core_controls": True})
        self.service.update(1)
        self.assertIsInstance(self.service.engine, CoreProcedureEngine)
        return self.service.rig.outputs

    def test_zero_starts_nitrogen_bypass_directly(self):
        outputs = self.start("zero")
        self.assertEqual(self.service.engine.stage, "core_zero")
        self.assertEqual(outputs["route"], "zero")
        self.assertEqual(outputs["gas"], "n2")
        self.assertIsNone(outputs["heater_target_c"])

    def test_adsorption_starts_air_off_and_has_core_air_toggle(self):
        outputs = self.start("adsorption")
        self.assertEqual(outputs["route"], "adsorption")
        self.assertEqual(outputs["gas"], "off")
        self.service.update(5)
        self.assertTrue(self.service.engine.running)
        self.service.core_action("air", True)
        self.service.update(1)
        self.assertEqual(outputs["gas"], "air")
        self.assertGreater(self.service.snapshot()["values"]["flow_sccm"], 1)
        self.service.core_action("air", False)
        self.service.update(5)
        self.assertEqual(outputs["gas"], "off")
        self.assertTrue(self.service.engine.running)

    def test_breakthrough_starts_bypass_and_never_switches_itself(self):
        outputs = self.start("breakthrough")
        self.service.set_speed(120)
        self.service.update(3)
        self.assertEqual(outputs["route"], "bypass")
        self.assertEqual(self.service.engine.stage, "core_breakthrough_bypass")
        self.service.core_action("column", True)
        self.service.update(1)
        self.assertEqual(outputs["route"], "column")
        self.assertIsNone(self.service.engine.threshold)
        self.service.core_action("column", False)
        self.assertEqual(outputs["route"], "bypass")

    def test_desorption_starts_heat_off_until_operator_requests_heat(self):
        outputs = self.start("desorption")
        self.assertEqual(outputs["route"], "purge")
        self.assertEqual(outputs["gas"], "n2")
        self.assertIsNone(outputs["heater_target_c"])
        self.service.core_action("heater", 140)
        self.service.update(1)
        self.assertEqual(outputs["heater_target_c"], 140)
        self.service.core_action("heater", None)
        self.assertIsNone(outputs["heater_target_c"])

    def test_live_flow_and_temperature_protections_are_not_disabled(self):
        self.start("desorption")
        self.service.rig.values["flow_sccm"] = 0
        with self.assertRaises(ValueError):
            self.service.core_action("heater", 150)
        self.service.inject_fault("flow_loss")
        self.service.update(5)
        self.assertEqual(self.service.engine.stage, "fault")
        self.assertIsNone(self.service.rig.outputs["heater_target_c"])

    def test_controls_cannot_be_used_during_automatic_cycles(self):
        self.service.start({"test_type": "breakthrough", "automatic_cutoff_enabled": True, "cycles": 1,
                            "initial_treatment": "none", "final_purge": False})
        with self.assertRaises(RuntimeError):
            self.service.core_action("column", True)
        self.service.set_speed(120)
        for _ in range(12):
            self.service.update(1)
            if not self.service.engine.running:
                break
        self.assertEqual(self.service.engine.outcome, "complete")


class ManualCalibratorTests(unittest.TestCase):
    def profile(self):
        value = commissioned_profile()
        value["calibrator_mode"] = "manual"
        value["devices"]["calibrator"] = {}
        return value

    def test_manual_profile_needs_no_146c_port_or_command_mapping(self):
        value = self.profile()
        self.assertFalse(profile_blockers(value))
        value["devices"]["alicat"]["gas_selection_verified"] = False
        self.assertTrue(profile_blockers(value), "Real-gas meter correction must still be verified")

    def test_discovery_and_connection_never_open_146c(self):
        value = self.profile()
        adapters = [{"port": settings["port"], "usb_serial": settings["expected_usb_serial"]}
                    for name, settings in value["devices"].items() if name != "calibrator"]
        report = resolve_profile(value, adapters)
        self.assertFalse(report["blockers"])
        self.assertEqual(report["devices"]["calibrator"]["state"], "disabled")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "apparatus.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            rig = HardwareRig(path)
            with patch("cct.drivers.discovery.enumerate_adapters", return_value=adapters), patch("cct.drivers.hardware._Worker") as worker:
                rig.connect()
                self.assertEqual({call.args[1] for call in worker.call_args_list}, {"core2", "licor", "alicat"})
                self.assertFalse(rig.snapshot()["devices"]["calibrator"]["required"])
                rig.disconnect()

    def test_manual_mode_keeps_meter_and_heater_but_excludes_all_146c_commands(self):
        rig = HardwareRig("missing.json")
        rig._profile = self.profile()
        for action, arguments in (("gas", {"name": "air"}), ("gas", {"name": "n2"}),
                                  ("gas", {"name": "off"}), ("safe_state", {}), ("emergency_stop", {}),
                                  ("heater", {"target_c": 150})):
            commands = rig._commands(action, arguments)
            self.assertFalse(any(device == "calibrator" for device, _, _ in commands))
        self.assertEqual([device for device, _, _ in rig._commands("gas", {"name": "air"})], ["core2", "alicat"])
        self.assertEqual(rig._commands("heater", {"target_c": 150})[0][2]["target_c"], 150)

    def test_manual_gas_change_still_waits_for_meter_ack_before_source_open(self):
        rig = HardwareRig("missing.json")
        rig._profile = self.profile()
        rig._profile_errors = []
        rig._active = rig._handshake = True
        rig._outputs["owned"] = True
        for name in ("core2", "alicat", "licor"):
            rig._workers[name] = _Worker(rig, name, rig._profile["devices"][name])
            rig._devices[name]["connected"] = True
        group = rig.execute("gas", name="air")
        closed = rig._workers["core2"].outbound.get_nowait()
        self.assertEqual(closed[3]["name"], "off")
        self.assertTrue(rig._workers["alicat"].outbound.empty())
        rig._part_done(group, closed[1])
        meter = rig._workers["alicat"].outbound.get_nowait()
        self.assertTrue(rig._workers["core2"].outbound.empty())
        rig._part_done(group, meter[1])
        opened = rig._workers["core2"].outbound.get_nowait()
        self.assertEqual(opened[3]["name"], "air")

    def test_missing_manual_confirmation_cannot_start_physical_operation(self):
        from cct.simulator import SimulationRig
        class ManualFixture(SimulationRig):
            def snapshot(self):
                result = super().snapshot()
                result["calibrator_mode"] = "manual"
                result["profile"] = {"routes": {"zero": [{"mask": 7, "wait_ms": 0}]}}
                result["devices"]["calibrator"].update(connected=False, fresh=False, required=False)
                return result
        with tempfile.TemporaryDirectory() as directory:
            service = LabService(directory)
            service.rig = ManualFixture()
            service.rig.connect()
            service.connected, service.mode = True, "hardware"
            with self.assertRaisesRegex(ValueError, "146C.*Local/manual"):
                service.start({"test_type": "zero", "core_controls": True})
            self.assertFalse(service.list_runs())
            service.start({"test_type": "zero", "core_controls": True, "manual_calibrator_confirmed": True})
            service.update(1)
            self.assertTrue(service.engine.running)
            self.assertEqual(service.config["calibrator_mode"], "manual")
            service.disconnect()


class SimplifiedOperatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_core_choices_hide_cycle_parameters_and_repeat_is_explicit(self):
        from cct.ui.setup import ExperimentPage
        page = ExperimentPage()
        try:
            self.assertEqual(page.test_selector.count(), 5)
            for kind in ("zero", "adsorption", "breakthrough", "desorption"):
                page.test_selector.setCurrentIndex(page.test_selector.findData(kind))
                self.assertTrue(page.settings_card.isHidden())
                self.assertTrue(page.config()["core_controls"])
                self.assertIsNone(page.config()["breakthrough_pct"])
            page.test_selector.setCurrentIndex(page.test_selector.findData("breakthrough_cycles"))
            self.assertFalse(page.settings_card.isHidden())
            self.assertFalse(page.config()["core_controls"])
            self.assertTrue(page.config()["automatic_cutoff_enabled"])
        finally:
            page.deleteLater()
            self.app.processEvents()

    def test_manual_146c_start_requires_both_operator_confirmations(self):
        from cct.ui.preflight import PreflightDialog
        dialog = PreflightDialog({"ready": True, "mode": "hardware", "calibrator_mode": "manual"}, {}, allow_start=True)
        try:
            dialog.confirm.setChecked(True)
            self.assertFalse(dialog.start_btn.isEnabled())
            dialog.manual_confirm.setChecked(True)
            self.assertTrue(dialog.start_btn.isEnabled())
        finally:
            dialog.deleteLater()
            self.app.processEvents()

    def test_manual_profile_pairing_requires_three_not_four_adapters(self):
        from cct.ui.connections import DevicesPage
        from PySide6.QtTest import QTest
        import time
        profile = commissioned_profile()
        profile["calibrator_mode"] = "manual"
        adapters = [{"port": device["port"], "usb_serial": device["expected_usb_serial"]}
                    for role, device in profile["devices"].items() if role != "calibrator"]
        page = DevicesPage(discovery_callback=lambda: adapters)
        try:
            page.mode.setCurrentIndex(page.mode.findData("hardware"))
            page.set_profile(profile, path="synthetic-profile.json")
            page.scan_btn.click()
            deadline = time.monotonic() + 5
            while page._workers and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(5)
            self.assertFalse(page._workers)
            self.assertTrue(page._all_paired())
            self.assertTrue(page.connect_btn.isEnabled())
            self.assertTrue(page.device_cards["calibrator"].isHidden())
        finally:
            page.wait_for_done(5000)
            page.deleteLater()
            self.app.processEvents()
