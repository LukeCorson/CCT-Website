"""Nonblocking real serial rig. Each port has exactly one worker/owner.

Connecting performs read-only discovery; executable profiles and the versioned
Core2 ownership handshake are both required before any actuator command.
"""

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import threading
import time
import uuid

from ..contracts import CHANNELS, DEVICE_IDS, manual_calibrator, manual_device, required_devices, required_channels
from .discovery import enumerate_adapters, port_key, resolve_profile
from .profile import ROUTE_NAMES, OPTIONAL_ROUTE_NAMES, profile_blockers
from .protocols import (LineFramer, LicorFramer, calibrator_frame, core_message,
                        finite, licor_output_config, parse_alicat, parse_licor)


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate hardware profile field or device role: " + key)
        obj[key] = value
    return obj


class HardwareRig:
    def __init__(self, profile_path=None):
        self.profile_path = Path(profile_path) if profile_path else Path(__file__).resolve().parents[2] / "config" / "hardware.local.json"
        self._profile = {}
        self._profile_errors = []
        self._load_errors = []
        try:
            self._profile = json.loads(self.profile_path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
            self._profile_errors = profile_blockers(self._profile)
            if not isinstance(self._profile, dict) or not isinstance(self._profile.get("devices", {}), dict):
                self._profile = {}
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            self._load_errors = ["Apparatus setup is not ready. Create and commission config/hardware.local.json before physical control. " + str(exc)]
            self._profile_errors = list(self._load_errors)
        self._configured_profile = deepcopy(self._profile)
        self._discovery = {"devices": {}, "blockers": [], "adapters": []}
        self._lock = threading.RLock()
        self._events = deque(maxlen=4096)
        self._workers = {}
        self._values = {key: None for key in CHANNELS}
        self._received = {}
        self._devices = {key: {"connected": False, "fresh": False, "message": "Not connected"} for key in DEVICE_IDS}
        self._device_received = {}
        self._outputs = {"route": "unknown", "gas": "unknown", "heater_on": False,
                         "desired_mask": None, "applied_mask": None, "pending_count": 0,
                         "command_fault": "", "owned": False, "s3_online": False}
        self._pending = {}
        self._active = False
        self._heartbeat_tick = 0.0
        self._session = uuid.uuid4().hex
        self._boot_id = None
        self._handshake = False

    def _event(self, kind, **details):
        with self._lock:
            self._events.append({"kind": kind, "at_monotonic": time.monotonic(), **details})

    def _set_device(self, name, connected, message):
        with self._lock:
            self._devices[name].update(connected=connected, message=str(message)[:240])

    def _sample(self, device, values, ages=None):
        now = time.monotonic()
        with self._lock:
            for key, value in values.items():
                if key not in CHANNELS or CHANNELS[key][2] != device:
                    continue
                if value is None:
                    self._values[key] = None
                    self._received.pop(key, None)
                else:
                    age = finite((ages or {}).get(key, 0))
                    if age < 0 or age > 3600:
                        raise ValueError("Invalid sample age")
                    self._values[key] = finite(value)
                    self._received[key] = now - age
            self._device_received[device] = now

    def _part_done(self, group, part, error=None):
        with self._lock:
            record = self._pending.get(group)
            if not record:
                return
            record["remaining"].discard(part)
            if error:
                record["errors"].append(str(error))
                self._outputs["command_fault"] = str(error)
            if not record["remaining"]:
                if not record["errors"] and record.get("later"):
                    next_commands = record["later"].pop(0)
                    record["remaining"] = set(range(len(next_commands)))
                    try:
                        self._dispatch(group, next_commands)
                        return
                    except (ValueError, queue.Full) as exc:
                        record["errors"].append(str(exc))
                        self._outputs["command_fault"] = str(exc)
                self._pending.pop(group)
                errors = record["errors"]
                self._event("command_error" if errors else "command_ack", command_id=group,
                            action=record["action"], complete=True,
                            message="; ".join(errors) if errors else "Completed and acknowledged")

    def connect(self):
        if self._active:
            return
        if any(worker.is_alive() for worker in self._workers.values()):
            self._event("connection_error", message="Previous serial sessions are still closing; retry in a moment")
            return
        # Passive inventory maps saved USB identities to current OS port names.
        # Unpaired/missing/ambiguous entries have no executable port fallback.
        resolution = resolve_profile(self._configured_profile)
        self._profile = resolution["profile"]
        self._discovery = {key: deepcopy(resolution[key]) for key in ("devices", "blockers", "adapters")}
        self._profile_errors = list(dict.fromkeys(self._load_errors + profile_blockers(self._profile) + resolution["blockers"]))
        devices = self._profile.get("devices", {})
        self._active = True
        self._workers.clear()
        self._boot_id = None
        self._session = uuid.uuid4().hex
        self._outputs["owned"] = False
        self._heartbeat_tick = time.monotonic()
        self._outputs["command_fault"] = ""
        for name in DEVICE_IDS:
            settings = devices.get(name, {})
            discovery = resolution["devices"][name]
            if discovery["state"] != "matched":
                self._set_device(name, False, discovery["message"])
                continue
            if not isinstance(settings, dict):
                self._set_device(name, False, "Invalid device settings in hardware profile")
                continue
            if not settings.get("port"):
                self._set_device(name, False, "Select a COM port in hardware profile")
                continue
            worker = _Worker(self, name, settings)
            self._workers[name] = worker
            worker.start()

    def disconnect(self):
        # Closing a live session invokes the known local shutdown, not a resume.
        if self._active and self._outputs.get("owned") and "core2" in self._workers:
            self._workers["core2"].stop_after_release = True
            self._workers["core2"].request_stop.set()
        for name, worker in self._workers.items():
            if name != "core2" or not self._outputs.get("owned"):
                worker.request_stop.set()
        self._active = False
        with self._lock:
            for key in self._devices:
                self._devices[key].update(connected=False, fresh=False, message="Disconnected")
            self._outputs["owned"] = False
            self._handshake = False
            for group, record in list(self._pending.items()):
                self._event("command_error", command_id=group, action=record["action"], complete=True,
                            message="Disconnected before command completion")
            self._pending.clear()

    def tick(self, dt, stage="idle"):
        self._heartbeat_tick = time.monotonic()

    def snapshot(self):
        now = time.monotonic()
        with self._lock:
            ages = {key: now - self._received[key] if key in self._received else 1e12 for key in CHANNELS}
            devices = deepcopy(self._devices)
            for name, device in devices.items():
                device["required"] = name in required_devices(self._profile)
                device["fresh"] = bool(device["connected"] and now - self._device_received.get(name, -1e12) <= 5)
            blockers = list(self._profile_errors)
            blockers.extend(f"{name}: {device['message']}" for name, device in devices.items() if name in required_devices(self._profile) and not device["fresh"])
            for channel, (label, _, _) in CHANNELS.items():
                if CHANNELS[channel][2] not in required_devices(self._profile):
                    continue
                if self._values[channel] is None or ages[channel] > 5:
                    blockers.append(f"{label}: waiting for a fresh measurement; check the device output configuration")
            if not self._handshake:
                blockers.append("Core2 USB bridge handshake not complete")
            if not self._outputs["owned"]:
                blockers.append("Computer does not own Core2 control session")
            if not self._outputs.get("s3_online"):
                blockers.append("Core2–S3 link/readback is not ready")
            if self._outputs["command_fault"]:
                blockers.append(self._outputs["command_fault"])
            if self._pending:
                blockers.append("Wait for the current instrument operation to finish")
            outputs = deepcopy(self._outputs)
            outputs["pending_count"] = len(self._pending)
            return {"values": dict(self._values), "ages": ages, "devices": devices,
                    "outputs": outputs, "ready": not blockers, "blockers": list(dict.fromkeys(blockers)),
                    "safety_limits": {"max_temperature_c": self._profile.get("max_temperature_c", 175),
                                      "min_heater_target_c": 50},
                    "profile": deepcopy(self._profile), "profile_path": str(self.profile_path),
                    "calibrator_mode": "manual" if manual_calibrator(self._profile) else "automatic",
                    "manual_devices": [role for role in DEVICE_IDS if manual_device(self._profile, role)],
                    "configured_profile": deepcopy(self._configured_profile), "discovery": deepcopy(self._discovery),
                    "warnings": (["146C MANUAL: set Local/manual at its front panel. The app neither monitors nor controls the 146C; verify gas supplies and close them manually when required."] if manual_calibrator(self._profile) else []) +
                                (["Alicat MANUAL: flow is not measured or protected by the app. Verify gas correction and continuously monitor flow at the instrument."] if manual_device(self._profile, "alicat") else []) +
                                (["LI-COR MANUAL: no CO₂ measurements are recorded by the app. Automatic breakthrough cycles are unavailable."] if manual_device(self._profile, "licor") else []) +
                                ["Hardware commands require supervised commissioning; no auto-resume after reconnect."]}

    def drain_events(self):
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def execute(self, action, **kwargs):
        group = uuid.uuid4().hex
        try:
            if self._profile_errors:
                raise ValueError(self._profile_errors[0])
            commands = self._commands(action, kwargs)
            later = []
            if action == "gas" and kwargs.get("name") in ("air", "n2"):
                # First close physical source valves, then configure both gas
                # instruments, then reopen exactly one reviewed source valve.
                later = ([commands[1:]] if commands[1:] else []) + [[("core2", "gas", {"name": kwargs["name"]})]]
                commands = commands[:1]
            safety = action in ("safe_state", "emergency_stop")
            if safety:
                self._cancel_commands()
            if not self._active or ((not self._handshake or not self._outputs["owned"]) and not safety):
                raise ValueError("No owned Core2 bridge session; commands disabled")
            errors = []
            available = []
            for device, _, _ in commands + [command for phase in later for command in phase]:
                if device not in self._workers or not self._devices[device]["connected"]:
                    errors.append(device + " is not connected")
                elif device == "core2" and not self._outputs["owned"]:
                    errors.append("Core2 session is not owned")
                if device in self._workers and self._workers[device].outbound.full():
                    raise ValueError(device + " command queue is full")
            if errors and not safety:
                raise ValueError("; ".join(errors))
            for command in commands:
                device = command[0]
                if device in self._workers and self._devices[device]["connected"] and (device != "core2" or self._outputs["owned"]):
                    available.append(command)
            commands = available
            if not commands:
                raise ValueError("; ".join(errors) or "No device available for shutdown")
            with self._lock:
                self._pending[group] = {"action": action, "remaining": set(range(len(commands))), "errors": errors,
                                        "later": later, "phase": 0, "safety": safety}
            self._dispatch(group, commands)
            self._event("command_queued", command_id=group, action=action, arguments=kwargs)
        except (ValueError, KeyError, TypeError, queue.Full) as exc:
            self._outputs["command_fault"] = str(exc)
            self._event("command_error", command_id=group, action=action, complete=True, message=str(exc))
        return group

    def _cancel_commands(self):
        """Stop owns the queues; cancelled gas groups can never open a source."""
        with self._lock:
            for group, record in self._pending.items():
                self._event("command_cancelled", command_id=group, action=record["action"],
                            message="Superseded by shutdown")
            self._pending.clear()
            for worker in self._workers.values():
                for buffer in (worker.outbound, worker.urgent):
                    while True:
                        try:
                            buffer.get_nowait()
                        except queue.Empty:
                            break

    def _dispatch(self, group, commands):
        """Called under the rig lock when a phase's acknowledgements complete."""
        for device, _, _ in commands:
            if device not in self._workers or not self._devices[device]["connected"]:
                raise ValueError(device + " disconnected during gas change")
            if self._workers[device].outbound.full():
                raise ValueError(device + " command queue full during gas change")
        record = self._pending[group]
        record["phase"] = record.get("phase", 0) + 1
        for part, (device, kind, payload) in enumerate(commands):
            wire_payload = dict(payload)
            wire_payload["_phase"] = record["phase"]
            buffer = self._workers[device].urgent if record.get("safety") else self._workers[device].outbound
            buffer.put_nowait((group, part, kind, wire_payload))

    def _commands(self, action, kwargs):
        if action in ("safe_state", "emergency_stop"):
            if manual_calibrator(self._profile):
                return [("core2", "safe_state", {})]
            return [("core2", "safe_state", {}), ("calibrator", "gas", {"name": "off"})]
        if action == "route" and kwargs.get("name") in ROUTE_NAMES + OPTIONAL_ROUTE_NAMES:
            if not self._profile.get("routes", {}).get(kwargs["name"]):
                raise ValueError("The selected route is not commissioned: " + kwargs["name"])
            return [("core2", "route", {"name": kwargs["name"]})]
        if action == "heater":
            target = kwargs.get("target_c")
            if target is not None:
                target = finite(target)
                if not 50 <= target < finite(self._profile.get("max_temperature_c", 175)):
                    raise ValueError("Heater target outside verified range")
            return [("core2", "heater", {"target_c": target})]
        if action == "valve" and kwargs.get("name") in {"exhaust", "check", "bypass", "mfm_selector", "nitrogen", "compressed_air"} and type(kwargs.get("on")) is bool:
            return [("core2", "valve", {"name": kwargs["name"], "on": kwargs["on"]})]
        if action == "gas" and kwargs.get("name") in ("air", "n2", "off"):
            name = kwargs["name"]
            result = [("core2", "gas", {"name": "off"})]
            if not manual_calibrator(self._profile):
                result.append(("calibrator", "gas", {"name": name}))
            if name != "off" and not manual_device(self._profile, "alicat"):
                result.append(("alicat", "gas", {"name": name}))
            return result
        raise ValueError("Unknown or invalid hardware action: " + str(action))


class _Worker(threading.Thread):
    def __init__(self, rig, name, settings):
        super().__init__(name="cct-" + name, daemon=True)
        self.rig, self.name_id, self.settings = rig, name, settings
        self.outbound = queue.Queue(maxsize=32)
        self.urgent = queue.Queue(maxsize=4)
        self.request_stop = threading.Event()
        self.stop_after_release = False
        self.serial = None
        self.framer = LicorFramer() if name == "licor" else LineFramer()
        self.active = None
        self.deadline = 0.0
        self.last_poll = 0.0
        self.last_status = 0.0
        self.claim_sent = False
        self.claim_time = 0.0
        self.verified = False
        self.gas_status_phase = False

    def send(self, data):
        if self.serial.write(data) != len(data):
            raise OSError("Incomplete serial write")

    def core_send(self, op, command_id=None, **args):
        obj = {"protocol": "cct-usb", "version": 1, "op": op,
               "id": command_id or uuid.uuid4().hex, "session": self.rig._session, **args}
        self.send(json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n")

    def run(self):
        rig, name = self.rig, self.name_id
        try:
            import serial
            # Repeat the complete matching check immediately before opening. A
            # cable may move after the connection page's inventory was taken.
            verification = resolve_profile(rig._profile, enumerate_adapters())
            match = verification["devices"][name]
            if match["state"] != "matched" or port_key(match["port"]) != port_key(self.settings["port"]):
                raise ValueError("USB adapter identity changed before connection: " + match["message"] + "; reconnect after checking cable labels")
            # Set DTR/RTS before open; some adapters can still glitch on open.
            # Firmware boots without writing outputs and never resumes a run.
            self.serial = serial.Serial(port=None, baudrate=self.settings.get("baud", 9600),
                                        bytesize=8, parity="N", stopbits=1, timeout=0.05,
                                        write_timeout=0.25, xonxoff=False, rtscts=False, dsrdtr=False)
            self.serial.dtr = False
            self.serial.rts = False
            self.serial.port = self.settings["port"]
            self.serial.open()
            opened_at = time.monotonic()
            wait_message_shown = False
            rig._set_device(name, True, "Port open; verifying protocol")
            if name == "licor":
                self.send(b"<LI840><DATA>?</DATA></LI840>\n")
            while not self.request_stop.is_set():
                now = time.monotonic()
                self._periodic(now)
                try:
                    priority = self.urgent.get_nowait()
                    self.active = priority
                    self._start(now)
                except queue.Empty:
                    pass
                if self.active is None:
                    try:
                        self.active = self.outbound.get_nowait()
                        self._start(now)
                    except queue.Empty:
                        pass
                chunk = self.serial.read(min(max(self.serial.in_waiting, 1), 4096))
                for frame in self.framer.feed(chunk):
                    # LI-COR XML may contain H2O; record canonical CO2/temperature only.
                    if name != "licor":
                        rig._event("raw_frame", device=name, received_utc=datetime.now(timezone.utc).isoformat(),
                                   raw_hex=frame.hex(), raw_ascii=frame.decode("ascii", errors="replace"))
                    try:
                        self._receive(frame, now)
                    except (ValueError, UnicodeError, KeyError, TypeError) as exc:
                        rig._set_device(name, True, "Invalid frame: " + str(exc))
                        rig._event("protocol_warning", device=name, message=str(exc))
                if self.active and now > self.deadline:
                    self._finish("Command acknowledgement timed out on " + name)
                if name == "core2" and self.claim_sent and not rig._outputs["owned"] and now - self.claim_time > 5:
                    raise ValueError("Core2 control claim failed; reconnect after reviewing firmware status")
                if not wait_message_shown and now - opened_at > 5:
                    if name == "core2" and not rig._handshake:
                        rig._set_device(name, True, "USB adapter found, but Core2 did not answer cct-usb v1; upload or check the bridge firmware")
                        wait_message_shown = True
                    elif name == "licor" and name not in rig._device_received:
                        rig._set_device(name, True, "USB adapter found, but no LI-COR XML measurements arrived; check 9600 baud, null-modem cable, and RS-232 output")
                        wait_message_shown = True
            if name == "core2" and self.stop_after_release:
                self.core_send("release")
        except Exception as exc:
            rig._set_device(name, False, str(exc))
            rig._event("connection_error", device=name, message=str(exc))
            if name == "core2":
                rig._handshake = False
                rig._outputs["owned"] = False
        finally:
            if self.active:
                self._finish("Port closed before acknowledgement")
            while not self.outbound.empty():
                group, part, _, _ = self.outbound.get_nowait()
                rig._part_done(group, part, "Port closed before command was sent")
            if self.serial:
                self.serial.close()

    def _periodic(self, now):
        if self.name_id == "core2" and now - self.last_poll >= 0.5:
            if self.rig._handshake and self.rig._outputs["owned"]:
                if now - self.rig._heartbeat_tick < 2:
                    self.core_send("heartbeat")
            else:
                self.core_send("hello")
            self.last_poll = now
        elif self.name_id == "licor" and now - self.last_poll >= 1:
            # Query only when the existing stream has not supplied recent data.
            if now - self.rig._device_received.get("licor", 0) > 1.2:
                self.send(b"<LI840><DATA>?</DATA></LI840>\n")
            self.last_poll = now
        elif self.name_id == "alicat" and now - self.last_poll >= 0.5:
            unit = self.settings.get("unit_id", "A")
            if len(unit) != 1 or unit not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                raise ValueError("Invalid BASIS unit ID")
            self.send(unit.encode("ascii") + b"\r")
            self.last_poll = now
        elif self.name_id == "calibrator":
            if self.active and not self.gas_status_phase and now - self.last_status >= 0.3:
                self.send(calibrator_frame("status", self.settings.get("instrument_id", 51)))
                self.gas_status_phase = True
            elif now - self.last_poll >= 1 and not self.active:
                self.send(calibrator_frame("status", self.settings.get("instrument_id", 51)))
                self.last_poll = now

    def _start(self, now):
        group, part, kind, payload = self.active
        if group not in self.rig._pending:
            self.active = None
            return
        self.deadline = now + (90 if self.name_id == "core2" and kind == "route" else 8)
        if self.name_id == "core2":
            wire_payload = {key: value for key, value in payload.items() if not key.startswith("_")}
            self.core_send(kind, group + "-" + str(payload.get("_phase", 0)) + "-" + str(part), **wire_payload)
        elif self.name_id == "calibrator":
            command = self.settings["gas_actions"][payload["name"]]["command"]
            self.send(calibrator_frame(command, self.settings.get("instrument_id", 51)))
            # Only a subsequent status response can acknowledge delivery selection.
            self.gas_status_phase = False
            self.last_status = now
        elif self.name_id == "alicat":
            gas_number = 0 if payload["name"] == "air" else 3
            self.send((self.settings["unit_id"] + "g" + str(gas_number) + "\r").encode("ascii"))

    def _finish(self, error=None):
        group, part, _, _ = self.active
        self.active = None
        self.rig._part_done(group, part, error)

    def _receive(self, frame, now):
        rig, name = self.rig, self.name_id
        if name == "licor":
            values = parse_licor(frame)
            if values:
                rig._sample(name, values)
                rig._set_device(name, True, "LI-840A XML measurements")
        elif name == "alicat":
            values, gas, exhaust = parse_alicat(frame, self.settings["unit_id"])
            if exhaust:
                raise ValueError("Unexpected BASIS EXH status")
            rig._sample(name, values)
            rig._set_device(name, True, "BASIS meter • " + gas)
            rig._outputs["meter_gas"] = gas
            if self.active:
                expected = "Air" if self.active[3]["name"] == "air" else "N2"
                if gas.upper() == expected.upper():
                    self._finish()
        elif name == "calibrator":
            if frame.startswith(bytes([0xB3])):
                frame = frame[1:]
            text = frame.decode("ascii", errors="strict").strip()
            # Many 146C replies prefix 0xB3: remove only that known address byte.
            identity = self.settings.get("status_identity_contains")
            if identity and identity in text:
                rig._device_received[name] = now
                rig._set_device(name, True, "146C status identity verified")
            if self.active:
                if not self.gas_status_phase and now - self.last_status >= 0.1:
                    self.send(calibrator_frame("status", self.settings.get("instrument_id", 51)))
                    self.gas_status_phase = True
                    return
                marker = self.settings["gas_actions"][self.active[3]["name"]]["status_contains"]
                if self.gas_status_phase and marker and marker in text and identity and identity in text:
                    rig._outputs["gas"] = self.active[3]["name"]
                    self._finish()
        else:
            obj = core_message(frame)
            boot = obj.get("boot_id")
            if rig._boot_id is not None and boot and boot != rig._boot_id:
                raise OSError("Core2 restarted; automatic resume is disabled")
            if boot:
                rig._boot_id = boot
            if obj["type"] == "hello":
                if obj.get("firmware") != "cct-core2-usb-1":
                    raise ValueError("Unsupported Core2 firmware")
                rig._handshake = True
                if not rig._profile_errors and not self.claim_sent:
                    self.core_send("claim", "claim", safe_vent_mask=rig._profile["safe_vent_mask"],
                                   max_temperature_c=rig._profile["max_temperature_c"],
                                   routes=rig._profile["routes"])
                    self.claim_sent = True
                    self.claim_time = now
            elif obj["type"] == "status":
                ages = {key: finite(age) / 1000 for key, age in obj.get("ages_ms", {}).items()}
                rig._sample(name, obj.get("values", {}), ages)
                outputs = obj.get("outputs", {})
                for key in ("route", "heater_on", "desired_mask", "applied_mask", "s3_online", "heater_target_c", "source_valves", "valves"):
                    if key in outputs:
                        rig._outputs[key] = outputs[key]
                if manual_calibrator(rig._profile) and "source_valves" in outputs:
                    rig._outputs["gas"] = {0: "off", 16: "n2", 32: "air"}.get(outputs["source_valves"], "unknown")
                rig._outputs["owned"] = obj.get("owner") == rig._session
                rig._set_device(name, True, "USB bridge owned" if rig._outputs["owned"] else "USB bridge read-only")
                if obj.get("fault"):
                    rig._outputs["command_fault"] = str(obj["fault"])
            elif obj["type"] in ("ack", "error"):
                if obj.get("id") == "claim" and obj["type"] == "ack":
                    rig._outputs["owned"] = True
                if self.active:
                    expected = self.active[0] + "-" + str(self.active[3].get("_phase", 0)) + "-" + str(self.active[1])
                    if obj.get("id") == expected:
                        if obj["type"] == "error":
                            self._finish(str(obj.get("message", "Core2 command rejected")))
                        elif obj.get("complete") is True:
                            self._finish()
            elif obj["type"] == "event":
                rig._event("firmware_event", device=name, message=obj.get("message", ""))
