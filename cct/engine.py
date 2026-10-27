"""Recipe state machine independent of widgets, storage, and serial drivers."""

import math
import statistics
from collections import deque

from .contracts import CHANNELS, DEFAULT_CONFIG, required_channels
from .named_tests import TESTS


STAGES = {
    "idle": ("Ready", "Choose a test and connect the apparatus."),
    "warmup": ("Analyzer warmup", "Waiting for the analyzer to remain within its temperature band."),
    "initial_baseline": ("Initial nitrogen baseline", "Qualifying near-zero CO₂ under nitrogen before initial desorption."),
    "initial_heat": ("Initial heating", "Heating the column for initial desorption."),
    "initial_release": ("Initial treatment", "Purging with nitrogen until CO₂ returns near zero."),
    "cooldown": ("Column cooldown", "Cooling the column to the adsorption starting temperature."),
    "stabilize": ("Bypass stabilization", "Establishing stable inlet CO₂ and flow."),
    "baseline": ("Inlet reference", "Recording the inlet CO₂ reference while gas bypasses the column."),
    "breakthrough": ("Measuring column outlet", "Recording CO₂ leaving the column."),
    "zero": ("Zero check", "Nitrogen bypasses the column. Readings are recorded until you end the test."),
    "adsorption": ("Adsorption", "Air flows through the column to exhaust. The analyzer is not measuring the column outlet."),
    "desorption": ("Desorption", "Holding the column temperature under nitrogen. End the test when ready."),
    "depressurize": ("Depressurization", "Feed is off; the configured vent route is active."),
    "purge_heat": ("Regeneration heating", "Heating the column using the selected nitrogen policy."),
    "purge_release": ("Regeneration purge", "Nitrogen carries desorbed CO₂ to the analyzer."),
    "final_cooldown": ("Final cooldown", "Cooling before completing the experiment."),
    "stopping": ("Controlled stop", "Confirming feed and heat off, then holding the vent route for 10 seconds. The column may remain hot."),
    "shutdown": ("Confirming shutdown", "Waiting for feed-off, heater-off and vent acknowledgements."),
    "complete": ("Completed", "The test has finished and shutdown has been confirmed."),
    "aborted": ("Stopped", "The operator stopped this experiment."),
    "fault": ("Fault — run stopped", "Automatic progression stopped. Review the event log before restarting."),
}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_config(config):
    if not isinstance(config, dict):
        return ["Experiment settings must be a dictionary."]
    merged = {**DEFAULT_CONFIG, **config}
    errors = []
    named = "test_type" in merged
    automatic = merged.get("automatic_cutoff_enabled", not named)
    if not named and automatic is False:
        errors.append("Choose a named test to disable automatic breakthrough cutoff.")
    if named and (not isinstance(merged["test_type"], str) or merged["test_type"] not in TESTS):
        errors.append("Choose Zero, Adsorption, Breakthrough, or Desorption.")
    if not isinstance(automatic, bool):
        errors.append("Automatic cutoff must be enabled or disabled.")
    if "core_controls" in merged and not isinstance(merged["core_controls"], bool):
        errors.append("Core controls must be enabled or disabled.")
    if named and automatic and merged["test_type"] != "breakthrough":
        errors.append("Automatic breakthrough cycles are available only for the Breakthrough test.")
    for key in ("notebook_id", "sample_id"):
        value = merged[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            errors.append(f"{key.replace('_', ' ').title()} must contain 1–100 characters.")
        elif any(char in value for char in "<>:\"/\\|?*\x00") or any(ord(char) < 32 for char in value):
            errors.append(f"{key.replace('_', ' ').title()} contains characters that cannot be used in experiment names.")
    for key in ("operator", "notes", "recipe_name"):
        if not isinstance(merged[key], str) or len(merged[key]) > 20000:
            errors.append(f"{key.replace('_', ' ').title()} must be text of at most 20,000 characters.")
    if not isinstance(merged["cycles"], int) or isinstance(merged["cycles"], bool) or not 1 <= merged["cycles"] <= 1000:
        errors.append("Cycle count must be a whole number from 1 to 1,000.")
    bounds = {
        "sample_mass_g": (0.000001, 100000), "breakthrough_pct": (0.01, 100),
        "analyzer_target_c": (0, 100), "analyzer_tolerance_c": (0.05, 10), "analyzer_dwell_s": (1, 3600),
        "adsorption_temp_c": (5, 100), "desorption_target_c": (10, 175), "max_temperature_c": (20, 200),
        "stable_window_s": (5, 3600), "stable_co2_range_ppm": (0.01, 1000),
        "stable_flow_range_sccm": (0.01, 10000),
        "baseline_s": (5, 36000), "breakthrough_hold_s": (1, 3600), "breakthrough_guard_s": (0, 3600),
        "near_zero_ppm": (0.01, 1000), "release_dwell_s": (1, 3600), "min_release_s": (0, 36000),
        "depressurize_s": (1, 3600), "shutdown_hold_s": (10, 60), "stage_timeout_s": (30, 86400), "stale_after_s": (1, 60),
    }
    for key, (minimum, maximum) in bounds.items():
        if key == "breakthrough_pct" and not automatic:
            continue
        value = merged[key]
        if not finite(value) or not minimum <= value <= maximum:
            errors.append(f"{key.replace('_', ' ').title()} must be a finite number from {minimum:g} to {maximum:g}.")
    if merged["initial_treatment"] not in ("none", "purge", "desorption"):
        errors.append("Initial treatment must be none, purge, or desorption.")
    for key in ("final_purge", "purge_heat_without_n2"):
        if not isinstance(merged[key], bool):
            errors.append(f"{key.replace('_', ' ').title()} must be enabled or disabled.")
    if errors:
        return errors
    if merged["desorption_target_c"] <= merged["adsorption_temp_c"]:
        errors.append("Desorption temperature must be above the adsorption starting temperature.")
    if merged["max_temperature_c"] < merged["desorption_target_c"] + 5:
        errors.append("Maximum temperature must be at least 5 °C above the desorption target.")
    if merged["baseline_s"] < merged["stable_window_s"]:
        errors.append("Inlet reference duration must be at least as long as the stability window.")
    longest = max(merged["baseline_s"], merged["stable_window_s"], merged["analyzer_dwell_s"],
                  merged["depressurize_s"], merged["min_release_s"] + merged["release_dwell_s"],
                  merged["breakthrough_guard_s"] + merged["breakthrough_hold_s"])
    if merged["stage_timeout_s"] <= longest:
        errors.append("Stage timeout must be longer than all configured measurement and dwell periods.")
    return errors


class ExperimentEngine:
    def __init__(self, rig, config, emit):
        self.rig, self.config, self.emit = rig, dict(config), emit
        self.elapsed = 0.0
        self.stage_elapsed = 0.0
        self.stage_wall_elapsed = 0.0
        self.stage = "idle"
        self.cycle = 1
        self.cycles_completed = 0
        self.running = False
        self.outcome = ""
        self.reason = ""
        self.baseline = None
        self.ratio = None
        self.test_type = config.get("test_type")
        self.single_test = bool(self.test_type) and not config.get("automatic_cutoff_enabled", False)
        self.threshold = None if self.single_test else config["breakthrough_pct"] / 100.0
        self.history = deque()
        self.baseline_values = []
        self.dwell = 0.0
        self.crossing_dwell = 0.0
        self.crossing_started = None
        self.last_observation = {}
        self.early_logged = False
        self.release_peak = 0.0
        self.no_flow_dwell = 0.0
        self.summary = {"cycles": [], "test_type": self.test_type or "legacy_breakthrough"}
        self.waiting_for = ""
        self.stage_progress = None
        self.finish_status = "complete"
        self.operator_finished = False
        self.command_plan = deque()

    def start(self):
        self.running = True
        self._enter("warmup")

    def _event(self, event, message, level="info", **details):
        self.emit({"event": event, "message": message, "level": level, "details": details})

    def _command(self, action, **kwargs):
        self.rig.execute(action, **kwargs)

    def _pump_commands(self):
        """A stage's heater may start only after preceding gas/routing ACKs."""
        while self.command_plan:
            outputs = self.rig.snapshot().get("outputs", {})
            if outputs.get("command_fault") or outputs.get("pending_count", 0):
                return
            action, arguments = self.command_plan.popleft()
            self._command(action, **arguments)

    def _enter(self, stage):
        self.stage = stage
        self.stage_elapsed = self.stage_wall_elapsed = self.dwell = self.crossing_dwell = 0.0
        self.crossing_started = None
        self.stage_progress = None
        self.command_plan.clear()
        self.history.clear()
        self.baseline_values.clear()
        self.release_peak = 0.0
        self.no_flow_dwell = 0.0
        self.waiting_for = STAGES[stage][1]
        self._event("stage", STAGES[stage][0], stage=stage, cycle=self.cycle)
        if stage == "warmup":
            self.command_plan.append(("safe_state", {}))
        elif stage == "initial_baseline":
            self.command_plan.extend([("heater", {"target_c": None}), ("gas", {"name": "off"}), ("route", {"name": "purge"}), ("gas", {"name": "n2"})])
        elif stage in {"initial_heat", "purge_heat"}:
            initial_desorption = stage == "initial_heat" and (self.config["initial_treatment"] == "desorption" or self.test_type == "desorption")
            gas = "n2" if initial_desorption or not self.config["purge_heat_without_n2"] else "off"
            self.command_plan.extend([("gas", {"name": "off"}), ("route", {"name": "purge"}), ("gas", {"name": gas}), ("heater", {"target_c": self.config["desorption_target_c"]})])
        elif stage in {"initial_release", "purge_release"}:
            self.command_plan.extend([("gas", {"name": "off"}), ("route", {"name": "purge"}), ("gas", {"name": "n2"})])
        elif stage in {"cooldown", "final_cooldown"}:
            gas = "off" if stage == "final_cooldown" and not self.config["final_purge"] else "n2"
            route = "vent" if stage == "final_cooldown" and not self.config["final_purge"] else "purge"
            self.command_plan.extend([("heater", {"target_c": None}), ("gas", {"name": "off"}), ("route", {"name": route}), ("gas", {"name": gas})])
        elif stage == "stabilize":
            self.baseline = self.ratio = None
            self.command_plan.extend([("heater", {"target_c": None}), ("gas", {"name": "off"}), ("route", {"name": "bypass"}), ("gas", {"name": "air"})])
        elif stage == "breakthrough":
            self.early_logged = False
            self.command_plan.append(("route", {"name": "column"}))
        elif stage in {"zero", "adsorption", "desorption"}:
            route = "purge" if stage == "desorption" else stage
            gas = "air" if stage == "adsorption" else "n2"
            self.command_plan.extend([("gas", {"name": "off"}), ("route", {"name": route}), ("gas", {"name": gas})])
            if stage != "desorption":
                self.command_plan.append(("heater", {"target_c": None}))
        elif stage in {"stopping", "shutdown"}:
            # A stop supersedes a partially completed stage command plan.
            self._command("safe_state")
        elif stage == "depressurize":
            self.command_plan.append(("safe_state", {}))
        self._pump_commands()

    def stop(self):
        if self.running and self.stage not in {"stopping", "shutdown"}:
            outputs = self.rig.snapshot().get("outputs", {})
            self.operator_finished = (self.single_test and self.stage in {"zero", "adsorption", "breakthrough", "desorption"}
                                      and self.stage_elapsed > 0 and not self.command_plan
                                      and not outputs.get("pending_count", 0) and not outputs.get("command_fault"))
            if self.operator_finished:
                self.summary["measurement_duration_s"] = self.stage_elapsed
                self.summary["ended_by"] = "operator"
                if self.summary["cycles"]:
                    self.summary["cycles"][-1].update(status="ended_by_operator", measurement_duration_s=self.stage_elapsed)
            self._event("stop_requested", "Operator ended the test; controlled shutdown requested.", "warning")
            self._enter("stopping")

    def fault(self, message):
        if not self.running:
            return
        self.reason = str(message)
        self._event("fault", self.reason, "error")
        try:
            self._command("emergency_stop")
        except Exception as error:
            self._event("shutdown_error", f"Shutdown command could not be queued: {error}", "error")
        self.running = False
        self.stage, self.outcome = "fault", "fault"
        self.waiting_for = self.reason
        self.stage_progress = None

    def _finish(self, status):
        self.running = False
        self.stage = status
        self.outcome = status
        self.stage_progress = 1.0
        self.waiting_for = STAGES[status][1]
        self._event("run_finished", self.waiting_for, "info" if status == "complete" else "warning")

    def _stable(self):
        window = self.config["stable_window_s"]
        if len(self.history) < 2 or self.history[-1][0] - self.history[0][0] < window - 0.01:
            return False
        checks = (("co2_ppm", "stable_co2_range_ppm"), ("flow_sccm", "stable_flow_range_sccm"))
        for channel, setting in checks:
            values = [row[1][channel] for row in self.history]
            observation_times = [row[2][channel] for row in self.history]
            if max(observation_times) - min(observation_times) < window - min(1.5, window * 0.1):
                return False
            tolerance = self.config[setting]
            if max(values) - min(values) > tolerance:
                return False
            n = max(1, len(values) // 3)
            if abs(statistics.mean(values[-n:]) - statistics.mean(values[:n])) > tolerance * 0.5:
                return False
        return statistics.mean(row[1]["flow_sccm"] for row in self.history) > 1.0 and statistics.mean(row[1]["co2_ppm"] for row in self.history) > max(1.0, self.config["near_zero_ppm"])

    def step(self, dt, snapshot):
        if not self.running:
            return
        self.elapsed += dt
        self.stage_wall_elapsed += dt
        values, ages = snapshot.get("values", {}), snapshot.get("ages", {})
        outputs = snapshot.get("outputs", {})
        if outputs.get("command_fault"):
            self.fault(f"Instrument command failed: {outputs['command_fault']}")
            return
        if outputs.get("s3_online") is False:
            self.fault("Core2–S3 output link/readback was lost. Shutdown requested; confirm physical valve state.")
            return
        temperature = values.get("column_temp_c")
        if finite(temperature) and temperature >= self.config["max_temperature_c"]:
            self.fault(f"Column temperature reached the {self.config['max_temperature_c']:g} °C limit.")
            return
        if self.stage_wall_elapsed >= self.config["stage_timeout_s"]:
            self.fault(f"{STAGES[self.stage][0]} exceeded its {self.config['stage_timeout_s']:g} second timeout.")
            return
        channels = required_channels(snapshot)
        for channel in channels:
            age = ages.get(channel)
            if not finite(values.get(channel)) or not finite(age) or age > self.config["stale_after_s"]:
                self.fault(f"Missing or stale {CHANNELS[channel][0]} reading. Automatic progression stopped.")
                return
        for device, state in snapshot.get("devices", {}).items():
            if state.get("required") is False:
                continue
            if not state.get("connected", False):
                self.fault(f"{device} disconnected during the experiment.")
                return
        self._pump_commands()
        outputs = self.rig.snapshot().get("outputs", {})
        if outputs.get("command_fault"):
            self.fault(f"Instrument command failed: {outputs['command_fault']}")
            return
        if self.command_plan or outputs.get("pending_count", 0):
            self.stage_elapsed = self.dwell = self.crossing_dwell = 0.0
            self.history.clear()
            self.baseline_values.clear()
            self.waiting_for = "Waiting for instrument command acknowledgements."
            self.stage_progress = None
            return
        self.stage_elapsed += dt
        observation_times = {channel: self.elapsed - ages[channel] for channel in channels}
        fresh = {channel: observation_times[channel] > self.last_observation.get(channel, -math.inf) + 0.05 for channel in channels}
        for channel in channels:
            if fresh[channel]:
                self.last_observation[channel] = observation_times[channel]
        nitrogen_heating = self.stage == "initial_heat" and (self.test_type == "desorption" or self.config["initial_treatment"] == "desorption" or not self.config["purge_heat_without_n2"])
        if self.stage in {"zero", "desorption", "initial_baseline", "initial_release", "purge_release", "cooldown"} or nitrogen_heating:
            self.no_flow_dwell = self.no_flow_dwell + dt if values["flow_sccm"] <= 1.0 else 0.0
            if self.no_flow_dwell >= self.config["stale_after_s"]:
                self.fault("Nitrogen flow was lost during treatment or cooling.")
                return
        self.ratio = values["co2_ppm"] / self.baseline if self.baseline else None
        if fresh.get("co2_ppm", False):
            self.history.append((self.stage_elapsed, dict(values), observation_times))
        while len(self.history) > 1 and self.stage_elapsed - self.history[1][0] >= self.config["stable_window_s"]:
            self.history.popleft()
        stage = self.stage
        if stage == "warmup":
            in_band = abs(values["analyzer_temp_c"] - self.config["analyzer_target_c"]) <= self.config["analyzer_tolerance_c"]
            self.dwell = self.dwell + dt if in_band else 0.0
            self.stage_progress = min(1.0, self.dwell / self.config["analyzer_dwell_s"])
            self.waiting_for = f"Analyzer within ±{self.config['analyzer_tolerance_c']:g} °C for {self.config['analyzer_dwell_s']:g} seconds."
            if self.dwell >= self.config["analyzer_dwell_s"] and fresh["analyzer_temp_c"]:
                if self.single_test and self.test_type == "zero":
                    self._enter("zero")
                elif self.single_test and self.test_type == "desorption":
                    self._enter("initial_baseline")
                else:
                    treatment = self.config["initial_treatment"]
                    self._enter("initial_baseline" if treatment == "desorption" else "initial_heat" if treatment == "purge" else "cooldown")
        elif stage in {"zero", "adsorption", "desorption"}:
            self.stage_progress = None
            self.waiting_for = STAGES[stage][1] + " Choose End test when finished."
            if stage == "adsorption" and values["flow_sccm"] <= 1.0:
                self.fault("Air flow was lost during adsorption.")
                return
        elif stage == "initial_baseline":
            qualified = abs(values["co2_ppm"]) <= self.config["near_zero_ppm"] and values["flow_sccm"] > 1.0
            self.dwell = self.dwell + dt if qualified else 0.0
            self.waiting_for = f"Nitrogen CO₂ ≤ {self.config['near_zero_ppm']:g} ppm for {self.config['stable_window_s']:g} seconds."
            self.stage_progress = min(1.0, self.dwell / self.config["stable_window_s"])
            if self.dwell >= self.config["stable_window_s"] and fresh["co2_ppm"]:
                self._event("initial_nitrogen_baseline", "Initial nitrogen baseline qualified before heating.")
                self._enter("initial_heat")
        elif stage in {"initial_heat", "purge_heat"}:
            self.stage_progress = min(1.0, max(0.0, (temperature - self.config["adsorption_temp_c"]) / (self.config["desorption_target_c"] - self.config["adsorption_temp_c"])))
            self.waiting_for = f"Column reaching {self.config['desorption_target_c']:g} °C."
            if temperature >= self.config["desorption_target_c"] - 1.0:
                self._enter("desorption" if self.single_test and self.test_type == "desorption" else "initial_release" if stage == "initial_heat" else "purge_release")
        elif stage in {"initial_release", "purge_release"}:
            self.release_peak = max(self.release_peak, values["co2_ppm"])
            near_zero = abs(values["co2_ppm"]) <= self.config["near_zero_ppm"] and values["flow_sccm"] > 1.0
            self.dwell = self.dwell + dt if near_zero and self.stage_elapsed >= self.config["min_release_s"] else 0.0
            self.stage_progress = min(1.0, self.dwell / self.config["release_dwell_s"])
            self.waiting_for = f"CO₂ ≤ {self.config['near_zero_ppm']:g} ppm for {self.config['release_dwell_s']:g} seconds, after the minimum purge period."
            if self.dwell >= self.config["release_dwell_s"] and fresh["co2_ppm"]:
                if self.release_peak <= self.config["near_zero_ppm"]:
                    self._event("no_desorption_peak", "No measurable desorption peak was observed; the purge baseline criterion was met.", "warning")
                self._enter("final_cooldown" if stage == "purge_release" and self.cycles_completed >= self.config["cycles"] else "cooldown")
        elif stage in {"cooldown", "final_cooldown"}:
            self.waiting_for = f"Column ≤ {self.config['adsorption_temp_c']:g} °C."
            if temperature <= self.config["adsorption_temp_c"]:
                if stage == "final_cooldown":
                    self.finish_status = "complete"
                    self._enter("shutdown")
                else:
                    self.cycle = self.cycles_completed + 1
                    self._enter("adsorption" if self.single_test and self.test_type == "adsorption" else "stabilize")
        elif stage == "stabilize":
            self.waiting_for = f"Stable CO₂ and nonzero flow for {self.config['stable_window_s']:g} seconds."
            self.stage_progress = min(1.0, self.stage_elapsed / self.config["stable_window_s"])
            if fresh["co2_ppm"] and self._stable():
                self._enter("baseline")
        elif stage == "baseline":
            full_window = len(self.history) > 1 and self.history[-1][0] - self.history[0][0] >= self.config["stable_window_s"]
            if not self._stable() and full_window:
                self._event("baseline_unstable", "Inlet changed while recording its reference; returning to stabilization.", "warning")
                self._enter("stabilize")
                return
            if fresh["co2_ppm"]:
                self.baseline_values.append(values["co2_ppm"])
            self.stage_progress = min(1.0, self.stage_elapsed / self.config["baseline_s"])
            self.waiting_for = f"Recording inlet reference: {self.stage_elapsed:.0f} / {self.config['baseline_s']:g} seconds."
            if self.stage_elapsed >= self.config["baseline_s"] and fresh["co2_ppm"] and self._stable():
                self.baseline = statistics.mean(self.baseline_values)
                self.summary["cycles"].append({"cycle": self.cycle, "inlet_co2_ppm": self.baseline, "inlet_std_ppm": statistics.pstdev(self.baseline_values), "threshold_ratio": self.threshold, "status": "measuring"})
                self._event("inlet_reference", f"Cycle {self.cycle} inlet reference: {self.baseline:.3f} ppm.", baseline_ppm=self.baseline)
                self._enter("breakthrough")
        elif stage == "breakthrough":
            if self.threshold is None:
                self.waiting_for = "Recording CO₂ leaving the column. Choose End test when finished."
                self.stage_progress = None
                if values["flow_sccm"] <= 1.0:
                    self.fault("Flow was lost during breakthrough measurement.")
                return
            above = self.ratio is not None and self.ratio >= self.threshold
            if above and self.stage_elapsed < self.config["breakthrough_guard_s"]:
                if not self.early_logged:
                    self._event("early_crossing", "Breakthrough crossed the threshold during the configured guard period. Data remain recorded.", "warning", ratio=self.ratio)
                    self.early_logged = True
            if fresh["co2_ppm"]:
                if above and self.stage_elapsed >= self.config["breakthrough_guard_s"]:
                    if self.crossing_started is None:
                        self.crossing_started = self.elapsed
                else:
                    self.crossing_started = None
            self.crossing_dwell = self.elapsed - self.crossing_started if self.crossing_started is not None else 0.0
            self.waiting_for = f"Outlet / inlet ≥ {self.threshold:.1%} for {self.config['breakthrough_hold_s']:g} seconds."
            self.stage_progress = min(1.0, self.crossing_dwell / self.config["breakthrough_hold_s"])
            if values["flow_sccm"] <= 1.0:
                self.fault("Flow was lost during breakthrough measurement.")
                return
            if self.crossing_dwell >= self.config["breakthrough_hold_s"] and fresh["co2_ppm"]:
                self.cycles_completed += 1
                self.summary["cycles"][-1].update(breakthrough_elapsed_s=self.elapsed, breakthrough_time_s=self.stage_elapsed - self.config["breakthrough_hold_s"], status="complete")
                self._event("breakthrough", f"Cycle {self.cycle} reached the sustained breakthrough threshold.", ratio=self.ratio)
                self._enter("depressurize")
        elif stage == "depressurize":
            self.waiting_for = f"Venting: {self.stage_elapsed:.0f} / {self.config['depressurize_s']:g} seconds."
            self.stage_progress = min(1.0, self.stage_elapsed / self.config["depressurize_s"])
            if self.stage_elapsed >= self.config["depressurize_s"]:
                if self.cycles_completed < self.config["cycles"] or self.config["final_purge"]:
                    self._enter("purge_heat")
                else:
                    self._enter("final_cooldown")
        elif stage == "stopping":
            hold = self.config.get("shutdown_hold_s", 10.0)
            self.waiting_for = f"Feed and heat off acknowledged. Vent hold: {max(0, hold-self.stage_elapsed):.0f} seconds left. The column may remain hot."
            if self.stage_elapsed >= hold:
                self.summary.update(column_temperature_at_end_c=temperature, cooling_required=temperature > self.config["adsorption_temp_c"])
                self.reason = "Ended by the operator." if self.operator_finished else "Stopped by the operator before completion."
                self.finish_status = "complete" if self.operator_finished else "aborted"
                if self.operator_finished:
                    self.cycles_completed = 1
                self._enter("shutdown")
        elif stage == "shutdown":
            self._finish(self.finish_status)
