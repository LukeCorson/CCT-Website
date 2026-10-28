"""Thread-safe adapter between HTTP requests and the synchronous LabService."""

import threading
import time
from collections import deque

from cct.contracts import DEFAULT_CONFIG
from cct.drivers.s3_usb import ALLOWED_VALVES, S3UsbLink
from cct.service import LabService
from .naming import build_test_name


def _plain(value):
    if isinstance(value, deque):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class WebController:
    def __init__(self, service=None, s3_factory=None):
        self.service = service or LabService()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._last_tick = time.monotonic()
        self._s3 = None
        self._s3_factory = s3_factory or S3UsbLink
        self._connection_kind = "service"

    def start_loop(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_tick = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="cct-web-acquisition", daemon=True)
        self._thread.start()

    def close(self):
        with self._lock:
            if self._s3:
                self._s3.close()
                self._s3 = None
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.wait(0.25):
            now = time.monotonic()
            with self._lock:
                self.service.update(max(0, now - self._last_tick))
            self._last_tick = now

    def snapshot(self):
        with self._lock:
            if self._s3 is not None:
                device = self._s3.snapshot()
                return {"connected": True, "running": False, "mode": "hardware",
                        "controller_target": "direct_s3", "direct_s3_commissioned": True,
                        "devices": {"s3": device}, "values": {}, "ages": {}, "series": [],
                        "events": self._s3.drain_events(),
                        "outputs": {"valves": device.get("applied_valves")},
                        "blockers": ["Automated tests require instrument readings, physical valve names, and temperature control."],
                        "save_status": "Direct S3 manual control — not recording",
                        "stage": "manual", "stage_label": "Direct S3 manual control",
                        "waiting_for": "Valve 1–6 are available. Heating is not implemented by the attached firmware.",
                        "direct_s3_notice": "Direct S3 USB controls six on/off relays. No heating or temperature feedback is available."}
            snapshot = _plain(self.service.snapshot())
        devices = snapshot.get("devices") or {}
        if "core2" in devices:
            devices["s3"] = devices.pop("core2")
        snapshot["devices"] = devices
        snapshot["controller_target"] = "simulation" if snapshot.get("mode") == "simulation" else "legacy_core2"
        snapshot["direct_s3_commissioned"] = False
        snapshot["direct_s3_notice"] = (
            "Choose Direct S3 USB and select the controller port, or use Simulator."
            if not snapshot.get("connected") else
            "Simulation only — connect Direct S3 USB to control the six relay outputs."
            if snapshot.get("mode") == "simulation" else
            "This legacy connection is not selectable in the direct-S3 web interface."
        )
        return snapshot

    def connect(self, mode, profile_path=None, port=None):
        with self._lock:
            if mode == "direct_s3":
                if self.service.connected:
                    self.service.disconnect()
                if self._s3:
                    self._s3.close()
                self._s3 = self._s3_factory(port)
                self._s3.connect()
                self._connection_kind = "direct_s3"
                return self.snapshot()
            if self._s3:
                self._s3.close()
                self._s3 = None
            self._connection_kind = "service"
            return _plain(self.service.connect(mode=mode, profile_path=profile_path or None))

    def disconnect(self):
        with self._lock:
            if self._s3:
                self._s3.close()
                self._s3 = None
                return
            self.service.disconnect()

    def start_test(self, config):
        if self._s3 is not None:
            raise RuntimeError("The attached S3 firmware provides manual Valve 1–6 control only; automated tests are locked.")
        settings = {**DEFAULT_CONFIG, **dict(config or {})}
        settings["operator"] = str(settings.get("operator") or "").strip()
        if not settings["operator"]:
            raise ValueError("Enter who is running the test.")
        settings["run_name"] = str(settings.get("run_name") or "").strip() or build_test_name(
            settings["operator"], settings.get("test_type"), settings.get("sample_id")
        )
        settings["notebook_id"] = settings["run_name"]
        repeating = settings.get("test_type") == "breakthrough_cycles"
        if repeating:
            settings["test_type"] = "breakthrough"
            settings["automatic_cutoff_enabled"] = True
        settings["core_controls"] = not bool(settings.get("automatic_cutoff_enabled"))
        with self._lock:
            return self.service.start(settings)

    def stop_test(self):
        with self._lock:
            self.service.stop()

    def manual(self, action, arguments=None):
        with self._lock:
            if self._s3 is not None:
                if action == "set_valves":
                    return self._s3.set_valves(dict(arguments or {}).get("valves"))
                if action == "safe_state":
                    return self._s3.all_off()
                raise ValueError("Direct S3 supports Valve 1–6 and All off only.")
            return self.service.manual(action, **dict(arguments or {}))

    def runs(self):
        with self._lock:
            return _plain(self.service.list_runs())

