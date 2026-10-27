"""Deterministic apparatus simulation. No serial ports or physical outputs are used."""

import math

from .contracts import CHANNELS, DEVICE_IDS


class SimulationRig:
    def __init__(self):
        self.connected = False
        self.time = 0.0
        self.stage_time = 0.0
        self.stage = "idle"
        self.fault = ""
        self.values = {key: None for key in CHANNELS}
        self.ages = {key: 0.0 for key in CHANNELS}
        self.outputs = {"route": "closed", "gas": "off", "heater_target_c": None}
        self.events = []
        self.temperature = 24.0
        self.analyzer_temperature = 45.0

    def connect(self):
        self.connected = True
        self.fault = ""
        self.tick(0.0)
        self.events.append({"level": "info", "event": "connection", "message": "Simulation connected; no physical equipment is controlled."})

    def disconnect(self):
        self.execute("safe_state")
        self.connected = False

    def execute(self, action, **kwargs):
        if action in {"safe_state", "emergency_stop"}:
            self.outputs.update(route="vent", gas="off", heater_target_c=None)
        elif action == "route":
            self.outputs["route"] = kwargs["name"]
        elif action == "gas":
            self.outputs["gas"] = kwargs["name"]
        elif action == "heater":
            self.outputs["heater_target_c"] = kwargs.get("target_c")
        else:
            raise ValueError(f"Unknown rig action: {action}")
        self.events.append({"level": "info", "event": "command_ack", "message": f"Simulation acknowledged {action}", "action": action, "arguments": kwargs})

    def tick(self, dt, stage="idle"):
        if not self.connected:
            return
        if stage != self.stage:
            self.stage, self.stage_time = stage, 0.0
        self.time += dt
        self.stage_time += dt
        for key in self.ages:
            self.ages[key] += dt
        target = self.outputs["heater_target_c"]
        if target is None:
            self.temperature += (24.0 - self.temperature) * (1.0 - math.exp(-dt / 105.0))
        else:
            self.temperature += max(-1.0 * dt, min(1.3 * dt, target - self.temperature))
        self.analyzer_temperature += (51.5 - self.analyzer_temperature) * (1.0 - math.exp(-dt / 12.0))
        noise = 0.6 * math.sin(self.time * 0.37) + 0.25 * math.sin(self.time * 0.91)
        route, gas = self.outputs["route"], self.outputs["gas"]
        if stage in {"breakthrough", "core_breakthrough_column"} and route == "column":
            fraction = 1.0 / (1.0 + math.exp(-(self.stage_time - 150.0) / 28.0))
            co2 = 420.0 * fraction + noise * 0.3
        elif stage in {"initial_release", "purge_release", "desorption"} and gas == "n2":
            co2 = 0.4 + 230.0 * (1.0 - math.exp(-self.stage_time / 12.0)) * math.exp(-self.stage_time / 50.0) + noise * 0.12
        elif gas == "air":
            co2 = 420.0 + noise
        else:
            co2 = 0.4 + noise * 0.1
        new_values = {
            "co2_ppm": max(0.0, co2),
            "flow_sccm": (500.0 + 0.7 * math.sin(self.time * 0.2)) if gas != "off" else 0.0,
            "column_temp_c": self.temperature,
            "ambient_temp_c": 24.0 + 0.1 * math.sin(self.time / 70.0),
            "ambient_pressure_kpa": 101.3 + 0.03 * math.sin(self.time / 100.0),
            "analyzer_temp_c": self.analyzer_temperature,
        }
        if self.fault == "overtemp":
            new_values["column_temp_c"] = 210.0
        if self.fault == "flow_loss":
            new_values["flow_sccm"] = 0.0
        for key, value in new_values.items():
            device = CHANNELS[key][2]
            if self.fault in {"stale", "reading_loss", "licor_disconnect"} and device == "licor":
                continue
            if key.startswith("ambient_") and self.ages[key] < 1.5 and self.values[key] is not None:
                continue
            self.values[key] = value
            self.ages[key] = 0.0

    def snapshot(self):
        devices = {}
        for device in DEVICE_IDS:
            connected = self.connected and not (self.fault == "licor_disconnect" and device == "licor")
            relevant = [key for key, (_, _, owner) in CHANNELS.items() if owner == device]
            fresh = connected and all(self.values[key] is not None and self.ages[key] <= 5.0 for key in relevant)
            devices[device] = {"connected": connected, "fresh": fresh, "message": "Simulated" if fresh else "Simulated connection or reading fault"}
        return {"values": dict(self.values), "ages": dict(self.ages), "devices": devices,
                "outputs": dict(self.outputs), "ready": self.connected and not self.fault,
                "blockers": [] if self.connected and not self.fault else ["Connect the simulation and clear injected faults."]}

    def inject_fault(self, name):
        if name not in {"", "clear", "stale", "reading_loss", "licor_disconnect", "overtemp", "flow_loss"}:
            raise ValueError("Choose clear, stale, licor_disconnect, overtemp, or flow_loss.")
        self.fault = "" if name == "clear" else name
        self.events.append({"level": "warning", "event": "injected_fault", "message": f"Simulation fault: {name or 'clear'}"})

    def drain_events(self):
        events, self.events = self.events, []
        return events
