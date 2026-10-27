"""Small shared contracts. Hardware, experiment logic, and widgets stay independent."""

from pathlib import Path
from typing import Protocol

CHANNELS = {
    "co2_ppm": ("CO₂", "ppm", "licor"),
    "flow_sccm": ("Flow", "sccm", "alicat"),
    "column_temp_c": ("Column temperature", "°C", "core2"),
    "ambient_temp_c": ("Ambient temperature", "°C", "core2"),
    "ambient_pressure_kpa": ("Ambient pressure", "kPa", "core2"),
    "analyzer_temp_c": ("Analyzer temperature", "°C", "licor"),
}

DEFAULT_CONFIG = {
    "run_name": "",
    "notebook_id": "CCT-01-01",
    "sample_id": "Sample-001",
    "sample_mass_g": 1.0,
    "operator": "",
    "notes": "",
    "recipe_name": "Standard breakthrough",
    "cycles": 3,
    "breakthrough_pct": 10.0,
    "initial_treatment": "desorption",
    "final_purge": True,
    "analyzer_target_c": 51.5,
    "analyzer_tolerance_c": 1.0,
    "analyzer_dwell_s": 10.0,
    "adsorption_temp_c": 35.0,
    "desorption_target_c": 150.0,
    "max_temperature_c": 175.0,
    "stable_window_s": 30.0,
    "stable_co2_range_ppm": 8.0,
    "stable_flow_range_sccm": 5.0,
    "baseline_s": 120.0,
    "breakthrough_hold_s": 5.0,
    "breakthrough_guard_s": 0.0,
    "near_zero_ppm": 3.0,
    "release_dwell_s": 30.0,
    "min_release_s": 60.0,
    "depressurize_s": 60.0,
    "shutdown_hold_s": 10.0,
    "stage_timeout_s": 3600.0,
    "stale_after_s": 5.0,
    "purge_heat_without_n2": True,
}

DEVICE_IDS = ("core2", "licor", "alicat", "calibrator")


def manual_calibrator(profile):
    return manual_device(profile, "calibrator")


def manual_device(profile, role):
    return role in ("alicat", "licor", "calibrator") and isinstance(profile, dict) and profile.get(role + "_mode") == "manual"


def required_devices(profile):
    return tuple(role for role in DEVICE_IDS if not manual_device(profile, role))


def required_channels(snapshot):
    devices = snapshot.get("devices", {})
    return tuple(channel for channel, (_, _, role) in CHANNELS.items() if devices.get(role, {}).get("required") is not False)


class Rig(Protocol):
    """I/O must be nonblocking; real serial work belongs to a device worker.

    snapshot(): {values: channel->number|None, ages: channel->seconds,
      devices: id->{connected, fresh, message}, outputs: dict,
      ready: bool, blockers: list[str]}.
    execute(): queue a named action, never a widget-specific command.
    drain_events(): consume newly received command/ack/error events.
    """

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def tick(self, dt: float, stage: str = "idle") -> None: ...
    def snapshot(self) -> dict: ...
    def execute(self, action: str, **kwargs) -> None: ...
    def drain_events(self) -> list[dict]: ...


def default_data_root() -> Path:
    """Per-user application storage, independent of the source/install folder.

    CCT_DATA_DIR deliberately overrides the OS convention for a lab-managed
    local data disk. Existing source-tree data are never moved automatically.
    """
    import os
    import sys

    override = os.environ.get("CCT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "CCTControl"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CCTControl"
    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data_home) if xdg_data_home and Path(xdg_data_home).is_absolute() else Path.home() / ".local" / "share"
    return base / "cct-control"
