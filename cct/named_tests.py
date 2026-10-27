"""Public test catalogue; new tests never inherit a hidden percentage cutoff."""

TESTS = {
    "zero": ("Zero", "Check the analyzer baseline with nitrogen, bypassing the column."),
    "adsorption": ("Adsorption", "Core column-to-exhaust route; air starts off and is controlled with Air on/off."),
    "breakthrough": ("Breakthrough", "Core air-bypass route; choose Through column or Return to bypass."),
    "desorption": ("Desorption", "Core nitrogen-through-column route; heating starts only when requested."),
}


def normalize_test_config(config):
    if not isinstance(config, dict):
        return config
    result = dict(config)
    if "test_type" not in result:
        return result  # Old saved experiments retain their documented behavior.
    automatic = result.get("automatic_cutoff_enabled", False)
    result.setdefault("automatic_cutoff_enabled", False)
    if not automatic:
        result.update(cycles=1, initial_treatment="none", final_purge=False,
                      breakthrough_pct=None)
    test_type = result["test_type"]
    result["recipe_name"] = TESTS.get(test_type, ("Unknown test",))[0] if isinstance(test_type, str) else "Unknown test"
    return result
