"""The original Core2 procedure controls, separate from automatic cycles.

Zero starts nitrogen bypass; Adsorption starts with air OFF; Breakthrough
starts in bypass and switches only on request; Desorption starts with heat OFF.
The existing acknowledged rig operations and safety engine remain authoritative.
"""

from .engine import ExperimentEngine, STAGES, finite

STAGES.update({
    "core_zero": ("Zero", "Nitrogen bypass to the analyzer. End when finished."),
    "core_adsorption": ("Adsorption", "Column to exhaust. Use Air on / Air off."),
    "core_breakthrough_bypass": ("Breakthrough — bypass", "Air bypasses the column. Choose Through column when ready."),
    "core_breakthrough_column": ("Breakthrough — through column", "Recording column outlet. Return to bypass or End test when ready."),
    "core_desorption": ("Desorption", "Nitrogen through the column. Heating starts only when requested."),
})


class CoreProcedureEngine(ExperimentEngine):
    def __init__(self, rig, config, emit):
        super().__init__(rig, config, emit)
        self.air_on = False
        self.column_on = False
        self.heater_target = None
        self._acquired = False
        self._flow_missing_s = 0.0
        self.summary["procedure"] = "Core2 operator-controlled test"

    def _core_stage(self, stage, commands):
        self.stage = stage
        self.stage_elapsed = self.stage_wall_elapsed = 0.0
        self.stage_progress = None
        self.waiting_for = STAGES[stage][1]
        self.command_plan.clear()
        self.command_plan.extend(commands)
        self.history.clear()
        self._flow_missing_s = 0.0
        self._event("stage", STAGES[stage][0], stage=stage, cycle=1)
        self._pump_commands()

    def start(self):
        self.running = True
        kind = self.test_type
        stage = "core_breakthrough_bypass" if kind == "breakthrough" else "core_" + kind
        route = {"zero": "zero", "adsorption": "adsorption", "breakthrough": "bypass", "desorption": "purge"}[kind]
        source = {"zero": "n2", "adsorption": "off", "breakthrough": "air", "desorption": "n2"}[kind]
        self.air_on = source == "air"
        self._core_stage(stage, [("heater", {"target_c": None}), ("gas", {"name": "off"}),
                                 ("route", {"name": route}), ("gas", {"name": source})])

    def controls(self):
        snapshot = self.rig.snapshot()
        outputs = snapshot.get("outputs", {})
        ready = snapshot.get("ready", False) and self.running and self.stage.startswith("core_") and not self.command_plan and not outputs.get("pending_count") and not outputs.get("command_fault")
        return {"ready": ready, "test_type": self.test_type, "air_on": self.air_on,
                "column_on": self.column_on, "heater_target_c": self.heater_target}

    def action(self, action, value=None):
        if not self.controls()["ready"]:
            raise RuntimeError("Wait for the current Core operation to finish.")
        if action == "air" and self.test_type == "adsorption" and isinstance(value, bool):
            self.air_on = value
            self.command_plan.append(("gas", {"name": "air" if value else "off"}))
            self._flow_missing_s = 0.0
        elif action == "column" and self.test_type == "breakthrough" and isinstance(value, bool):
            self.column_on = value
            self._core_stage("core_breakthrough_column" if value else "core_breakthrough_bypass",
                             [("route", {"name": "column" if value else "bypass"})])
        elif action == "heater" and self.test_type == "desorption":
            if value is not None:
                if not finite(value) or not 50 <= value <= min(175, self.config["max_temperature_c"] - 5):
                    raise ValueError("Choose a heater target of at least 50 °C and at least 5 °C below the high-temperature limit.")
                values = self.rig.snapshot().get("values", {})
                manual_flow = self.rig.snapshot().get("devices", {}).get("alicat", {}).get("required") is False
                if manual_flow and self.config.get("manual_flow_confirmed") is not True:
                    raise ValueError("Confirm external nitrogen flow monitoring before starting the heater.")
                if not manual_flow and (not finite(values.get("flow_sccm")) or values["flow_sccm"] <= 1):
                    raise ValueError("Establish nitrogen flow before starting the heater.")
            self.heater_target = value
            self.command_plan.append(("heater", {"target_c": value}))
        else:
            raise ValueError("That control does not belong to the selected Core test.")
        self._event("core_control", "Operator requested " + action, action=action, value=value)
        self._pump_commands()

    def step(self, dt, snapshot):
        super().step(dt, snapshot)
        outputs = snapshot.get("outputs", {})
        if not self.running or not self.stage.startswith("core_") or self.command_plan or outputs.get("pending_count", 0):
            return
        self._acquired = self._acquired or self.stage_elapsed > 0
        needs_flow = self.test_type in {"zero", "breakthrough", "desorption"} or self.air_on
        measured_flow = snapshot.get("devices", {}).get("alicat", {}).get("required") is not False
        self._flow_missing_s = self._flow_missing_s + dt if measured_flow and needs_flow and snapshot["values"]["flow_sccm"] <= 1 else 0
        if self._flow_missing_s >= self.config["stale_after_s"]:
            self.fault("Gas flow was lost during the Core test.")
            return
        if self.test_type == "desorption":
            self.waiting_for = (f"Heating under nitrogen to {self.heater_target:g} °C. End test turns heat off." if self.heater_target is not None else
                                "Nitrogen through the column; heater OFF. Choose Heat to target or 150 °C heat when ready.")
        elif self.test_type == "adsorption":
            self.waiting_for = "Air ON — column to exhaust." if self.air_on else "Air OFF — column to exhaust. Choose Air on when ready."

    def stop(self):
        if not self.running or self.stage in {"stopping", "shutdown"}:
            return
        completed = self._acquired
        duration = self.elapsed
        super().stop()
        self.operator_finished = completed
        if completed:
            self.summary.update(measurement_duration_s=duration, ended_by="operator")
