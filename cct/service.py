"""Application facade. The UI calls update(), never serial reads or SQL directly."""

import json
import shutil
import tempfile
from collections import deque
from pathlib import Path

from .contracts import CHANNELS, DEFAULT_CONFIG, default_data_root, required_channels
from .engine import ExperimentEngine, STAGES, finite, validate_config
from .core_tests import CoreProcedureEngine
from .named_tests import TESTS, normalize_test_config
from .operator import test_plan
from .simulator import SimulationRig
from .storage import RunStore, utc_now


class LabService:
    def __init__(self, data_root=None):
        self.store = RunStore(Path(data_root) if data_root is not None else default_data_root())
        self.mode = "hardware"
        self.rig = None
        self.connected = False
        self.engine = None
        self.speed = 1.0
        self.config = dict(DEFAULT_CONFIG)
        self.events = deque(maxlen=500)
        self.series = deque(maxlen=21600)
        self.live_elapsed = 0.0
        self.record_elapsed = 0.0
        self.run_id = None
        self.run_path = None
        self.outcome = ""
        self.save_status = "Not recording"
        self.last_error = ""
        self._finalized = True
        self._storage_failed = False
        for run_id in self.store.recover_interrupted():
            self._emit({"event": "recovered_run", "level": "warning", "message": f"An unfinished experiment was marked interrupted: {run_id}. Automatic resume is disabled."})

    def connect(self, mode="simulation", profile_path=None):
        if self.engine and self.engine.running:
            raise RuntimeError("Stop the current experiment before changing connections.")
        if mode not in {"simulation", "hardware"}:
            raise ValueError("Connection mode must be simulation or hardware.")
        if self.rig:
            self.rig.disconnect()
        self.connected = False
        self.store.close()
        self.mode = mode
        self.last_error = ""
        if mode == "simulation":
            self.rig = SimulationRig()
        else:
            from .drivers.rig import HardwareRig
            self.rig = HardwareRig(profile_path)
            self.speed = 1.0
        self.rig.connect()
        self.connected = True
        self.engine = None
        self.live_elapsed = 0.0
        self.series.clear()
        self._drain_rig_events()
        return self.snapshot()

    def disconnect(self):
        try:
            if self.engine and self.engine.running:
                self.engine.fault("The apparatus was disconnected by the operator.")
                self._finalize()
            if self.rig:
                self.rig.disconnect()
        except Exception as error:
            self._failure(error)
        finally:
            self.store.close()
        self.connected = False
        self._emit({"event": "disconnected", "level": "warning", "message": "Apparatus disconnected. Automatic resume is disabled."})

    def validate_setup(self, config):
        config = normalize_test_config(config)
        errors = validate_config(config)
        if not self.connected or self.rig is None:
            errors.append("Connect the apparatus with a commissioned hardware profile before starting.")
        else:
            rig_snapshot = self.rig.snapshot()
            if not rig_snapshot.get("ready", False):
                errors.extend(rig_snapshot.get("blockers") or ["Devices are not ready."])
            settings = {**DEFAULT_CONFIG, **config} if isinstance(config, dict) else DEFAULT_CONFIG
            maximum_age = settings["stale_after_s"] if finite(settings["stale_after_s"]) else DEFAULT_CONFIG["stale_after_s"]
            manual = rig_snapshot.get("manual_devices", [])
            if any(role in manual for role in ("alicat", "licor")) and (config.get("automatic_cutoff_enabled") or not config.get("core_controls")):
                errors.append("Manual Alicat or LI-COR operation supports single Core tests only. Repeating automatic cycles require measured flow and CO₂.")
            for channel in required_channels(rig_snapshot):
                label = CHANNELS[channel][0]
                value = rig_snapshot.get("values", {}).get(channel)
                age = rig_snapshot.get("ages", {}).get(channel)
                if not finite(value) or not finite(age) or age < 0 or age > maximum_age:
                    errors.append(f"Wait for a fresh {label} reading before starting.")
            if self.mode == "hardware" and not errors:
                settings = {**DEFAULT_CONFIG, **config}
                required_route = {"zero": "zero", "adsorption": "adsorption"}.get(settings.get("test_type"))
                if required_route and not rig_snapshot.get("profile", {}).get("routes", {}).get(required_route):
                    errors.append(f"Commission the {required_route.title()} test route in the hardware profile before starting this test.")
                limits = rig_snapshot.get("safety_limits", {})
                maximum = limits.get("max_temperature_c")
                minimum_target = limits.get("min_heater_target_c")
                if finite(maximum) and (settings["max_temperature_c"] > maximum or settings["desorption_target_c"] >= maximum):
                    errors.append(f"Test temperatures exceed the commissioned hardware limit of {maximum:g} °C.")
                if finite(minimum_target) and settings["desorption_target_c"] < minimum_target:
                    errors.append(f"Heater target must be at least {minimum_target:g} °C on the commissioned hardware.")
        if self.engine and self.engine.running:
            errors.append("An experiment is already running.")
        if self._storage_failed:
            errors.append("Resolve the recording error and restart the app before starting another experiment.")
        return list(dict.fromkeys(errors))

    def start(self, config):
        config = normalize_test_config(config)
        if self.mode == "hardware" and self.rig and self.rig.snapshot().get("calibrator_mode") == "manual" and config.get("manual_calibrator_confirmed") is not True:
            raise ValueError("Confirm the 146C is set to Local/manual and the required gas supplies are available before starting.")
        if self.mode == "hardware" and self.rig and "alicat" in self.rig.snapshot().get("manual_devices", []) and config.get("manual_flow_confirmed") is not True:
            raise ValueError("Confirm gas correction and continuous external flow monitoring on the manual Alicat before starting.")
        errors = self.validate_setup(config)
        if errors:
            raise ValueError("\n".join(errors))
        self.config = {**DEFAULT_CONFIG, **config}
        self.config.pop("stable_h2o_range_mmol_mol", None)
        if self.mode == "hardware" and self.rig:
            self.config["calibrator_mode"] = self.rig.snapshot().get("calibrator_mode", "automatic")
            self.config["manual_devices"] = list(self.rig.snapshot().get("manual_devices", []))
        # Reject objects that cannot survive a recipe/database round trip.
        json.dumps(self.config, allow_nan=False)
        self.engine = None
        self.outcome = ""
        self.last_error = ""
        self.record_elapsed = 0.0
        self.series.clear()
        try:
            self._finalized = False
            rig_snapshot = self.rig.snapshot()
            apparatus = {key: rig_snapshot[key] for key in ("devices", "outputs", "safety_limits", "profile", "profile_path", "instrument_identities", "calibrator_mode", "manual_devices") if key in rig_snapshot}
            metadata = self.store.create(self.config, self.mode, apparatus)
            self.run_id, self.run_path = metadata["id"], metadata["path"]
            self.save_status = "Recording — saving automatically"
            engine_type = CoreProcedureEngine if self.config.get("core_controls") and not self.config.get("automatic_cutoff_enabled") else ExperimentEngine
            self.engine = engine_type(self.rig, self.config, self._emit)
            self._emit({"event": "run_started", "level": "info", "message": f"Started {self.run_id} ({self.mode}).", "details": {"recipe": self.config}})
            self.engine.start()
            self._drain_rig_events()
            self._record()
        except Exception as error:
            self._failure(error)
            self.store.close()
            raise RuntimeError(f"Experiment could not start: {error}") from error
        return self.run_id

    def stop(self):
        if self.engine and self.engine.running:
            try:
                self.engine.stop()
            except Exception as error:
                self._failure(error)

    def core_action(self, action, value=None):
        if not isinstance(self.engine, CoreProcedureEngine):
            raise RuntimeError("Core controls are available only during a single Core test.")
        self.engine.action(action, value)
        self._drain_rig_events()

    def new_apparatus_profile(self):
        """Load a non-executable starting point; this never commissions hardware."""
        path = Path(__file__).resolve().parents[1] / "config" / "hardware.example.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        profile["calibrator_mode"] = "manual"
        profile["alicat_mode"] = "manual"
        profile["licor_mode"] = "automatic"
        return profile

    def save_apparatus_profile(self, profile):
        if self.engine and self.engine.running or self.connected and self.mode == "hardware":
            raise RuntimeError("Disconnect hardware before changing device pairing.")
        from .portable import save_apparatus_profile
        return save_apparatus_profile(self.store.root, profile)

    def import_completed_run(self, source_folder):
        from .portable import import_completed_run
        return import_completed_run(self.store.root, source_folder)

    def export_support_bundle(self):
        from .portable import export_support_bundle
        # Build a small operational snapshot on the calling thread. Never hand
        # the diagnostics exporter the measurement series or operator notes.
        snapshot = self.snapshot()
        return export_support_bundle(self.store.root, {key: snapshot.get(key) for key in
            ("mode", "connected", "running", "stage", "devices", "blockers", "outcome")})

    def add_observation(self, text):
        if not self.engine or not self.engine.running:
            raise RuntimeError("Start a test before adding a time-stamped observation.")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Enter an observation of 1–2,000 characters.")
        try:
            self._emit({"event": "operator_note", "message": text.strip(), "details": {"note": text.strip()}})
        except Exception as error:
            self._failure(error)
            raise RuntimeError("The observation could not be saved; controlled operation stopped.") from error

    def preflight_report(self, config):
        """Explicit pre-test check. Writes only a disposable local storage probe."""
        errors = self.validate_setup(config)
        checks = []
        rig = self.rig.snapshot() if self.rig else {}
        checks.append({"title": "Connection", "state": "ready" if self.connected else "attention",
                       "detail": "Practice mode — generated data, no physical control." if self.connected and self.mode == "simulation" else
                                 "USB hardware session selected." if self.connected else "Connect the apparatus first."})
        for role, title in (("core2", "Core2 + S3"), ("licor", "LI-COR analyzer"), ("alicat", "Alicat flow meter"), ("calibrator", "146C gas selector")):
            info = rig.get("devices", {}).get(role, {})
            if info.get("required") is False:
                detail = {"calibrator": "Set Local/manual at the 146C front panel and verify gas supplies.", "alicat": "No flow measurement or automatic flow-loss protection. Check gas correction and continuously monitor flow externally.", "licor": "No CO₂ measurements are available or recorded. Repeating automatic cycles are unavailable."}[role]
                checks.append({"title": title + " — manual", "state": "ready", "detail": "No USB port is opened. " + detail})
                continue
            checks.append({"title": title, "state": "ready" if info.get("connected") and info.get("fresh") else "attention",
                           "detail": "Fresh simulated data." if self.mode == "simulation" and info.get("fresh") else str(info.get("message") or "Not connected.")})
        try:
            with tempfile.TemporaryFile(dir=self.store.root) as probe:
                probe.write(b"CCT recording check")
                probe.flush()
            free = shutil.disk_usage(self.store.root).free
            if free < 1024 * 1024:
                raise OSError("Less than 1 MB free; free local disk space before starting.")
            checks.append({"title": "Automatic recording", "state": "ready", "detail": f"Local folder is writable; {free / 1024 ** 3:.1f} GB free. Full run storage is checked again at Start."})
        except OSError as error:
            message = f"Recording folder needs attention: {error}"
            errors.append(message)
            checks.append({"title": "Automatic recording", "state": "attention", "detail": message})
        valid = not validate_config(normalize_test_config(config))
        return {"ready": not errors, "checks": checks, "blockers": errors,
                "plan": test_plan(dict(config, calibrator_mode=rig.get("calibrator_mode", "automatic"))) if valid else None, "data_folder": str(self.store.root), "mode": self.mode,
                "calibrator_mode": rig.get("calibrator_mode", "automatic"), "manual_devices": rig.get("manual_devices", [])}

    def set_speed(self, value):
        if not finite(value) or not 0.1 <= value <= 600.0:
            raise ValueError("Simulation speed must be from 0.1× to 600×.")
        if self.mode == "hardware" and value != 1.0:
            raise ValueError("Physical hardware always runs in real time.")
        self.speed = float(value)

    def inject_fault(self, name):
        if self.mode != "simulation" or not self.rig:
            raise ValueError("Fault injection is available only in a connected simulation.")
        self.rig.inject_fault(name)
        self._drain_rig_events()

    def manual(self, action, **kwargs):
        if not self.connected or not self.rig:
            raise RuntimeError("Connect the apparatus first.")
        if self.engine and self.engine.running:
            raise RuntimeError("Manual controls are locked during automated experiments. Use Stop first.")
        if action in {"safe_state", "emergency_stop"}:
            if kwargs:
                raise ValueError("Safe shutdown does not accept additional settings.")
        elif action == "route":
            if kwargs.get("name") not in {"bypass", "column", "vent", "purge", "closed"} or set(kwargs) != {"name"}:
                raise ValueError("Choose bypass, column, vent, purge, or closed routing.")
        elif action == "gas":
            if kwargs.get("name") not in {"air", "n2", "off"} or set(kwargs) != {"name"}:
                raise ValueError("Choose air, n2, or off gas.")
        elif action == "heater":
            target = kwargs.get("target_c")
            if set(kwargs) != {"target_c"} or (target is not None and (not finite(target) or not 5 <= target < self.config["max_temperature_c"] - 5)):
                raise ValueError("Heater target must be below the configured temperature limit, or None for off.")
        elif action == "valve":
            names = {"exhaust", "check", "bypass", "mfm_selector", "nitrogen", "compressed_air"}
            if set(kwargs) != {"name", "on"} or kwargs.get("name") not in names or type(kwargs.get("on")) is not bool:
                raise ValueError("Choose a named valve and an on/off state.")
        else:
            raise ValueError("Unknown manual action.")
        if self.mode == "hardware" and not self.rig.snapshot().get("ready", False):
            # The driver still permits its own safe-state command if outputs need shutting down.
            if action not in {"safe_state", "emergency_stop"}:
                raise RuntimeError("Commission and connect all required devices before manual actuation.")
        self.rig.execute(action, **kwargs)
        self._emit({"event": "manual", "level": "warning", "message": f"Manual {action}", "details": kwargs})
        self._drain_rig_events()

    def _emit(self, event):
        stamped = dict(event)
        stamped.setdefault("timestamp_utc", utc_now())
        stamped.setdefault("elapsed_s", round(self.engine.elapsed, 6) if self.engine else 0.0)
        stamped.setdefault("cycle", self.engine.cycle if self.engine else 0)
        stamped.setdefault("stage", self.engine.stage if self.engine else "idle")
        stamped.setdefault("level", "info")
        stamped.setdefault("event", stamped.pop("kind", "device"))
        stamped.setdefault("message", stamped["event"].replace("_", " "))
        if "details" not in stamped:
            stamped["details"] = {key: value for key, value in stamped.items() if key not in {"timestamp_utc", "elapsed_s", "cycle", "stage", "level", "event", "message"}}
        # Native frames belong in the durable record, not the operator activity
        # feed: otherwise a few seconds of telemetry hide every phase change.
        if stamped["event"] != "raw_frame":
            self.events.append(stamped)
        if not self._storage_failed and not self._finalized:
            self.store.append_event(stamped)

    def _drain_rig_events(self):
        if not self.rig:
            return
        for event in self.rig.drain_events():
            self._emit(event)
            kind = event.get("event", event.get("kind", ""))
            if kind == "command_error" and self.engine and self.engine.running:
                self.engine.fault(event.get("message", "An instrument command failed."))

    def update(self, dt):
        if not finite(dt) or dt < 0:
            raise ValueError("Update duration must be a finite, nonnegative number of seconds.")
        if not self.connected or not self.rig or dt == 0:
            return
        remaining = min(float(dt), 5.0) * (self.speed if self.mode == "simulation" else 1.0)
        try:
            # A UI delay must never manufacture a long uninterrupted hardware dwell.
            if self.mode == "hardware" and dt > self.config["stale_after_s"] and self.engine and self.engine.running:
                self.engine.fault("The app update loop stalled beyond the allowed reading age.")
                self._record()
                self._finalize()
                remaining = min(remaining, 1.0)
            while remaining > 0.000001:
                step = min(1.0, remaining)
                stage = self.engine.stage if self.engine else "idle"
                self.rig.tick(step, stage)
                self.live_elapsed += step
                self._drain_rig_events()
                if self.engine and not self.engine.running and not self._finalized:
                    self._record()
                    self._finalize()
                if self.engine and self.engine.running:
                    self.engine.step(step, self.rig.snapshot())
                    self.record_elapsed += step
                    if self.record_elapsed >= 1.0:
                        self._record()
                        self.record_elapsed %= 1.0
                    if not self.engine.running:
                        self._record()
                        self._finalize()
                elif self.engine is None:
                    self.record_elapsed += step
                    if self.record_elapsed >= 1.0:
                        self._record(record=False)
                        self.record_elapsed %= 1.0
                remaining -= step
        except Exception as error:
            self._failure(error)

    def _row(self):
        snapshot = self.rig.snapshot() if self.rig else {"values": {}, "ages": {}, "outputs": {}}
        raw = {key: value for key, value in snapshot.get("values", {}).items() if key in CHANNELS}
        ages = {key: value for key, value in snapshot.get("ages", {}).items() if key in CHANNELS}
        values, quality = {}, {}
        for channel in CHANNELS:
            valid = finite(raw.get(channel)) and finite(ages.get(channel)) and ages[channel] <= self.config["stale_after_s"]
            values[channel] = raw[channel] if valid else None
            quality[channel] = "valid" if valid else "missing_or_stale"
            if snapshot.get("devices", {}).get(CHANNELS[channel][2], {}).get("required") is False:
                values[channel] = raw[channel] = None
                quality[channel] = "manual_not_recorded"
        engine = self.engine
        return {"timestamp_utc": utc_now(), "mode": self.mode,
                "test_type": self.config.get("test_type", "legacy_breakthrough"),
                "run_id": self.run_id, "notebook_id": self.config["notebook_id"],
                "sample_id": self.config["sample_id"],
                "elapsed_s": round(engine.elapsed if engine else self.live_elapsed, 6),
                "stage_elapsed_s": round(engine.stage_elapsed, 6) if engine else 0.0,
                "stage": engine.stage if engine else "idle", "cycle": engine.cycle if engine else 0,
                **values, "baseline_ppm": engine.baseline if engine else None, "breakthrough_ratio": engine.ratio if engine else None,
                "threshold_ratio": engine.threshold if engine else None, "quality": quality, "ages": ages,
                "outputs": dict(snapshot.get("outputs", {})), "raw": {key: value if finite(value) else None for key, value in raw.items()}}

    def _record(self, record=True):
        row = self._row()
        if self.series and self.series[-1]["elapsed_s"] == row["elapsed_s"] and self.series[-1]["stage"] == row["stage"]:
            return
        self.series.append(row)
        if record and not self._finalized:
            self.store.append_sample(row)

    def _finalize(self):
        if self._finalized or not self.engine:
            return
        self.outcome = self.engine.outcome
        self.store.finalize(self.outcome or "interrupted", self.engine.cycles_completed, self.engine.reason, self.engine.summary)
        self._finalized = True
        self.save_status = "Saved — test record complete"
        try:
            self.store.export(self.run_id)
            self.save_status = "Saved — data and CSV exports"
        except Exception as error:
            self.save_status = "Database saved; CSV export needs retry"
            self._emit({"event": "export_error", "level": "warning", "message": f"The database is saved; CSV export failed: {error}"})

    def _failure(self, error):
        self.last_error = str(error)
        self._storage_failed = True
        self.save_status = f"Recording / control error: {error}"
        if self.engine and self.engine.running:
            self.engine.fault(f"Recording or control error: {error}")
        elif self.rig:
            try:
                self.rig.execute("emergency_stop")
            except Exception:
                pass
        if self.engine:
            self.outcome = self.engine.outcome or "fault"
        self.events.append({"timestamp_utc": utc_now(), "elapsed_s": self.engine.elapsed if self.engine else 0,
                            "stage": "fault", "cycle": self.engine.cycle if self.engine else 0,
                            "event": "recording_error", "level": "error", "message": str(error)})
        try:
            if not self._finalized:
                self.store.finalize("fault", self.engine.cycles_completed if self.engine else 0, str(error), self.engine.summary if self.engine else {})
                self._finalized = True
        except Exception:
            # Existing committed rows remain recoverable. Do not report that a failed write was saved.
            pass

    def snapshot(self):
        rig = self.rig.snapshot() if self.rig else {"values": {}, "ages": {}, "devices": {}, "outputs": {}, "blockers": ["Connect devices or start simulation."]}
        engine = self.engine
        stage = engine.stage if engine else "idle"
        blockers = list(rig.get("blockers", []))
        if self.last_error:
            blockers.append(self.last_error)
        warnings = list(rig.get("warnings", []))
        if self.mode == "simulation":
            warnings.append("Simulation only — no physical equipment is being controlled.")
        if engine and engine.stage == "fault":
            warnings.append("Fault shutdown was requested. Confirm the physical apparatus state before restarting.")
        temperature = rig.get("values", {}).get("column_temp_c")
        if not (engine and engine.running) and finite(temperature) and temperature > self.config["adsorption_temp_c"]:
            warnings.append(f"Column still hot ({temperature:g} °C). Test completion does not mean it has cooled; keep the physical vent path safe.")
        row = self._row() if self.rig else {"values": {}}
        return {"mode": self.mode, "connected": self.connected, "running": bool(engine and engine.running),
                "stage": stage, "stage_label": STAGES.get(stage, (stage, ""))[0],
                "stage_description": STAGES.get(stage, (stage, ""))[1],
                "waiting_for": engine.waiting_for if engine else "Connect and configure the experiment.",
                "cycle": engine.cycle if engine else 0, "total_cycles": self.config["cycles"],
                "elapsed_s": engine.elapsed if engine else 0.0, "stage_elapsed_s": engine.stage_elapsed if engine else 0.0,
                "stage_progress": engine.stage_progress if engine else None,
                "values": {channel: row.get(channel) for channel in CHANNELS}, "ages": dict(rig.get("ages", {})),
                "devices": rig.get("devices", {}), "outputs": rig.get("outputs", {}),
                "baseline_ppm": engine.baseline if engine else None, "breakthrough_ratio": engine.ratio if engine else None,
                "threshold_ratio": engine.threshold if engine else None,
                "test_type": self.config.get("test_type", "breakthrough"),
                "test_label": TESTS.get(self.config.get("test_type", "breakthrough"), ("Breakthrough",))[0],
                "automatic_cutoff_enabled": bool(engine and engine.threshold is not None),
                "core_controls": engine.controls() if isinstance(engine, CoreProcedureEngine) else {},
                "calibrator_mode": rig.get("calibrator_mode", "automatic"),
                "manual_devices": rig.get("manual_devices", []),
                "run_id": self.run_id, "run_path": self.run_path, "outcome": engine.outcome if engine else self.outcome,
                "save_status": self.save_status, "blockers": blockers, "warnings": warnings,
                "events": list(self.events)[-100:], "series": list(self.series)}

    def save_recipe(self, name, config):
        errors = validate_config(config)
        if errors:
            raise ValueError("\n".join(errors))
        self.store.save_recipe(name, {**DEFAULT_CONFIG, **config})

    def list_recipes(self):
        return self.store.list_recipes()

    def list_runs(self):
        return self.store.list_runs()

    def load_run(self, run_id):
        return self.store.load(run_id)

    def export_run(self, run_id):
        return self.store.export(run_id)
