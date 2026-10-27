"""Commissioning checks keep unknown plumbing out of executable defaults."""

import re
import os

from .protocols import ALLOWED_146C, finite
from .discovery import usb_id
from ..contracts import manual_calibrator, manual_device, required_devices

DEVICE_IDS = ("core2", "licor", "alicat", "calibrator")
ROUTE_NAMES = ("bypass", "column", "vent", "purge", "closed")
OPTIONAL_ROUTE_NAMES = ("zero", "adsorption")


def _profile_blockers(profile):
    problems = []
    if not isinstance(profile, dict):
        return ["Hardware profile must be an object"]
    if profile.get("schema_version") != 1:
        problems.append("Hardware profile schema_version must be 1")
    for role in ("calibrator", "alicat", "licor"):
        if profile.get(role + "_mode", "automatic") not in ("manual", "automatic"):
            problems.append(role + "_mode must be manual or automatic")
    manual = manual_calibrator(profile)
    if profile.get("commissioned") is not True:
        problems.append("Hardware profile is not commissioned; connections are read-only")
    devices = profile.get("devices", {})
    if not isinstance(devices, dict):
        return problems + ["devices must be an object"]
    ports = []
    for name in required_devices(profile):
        device = devices.get(name, {})
        if not isinstance(device, dict):
            problems.append(name + ": invalid device settings")
            continue
        port = device.get("port")
        if not isinstance(port, str) or not port.strip():
            problems.append(name + ": select a COM port")
        else:
            ports.append(os.path.normcase(port))
        if not device.get("expected_usb_serial"):
            problems.append(name + ": record adapter USB serial number")
        for identity_key in ("expected_usb_vid", "expected_usb_pid"):
            try:
                usb_id(device.get(identity_key))
            except ValueError:
                problems.append(name + ": " + identity_key + " must be a USB ID from 0 to 65535")
        if type(device.get("baud")) is not int or device["baud"] not in (4800, 9600, 19200, 38400, 57600, 115200):
            problems.append(name + ": invalid baud rate")
    if len(ports) != len(set(ports)):
        problems.append("Every instrument must have its own distinct COM port")
    evidence = ("operator", "date", "plumbing_evidence", "shutdown_evidence", "gas_mapping_evidence", "heater_test_evidence")
    for key in evidence:
        value = profile.get("commissioning", {}).get(key)
        if not isinstance(value, str) or len(value.strip()) < 3:
            problems.append("Commissioning: record " + key.replace("_", " "))
    core = devices.get("core2", {})
    if core.get("expected_firmware") != "cct-core2-usb-1":
        problems.append("Core2 requires cct-core2-usb-1 firmware")
    routes = profile.get("routes", {})
    for name in ROUTE_NAMES + OPTIONAL_ROUTE_NAMES:
        steps = routes.get(name)
        if name in OPTIONAL_ROUTE_NAMES and (steps is None or steps == []):
            continue
        if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
            problems.append("Verify route steps for " + name)
            continue
        for step in steps:
            if not isinstance(step, dict):
                problems.append("Invalid route step in " + name)
                continue
            mask, wait = step.get("mask"), step.get("wait_ms")
            if type(mask) is not int or not 0 <= mask <= 15:
                problems.append(name + ": routing mask must be 0..15; gas supply bits are controlled separately")
            if type(wait) is not int or not 0 <= wait <= 10000:
                problems.append(name + ": wait_ms must be 0..10000")
        final = steps[-1].get("mask") if isinstance(steps[-1], dict) else None
        if name in ("closed", "vent") and isinstance(final, int) and final & 0x30:
            problems.append(name + " must close both gas-supply outputs")
    safe = profile.get("safe_vent_mask")
    if type(safe) is not int or not 0 <= safe <= 15:
        problems.append("Verify safe_vent_mask (heater and both source valves off)")
    elif routes.get("vent") and isinstance(routes["vent"][-1], dict) and routes["vent"][-1].get("mask") != safe:
        problems.append("safe_vent_mask must equal the verified vent route final mask")
    if not manual:
        problems.extend(_calibrator_blockers(devices.get("calibrator", {})))
    alicat = devices.get("alicat", {})
    if not manual_device(profile, "alicat") and not re.fullmatch("[A-Z]", str(alicat.get("unit_id", ""))):
        problems.append("Alicat unit_id must be one uppercase letter")
    if not manual_device(profile, "alicat") and alicat.get("protocol") != "basis-labelled":
        problems.append("Alicat B-500C requires verified BASIS labelled output")
    if not manual_device(profile, "alicat") and alicat.get("gas_selection_verified") is not True:
        problems.append("Verify BASIS gas selection support for Air (0) and N2 (3)")
    if not manual_device(profile, "licor") and devices.get("licor", {}).get("baud") != 9600:
        problems.append("LI-840A requires 9600 baud")
    try:
        maximum = finite(profile.get("max_temperature_c"))
        if not 50 <= maximum <= 175:
            raise ValueError()
    except (ValueError, TypeError):
        problems.append("max_temperature_c must be verified between 50 and 175 °C")
    return list(dict.fromkeys(problems))


def _calibrator_blockers(cal):
    problems = []
    if cal.get("baud") != 9600 or cal.get("instrument_id") != 51:
        problems.append("146C known configuration is 9600 baud, instrument ID 51")
    actions = cal.get("gas_actions", {})
    for name in ("air", "n2", "off"):
        action = actions.get(name, {})
        if not isinstance(action, dict) or action.get("command") not in ALLOWED_146C - {"status"}:
            problems.append("Verify 146C command for " + name + "; no N2 command is assumed")
        if not isinstance(action, dict) or not isinstance(action.get("status_contains"), str) or len(action["status_contains"].strip()) < 3:
            problems.append("146C " + name + ": record distinctive status readback")
    if actions.get("air", {}).get("command") == actions.get("n2", {}).get("command"):
        problems.append("Air and N2 must map to distinct verified 146C configurations")
    for name in ("air", "n2"):
        if actions.get(name, {}).get("command") not in {"sel gas a span 0", "sel gas a span 1"}:
            problems.append("146C " + name + " must map to a verified span setting, not gas off")
    markers = [actions.get(name, {}).get("status_contains") for name in ("air", "n2", "off")]
    if any(not marker for marker in markers) or len(set(markers)) != 3:
        problems.append("146C Air/N2/off status readback markers must be distinct")
    if actions.get("off", {}).get("command") != "sel gas off":
        problems.append("146C off must map to sel gas off")
    if not isinstance(cal.get("status_identity_contains"), str) or len(cal["status_identity_contains"].strip()) < 3:
        problems.append("146C: record distinctive instrument status identity")
    return problems


def profile_blockers(profile):
    """A malformed user-editable file is a blocker, never an application crash."""
    try:
        return _profile_blockers(profile)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
        return ["Malformed hardware profile: check devices, commissioning, routes and gas_actions objects (" + str(exc) + ")"]
