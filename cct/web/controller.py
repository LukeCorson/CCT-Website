"""Thread-safe adapter between HTTP requests and the synchronous LabService."""

import threading
import time
from collections import deque

from cct.contracts import DEFAULT_CONFIG
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
    def __init__(self, service=None):
        self.service = service or LabService()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._last_tick = time.monotonic()

    def start_loop(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_tick = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="cct-web-acquisition", daemon=True)
        self._thread.start()

    def close(self):
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
            snapshot = _plain(self.service.snapshot())
        devices = snapshot.get("devices") or {}
        # The browser names the direct controller target S3. Until the direct-S3
        # firmware is commissioned, the existing bridge appears here fail-closed.
        devices["s3"] = dict(devices.pop("core2", {}) or {})
        devices["s3"].setdefault("message", "Direct S3 controller not connected")
        snapshot["devices"] = devices
        snapshot["controller_target"] = "s3"
        snapshot["direct_s3_commissioned"] = False
        snapshot["direct_s3_notice"] = (
            "Direct S3 valve/heater control remains locked until S3 temperature feedback, "
            "heartbeat shutdown, output readback, and the reviewed safe vent mask are commissioned."
        )
        return snapshot

    def connect(self, mode, profile_path=None):
        with self._lock:
            return _plain(self.service.connect(mode=mode, profile_path=profile_path or None))

    def disconnect(self):
        with self._lock:
            self.service.disconnect()

    def start_test(self, config):
        settings = {**DEFAULT_CONFIG, **dict(config or {})}
        settings["operator"] = str(settings.get("operator") or "").strip()
        if not settings["operator"]:
            raise ValueError("Enter who is running the test.")
        settings["run_name"] = str(settings.get("run_name") or "").strip() or build_test_name(
            settings["operator"], settings.get("test_type"), settings.get("sample_id")
        )
        settings["notebook_id"] = settings["run_name"]
        settings["core_controls"] = not bool(settings.get("automatic_cutoff_enabled"))
        with self._lock:
            return self.service.start(settings)

    def stop_test(self):
        with self._lock:
            self.service.stop()

    def manual(self, action, arguments=None):
        # Simulation is usable for interface verification. Hardware is blocked
        # here until a direct-S3 controller implements the safety contract.
        with self._lock:
            if self.service.mode == "hardware":
                raise RuntimeError("Direct S3 hardware control is not commissioned yet.")
            return self.service.manual(action, **dict(arguments or {}))

    def runs(self):
        with self._lock:
            return _plain(self.service.list_runs())

