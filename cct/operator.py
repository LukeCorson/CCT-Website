"""Plain-language test plans and recovery guidance, independently testable."""

from .contracts import DEFAULT_CONFIG
from .named_tests import TESTS, normalize_test_config


def test_plan(config):
    settings = {**DEFAULT_CONFIG, **normalize_test_config(config)}
    kind = settings.get("test_type", "breakthrough")
    automatic = kind == "breakthrough" and settings.get("automatic_cutoff_enabled", "test_type" not in settings)
    steps = {
        "zero": ["Wait for the analyzer to warm up", "Send nitrogen past the column to the analyzer", "Record the baseline until you end the test"],
        "adsorption": ["Wait for analyzer readiness and a cool column", "Send air through the column to exhaust", "Record flow and temperature until you end the test"],
        "breakthrough": ["Wait for analyzer readiness and a cool column", "Stabilize bypass flow and record an inlet CO₂ reference", "Switch gas through the column to the analyzer", "Record the column outlet until you end the test"],
        "desorption": ["Wait for analyzer readiness", "Establish a nitrogen baseline", f"Heat under nitrogen to {settings['desorption_target_c']:g} °C", "Hold the temperature and record released CO₂ until you end the test"],
    }.get(kind, [])
    if settings.get("core_controls") and not automatic:
        steps = {"zero": ["Apply Core Zero routing and open nitrogen", "Record until End test"],
                 "adsorption": ["Apply Core Adsorption routing to exhaust; air starts OFF", "Use the Core Air on/off control", "End test when finished"],
                 "breakthrough": ["Apply Core Breakthrough routing with air in bypass", "Use Through column and Return to bypass as needed", "End test when finished"],
                 "desorption": ["Apply Core Desorption routing and open nitrogen; heater starts OFF", "Use Heat to target or the 150 °C heating control when ready", "End test turns the heater and supplies off"]}[kind]
    if automatic:
        steps = ["Wait for analyzer readiness", "Apply the selected initial treatment and cooldown",
                 "Record the inlet reference and column outlet",
                 f"Use the explicitly selected {settings['breakthrough_pct']:g}% cutoff; regenerate and repeat {settings['cycles']} cycle(s)"]
    return {"title": TESTS.get(kind, ("Test",))[0], "steps": steps,
            "ending": "Automatic sequence; End test remains available." if automatic else "Record continuously until you choose End test.",
            "shutdown": f"End test requests Core source valves OFF, heater OFF and the reviewed vent route. After S3 acknowledgement, hold the vent route for {settings['shutdown_hold_s']:g} s before closing the record. This does not mean the column has cooled." + (" The 146C is not shut off by the app; operate it manually as required." if settings.get("calibrator_mode") == "manual" else ""),
            "safety": f"Safety faults and the {settings['stage_timeout_s'] / 60:g}-minute maximum per step remain active.",
            "measurement_note": "In Adsorption, gas goes to exhaust: analyzer CO₂ is not the column-outlet measurement." if kind == "adsorption" else "All preparation, measurement and shutdown steps are recorded."}


def recovery_hint(reason):
    text = str(reason).casefold()
    if any(word in text for word in ("temperature reached", "temperature limit", "overtemp")):
        return "Keep clear of the hot column. Confirm heater shutdown using the approved physical procedure; let it cool and investigate before starting a new test."
    if any(word in text for word in ("recording", "disk", "storage")):
        return "Keep the apparatus safely stopped. Check local disk space and folder permissions, preserve the existing records, then restart the app."
    if any(word in text for word in ("flow", "nitrogen")):
        return "Check the supply, regulator, tubing and meter using the approved procedure. Confirm the real gas path and flow before starting a new test."
    if any(word in text for word in ("stale", "missing", "disconnect", "link", "heartbeat")):
        return "Check device power and labelled cables. Review Connections and wait for fresh readings. Reconnecting never resumes the previous test automatically."
    return "Review Test activity and Connections, confirm the physical apparatus is safe, and resolve the cause before starting a new test."
