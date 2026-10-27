"""Passive USB adapter inventory and saved-pairing resolution.

Discovery never opens a serial port or sends protocol probes. A USB adapter's
identity is a pairing aid, not proof of the instrument connected to its DB9.
"""

from collections import Counter
from copy import deepcopy
import os

from ..contracts import DEVICE_IDS, required_devices


def _text(value, label, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or (not optional and not value.strip()) or len(value) > 512:
        raise ValueError(label + " must be nonempty text" if not optional else label + " must be text or null")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(label + " contains control characters")
    return value or None if optional else value


def usb_id(value):
    """Normalize optional VID/PID integers or explicit 0x hexadecimal strings."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = int(value, 16 if value.lower().startswith("0x") else 10)
        except ValueError as exc:
            raise ValueError("USB VID/PID must be an integer or 0x hexadecimal value") from exc
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("USB VID/PID must be between 0 and 65535")
    return value


def port_key(port):
    """Use the host's path case rules, preserving Unix tty case differences."""
    value = os.path.normcase(os.path.normpath(port))
    # Win32 permits both COM12 and \\.\COM12 for the same endpoint.
    if os.name == "nt" and value.startswith("\\\\.\\"):
        value = value[4:]
    return value


def _adapter(item):
    if not isinstance(item, dict):
        raise ValueError("Every adapter inventory entry must be an object")
    return {
        "port": _text(item.get("port"), "Adapter port"),
        "usb_serial": _text(item.get("usb_serial"), "USB serial", optional=True),
        "vid": usb_id(item.get("vid")), "pid": usb_id(item.get("pid")),
        "description": _text(item.get("description", ""), "Description", optional=True) or "",
        "manufacturer": _text(item.get("manufacturer"), "Manufacturer", optional=True),
        "location": _text(item.get("location"), "USB location", optional=True),
    }


def enumerate_adapters():
    """Return OS serial inventory only; no Serial objects or wire traffic.

    Raises RuntimeError if inventory cannot be obtained rather than claiming
    that an enumeration failure means all adapters have been unplugged.
    """
    try:
        from serial.tools import list_ports
        adapters = [_adapter({"port": item.device, "usb_serial": item.serial_number,
                              "vid": item.vid, "pid": item.pid,
                              "description": item.description, "manufacturer": item.manufacturer,
                              "location": item.location}) for item in list_ports.comports()]
        return sorted(adapters, key=lambda item: port_key(item["port"]))
    except Exception as exc:
        raise RuntimeError("USB adapter enumeration failed: " + str(exc)) from exc


def resolve_profile(profile, adapters=None):
    """Resolve saved adapter identities to current ports without opening them.

    An unmatched role's copied port is cleared, never left at its stale saved
    value. Duplicate serials, device paths or role assignments fail closed.
    Optional expected_usb_vid/pid constrain a serial match but cannot make a
    duplicated serial number unique. No fallback uses description/location.
    """
    resolved = deepcopy(profile) if isinstance(profile, dict) else {}
    result = {"profile": resolved, "devices": {}, "blockers": [], "adapters": []}
    try:
        if not isinstance(profile, dict) or not isinstance(profile.get("devices"), dict):
            raise ValueError("Hardware profile devices must be an object")
        unknown = set(profile["devices"]) - set(DEVICE_IDS)
        if unknown:
            raise ValueError("Unknown device roles: " + ", ".join(sorted(map(str, unknown))))
        for name, settings in profile["devices"].items():
            if not isinstance(settings, dict):
                raise ValueError(name + " device settings must be an object")
        source = enumerate_adapters() if adapters is None else adapters
        if not isinstance(source, (list, tuple)):
            raise ValueError("Adapter inventory must be a list")
        inventory = [_adapter(item) for item in source]
        result["adapters"] = inventory
    except (ValueError, TypeError, RuntimeError) as exc:
        result["blockers"].append(str(exc))
        devices = resolved.setdefault("devices", {})
        if not isinstance(devices, dict):
            resolved["devices"] = devices = {}
        for name in DEVICE_IDS:
            if not isinstance(devices.get(name), dict):
                devices[name] = {}
            devices[name]["port"] = ""
            result["devices"][name] = {"state": "unpaired", "port": None, "usb_serial": None,
                                       "message": "Discovery unavailable: " + str(exc)}
        return result

    serial_counts = Counter(item["usb_serial"] for item in inventory if item["usb_serial"])
    path_counts = Counter(port_key(item["port"]) for item in inventory)
    active_roles = required_devices(profile)
    assignments = Counter(settings.get("expected_usb_serial") for role, settings in profile["devices"].items() if role in active_roles
                          if isinstance(settings.get("expected_usb_serial"), str) and settings.get("expected_usb_serial"))
    for name in DEVICE_IDS:
        settings = resolved["devices"].setdefault(name, {})
        expected = settings.get("expected_usb_serial")
        settings["port"] = ""
        state = {"state": "unpaired", "port": None,
                 "usb_serial": expected if isinstance(expected, str) else None, "message": ""}
        result["devices"][name] = state
        if name not in active_roles:
            state.update(state="disabled", message=name + " operated manually; this serial port stays closed")
            continue
        try:
            if expected is None or expected == "":
                state["message"] = "Pair this device with an adapter's unique USB serial number; unknown ports stay closed"
                continue
            _text(expected, "Expected USB serial")
            wanted_vid = usb_id(settings.get("expected_usb_vid"))
            wanted_pid = usb_id(settings.get("expected_usb_pid"))
            if "expected_usb_vid" in settings:
                settings["expected_usb_vid"] = wanted_vid
            if "expected_usb_pid" in settings:
                settings["expected_usb_pid"] = wanted_pid
            if assignments[expected] > 1:
                state.update(state="ambiguous", message="The same USB serial is paired with multiple device roles")
                continue
            candidates = [item for item in inventory if item["usb_serial"] == expected]
            if serial_counts[expected] > 1:
                state.update(state="ambiguous", message="Multiple detected adapters have this USB serial; automatic assignment is disabled")
            elif not candidates:
                state.update(state="missing", message="Paired USB adapter is missing; check power, cable and hub")
            elif path_counts[port_key(candidates[0]["port"])] > 1:
                state.update(state="ambiguous", message="Duplicate serial device paths were detected; automatic assignment is disabled")
            elif (wanted_vid is not None and candidates[0]["vid"] != wanted_vid) or (wanted_pid is not None and candidates[0]["pid"] != wanted_pid):
                state.update(state="missing", message="USB serial was found but its VID/PID does not match the saved pairing")
            else:
                match = candidates[0]
                settings["port"] = match["port"]
                state.update(state="matched", port=match["port"],
                             message="Paired USB adapter found at " + match["port"] + "; instrument protocol still needs verification")
        except (ValueError, TypeError) as exc:
            state["message"] = "Invalid saved pairing: " + str(exc)
        finally:
            if state["state"] != "matched":
                result["blockers"].append(name + ": " + state["message"])
    return result
