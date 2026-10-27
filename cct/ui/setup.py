"""Recipe form and metadata editor, generated from a small editable field schema."""

from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLineEdit, QSpinBox, QTextEdit, QVBoxLayout, QWidget

from cct.contracts import DEFAULT_CONFIG
from cct.named_tests import TESTS, normalize_test_config
from cct.operator import test_plan
from .widgets import QComboBox, banner, button, card, heading, label


# key: (human label, units, explanation). This is the single UI field catalogue.
ADVANCED = {
    "Analyzer readiness": {
        "analyzer_target_c": ("Analyzer target", "°C", "The LI-840A temperature required before proceeding."),
        "analyzer_tolerance_c": ("Readiness tolerance", "°C", "Acceptable distance from the analyzer target."),
        "analyzer_dwell_s": ("Readiness dwell", "s", "Continuous time in the readiness temperature band."),
    },
    "Temperature & regeneration": {
        "adsorption_temp_c": ("Adsorption starting temperature", "°C", "Cool the column to this temperature before measurement."),
        "desorption_target_c": ("Regeneration target", "°C", "Local Core2 heater target during desorption."),
        "max_temperature_c": ("High temperature limit", "°C", "Crossing this value ends automatic progression and turns off heat."),
        "near_zero_ppm": ("Release baseline tolerance", "ppm", "CO₂ must return near the purge baseline, after a meaningful release."),
        "release_dwell_s": ("Release completion dwell", "s", "Continuous return near baseline required for completion."),
        "min_release_s": ("Minimum regeneration time", "s", "Minimum time before regeneration can finish."),
        "purge_heat_without_n2": ("Heat before admitting N₂", "", "Procedure-specific. Confirm the physical method before live commissioning."),
    },
    "Stability & breakthrough": {
        "stable_window_s": ("Stability window", "s", "Time window used to evaluate variation and drift."),
        "stable_co2_range_ppm": ("CO₂ allowable variation", "ppm", "Maximum CO₂ range across the stability window."),
        "stable_flow_range_sccm": ("Flow allowable variation", "sccm", "Maximum flow range across the stability window."),
        "baseline_s": ("Inlet reference recording", "s", "Duration of bypass measurements used for inlet CO₂ reference."),
        "breakthrough_hold_s": ("Breakthrough confirmation", "s", "Fresh valid readings must sustain the threshold for this duration."),
        "breakthrough_guard_s": ("Minimum measurement time", "s", "Delay before a crossing can end measurement. Earlier crossings are still recorded."),
    },
    "Timing & data quality": {
        "depressurize_s": ("Depressurization duration", "s", "Time in the configured vent route."),
        "stage_timeout_s": ("Maximum time per stage", "s", "Stages that cannot finish within this time raise a fault."),
        "stale_after_s": ("Maximum measurement age", "s", "Older readings cannot satisfy automatic progression criteria."),
    },
}


class ExperimentPage(QWidget):
    start_requested = Signal(dict)
    save_requested = Signal(str, dict)
    connect_requested = Signal()
    connections_requested = Signal()
    review_requested = Signal(dict)

    def __init__(self):
        super().__init__()
        self.inputs = {}
        self.advanced_rows = {}
        self.advanced_groups = {}
        self._connected = False
        self._running = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(heading("Tests", "Choose a test, enter your sample details, and start recording. End the test when you are finished."))
        selector, selector_body = card()
        selector_body.addWidget(label("CHOOSE A TEST", "eyebrow"))
        self.test_selector = self._create_input("test_type")
        selector_body.addWidget(self.test_selector)
        self.test_description = label("", "subtitle", True)
        selector_body.addWidget(self.test_description)
        self.ending = label("", None, True)
        selector_body.addWidget(self.ending)
        self.plan_toggle = button("What will happen?", kind="")
        self.plan_toggle.setCheckable(True)
        self.plan_toggle.setStyleSheet("border: none; background: transparent; text-align: left; color: #087f83; padding: 2px 0;")
        selector_body.addWidget(self.plan_toggle)
        self.plan_detail = label("", "subtitle", True)
        self.plan_detail.hide()
        self.plan_toggle.toggled.connect(self.plan_detail.setVisible)
        selector_body.addWidget(self.plan_detail)
        layout.addWidget(selector)
        identity, body = card()
        body.addWidget(label("TEST DETAILS", "eyebrow"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(13)
        fields = [
            ("notebook_id", "Notebook / test ID", "Example: CCT-01-01"),
            ("sample_id", "Sample ID", "A label you will recognize later"),
            ("sample_mass_g", "Sample mass", "Measured sample mass in grams"),
            ("operator", "Operator · optional", "Name or initials"),
        ]
        for n, (key, title, tip) in enumerate(fields):
            edit = self._create_input(key)
            edit.setToolTip(tip)
            if isinstance(edit, QLineEdit):
                edit.setPlaceholderText(tip)
            cell = QVBoxLayout()
            cell.setSpacing(6)
            cell.addWidget(label(title))
            cell.addWidget(edit)
            grid.addLayout(cell, n // 2, n % 2)
        body.addLayout(grid)
        body.addWidget(label("Notes · optional"))
        self.inputs["notes"] = QTextEdit()
        self.inputs["notes"].setPlaceholderText("Preparation, material batch, observations, or anything future you should know…")
        self.inputs["notes"].setMaximumHeight(56)
        body.addWidget(self.inputs["notes"])
        layout.addWidget(identity)
        recipe, body = card()
        self.settings_card = recipe
        body.addWidget(label("SETTINGS", "eyebrow"))
        self.heat_setting = QWidget()
        heat_form = QFormLayout(self.heat_setting)
        heat_form.setContentsMargins(0, 0, 0, 0)
        heat_form.addRow("Desorption temperature", self._create_input("desorption_target_c", "°C"))
        body.addWidget(self.heat_setting)
        self.advanced_toggle = button("+  Advanced settings")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self._show_advanced)
        body.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced_layout = QGridLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 3, 0, 0)
        advanced_layout.setSpacing(14)
        self.automatic = self._create_input("automatic_cutoff_enabled")
        self.automatic.setText("Enable automatic breakthrough cutoff and repeated cycles")
        self.automatic.setToolTip("Optional. Without this setting, breakthrough records continuously until you choose End test.")
        advanced_layout.addWidget(self.automatic, 0, 0, 1, 2)
        self.cutoff_controls = QGroupBox("Automatic breakthrough sequence")
        cutoff_form = QFormLayout(self.cutoff_controls)
        for key, title in (("breakthrough_pct", "End measurement at"), ("cycles", "Number of cycles"), ("initial_treatment", "Initial treatment")):
            cutoff_form.addRow(title, self._create_input(key))
        final = self._create_input("final_purge")
        final.setText("Regenerate after the final cycle")
        cutoff_form.addRow(final)
        cutoff_form.addRow(label("This opt-in sequence can end each measurement automatically and repeat the selected number of cycles.", "subtitle", True))
        body.addWidget(self.cutoff_controls)
        self.automatic.toggled.connect(self._automatic_changed)
        self.automatic.hide()
        for n, (name, specs) in enumerate(ADVANCED.items()):
            group = QGroupBox(name)
            self.advanced_groups[name] = (group, set(specs) - {"desorption_target_c"})
            form = QFormLayout(group)
            form.setSpacing(9)
            for key, (title, unit, tip) in specs.items():
                if key == "desorption_target_c":
                    continue
                edit = self._create_input(key, unit)
                edit.setToolTip(tip)
                if isinstance(edit, QCheckBox):
                    edit.setText(title)
                    form.addRow(edit)
                else:
                    form.addRow(title, edit)
                self.advanced_rows[key] = (edit, form.labelForField(edit))
            advanced_layout.addWidget(group, n // 2 + 2, n % 2)
        self.advanced.setVisible(False)
        body.addWidget(self.advanced)
        layout.addWidget(recipe)
        self.validation = label("", "error", True)
        layout.addWidget(self.validation)
        foot = QHBoxLayout()
        self.status = label("Select USB devices in Connections, then open Connection options.", "subtitle", True)
        foot.addWidget(self.status, 1)
        self.connect_btn = button("Connection options", self.connect_requested.emit)
        foot.addWidget(self.connect_btn)
        self.connections_btn = button("Set up USB devices", self.connections_requested.emit)
        foot.addWidget(self.connections_btn)
        self.review_btn = button("Review checks", lambda: self.review_requested.emit(self.config()))
        foot.addWidget(self.review_btn)
        self.start_btn = button("Start test", self._start, "primary")
        foot.addWidget(self.start_btn)
        layout.addLayout(foot)
        layout.addStretch()
        self.test_selector.currentIndexChanged.connect(self._test_changed)
        self.inputs["desorption_target_c"].valueChanged.connect(self._test_changed)
        self.load_config({**DEFAULT_CONFIG, "test_type": "breakthrough", "automatic_cutoff_enabled": False})
        self._test_changed()

    def _create_input(self, key, unit=""):
        default = {"test_type": "breakthrough", "automatic_cutoff_enabled": False}.get(key, DEFAULT_CONFIG.get(key))
        if isinstance(default, bool):
            edit = QCheckBox()
        elif key == "test_type":
            edit = QComboBox()
            for value, (name, _) in TESTS.items():
                edit.addItem(name, value)
            edit.addItem("Repeating breakthrough cycles", "breakthrough_cycles")
        elif key == "initial_treatment":
            edit = QComboBox()
            edit.addItem("Initial desorption", "desorption")
            edit.addItem("N₂ purge", "purge")
            edit.addItem("No initial treatment", "none")
        elif isinstance(default, int):
            edit = QSpinBox()
            edit.setRange(1, 1000)
        elif isinstance(default, float):
            edit = QDoubleSpinBox()
            edit.setDecimals(3 if key == "sample_mass_g" else 1)
            edit.setRange(0, 100000)
            if key == "sample_mass_g":
                edit.setRange(0.001, 100000)
            if key == "breakthrough_pct":
                edit.setRange(0.1, 100)
                unit = "% of inlet"
            if key == "sample_mass_g":
                unit = "g"
            if unit:
                edit.setSuffix(" " + unit)
        else:
            edit = QLineEdit()
            edit.setMaxLength(200)
        edit.setAccessibleName(key.replace("_", " "))
        self.inputs[key] = edit
        return edit

    def config(self):
        result = {**deepcopy(DEFAULT_CONFIG), "test_type": "breakthrough", "automatic_cutoff_enabled": False}
        for key, edit in self.inputs.items():
            if isinstance(edit, QCheckBox):
                result[key] = edit.isChecked()
            elif isinstance(edit, QComboBox):
                result[key] = edit.currentData()
            elif isinstance(edit, (QSpinBox, QDoubleSpinBox)):
                result[key] = edit.value()
            elif isinstance(edit, QTextEdit):
                result[key] = edit.toPlainText().strip()
            else:
                result[key] = edit.text().strip()
        repeating = result["test_type"] == "breakthrough_cycles"
        result["test_type"] = "breakthrough" if repeating else result["test_type"]
        result["automatic_cutoff_enabled"] = repeating
        result["core_controls"] = not repeating
        if not repeating:
            result.update(automatic_cutoff_enabled=False, cycles=1, initial_treatment="none", final_purge=False)
        result["recipe_name"] = self.test_selector.currentText()
        return normalize_test_config(result)

    def load_config(self, config):
        for key, value in config.items():
            edit = self.inputs.get(key)
            if edit is None:
                continue
            if isinstance(edit, QCheckBox):
                edit.setChecked(bool(value))
            elif isinstance(edit, QComboBox):
                idx = edit.findData(value)
                if idx >= 0:
                    edit.setCurrentIndex(idx)
            elif isinstance(edit, (QSpinBox, QDoubleSpinBox)):
                if value is not None:
                    edit.setValue(float(value) if isinstance(edit, QDoubleSpinBox) else int(value))
            elif isinstance(edit, QTextEdit):
                edit.setPlainText(str(value))
            else:
                edit.setText(str(value))
        self.validation.clear()
        if config.get("test_type") == "breakthrough" and config.get("automatic_cutoff_enabled") is True:
            self.test_selector.setCurrentIndex(self.test_selector.findData("breakthrough_cycles"))
        self._test_changed()

    def _automatic_changed(self, checked):
        if checked and self.test_selector.currentData() == "breakthrough":
            self.test_selector.setCurrentIndex(self.test_selector.findData("breakthrough_cycles"))
        elif not checked and self.test_selector.currentData() == "breakthrough_cycles":
            self.test_selector.setCurrentIndex(self.test_selector.findData("breakthrough"))

    def _show_advanced(self, visible):
        self.advanced.setVisible(visible)
        self.advanced_toggle.setText(("-" if visible else "+") + "  Advanced settings")

    def _test_changed(self, *_):
        selected = self.test_selector.currentData()
        repeating = selected == "breakthrough_cycles"
        self.automatic.blockSignals(True)
        self.automatic.setChecked(repeating)
        self.automatic.blockSignals(False)
        descriptions = {"zero": "Core Zero: nitrogen bypass to the analyzer; run until End.",
                        "adsorption": "Core Adsorption: column to exhaust, with the Core Air on/off control.",
                        "breakthrough": "Core Breakthrough: starts in bypass. You choose Through column and Return to bypass.",
                        "desorption": "Core Desorption: nitrogen through the column. Heating starts only when you request it.",
                        "breakthrough_cycles": "Automatically repeat inlet reference, breakthrough, regeneration and cooldown."}
        self.test_description.setText(descriptions.get(selected, ""))
        self.settings_card.setVisible(repeating)
        self.heat_setting.setVisible(repeating)
        self.automatic.hide()
        self.cutoff_controls.setVisible(repeating)
        common = {"analyzer_target_c", "analyzer_tolerance_c", "analyzer_dwell_s", "stage_timeout_s", "stale_after_s"}
        stability = {"stable_window_s", "stable_co2_range_ppm", "stable_flow_range_sccm"}
        if selected == "zero":
            visible = common
        elif selected == "adsorption":
            visible = common | {"adsorption_temp_c", "max_temperature_c"}
        elif selected == "desorption":
            visible = common | stability | {"max_temperature_c", "near_zero_ppm"}
        elif repeating:
            visible = set(self.advanced_rows)
        else:
            visible = common | stability | {"adsorption_temp_c", "max_temperature_c", "baseline_s"}
        for key, (edit, caption) in self.advanced_rows.items():
            edit.setVisible(key in visible)
            if caption is not None:
                caption.setVisible(key in visible)
        for group, keys in self.advanced_groups.values():
            group.setVisible(bool(keys & visible))
        plan = test_plan(self.config())
        self.ending.setText(plan["ending"] + " Measurements are saved automatically.")
        self.plan_detail.setText("\n".join(f"{index}. {step}" for index, step in enumerate(plan["steps"], 1)) +
                                 "\n\n" + plan["shutdown"] + "\n" + plan["safety"])

    def set_validation(self, errors):
        self.validation.setText("\n".join("• " + str(e) for e in errors))

    def update_state(self, snapshot):
        self._connected = bool(snapshot.get("connected"))
        self._running = bool(snapshot.get("running"))
        blockers = snapshot.get("blockers") or []
        limited = any(role in snapshot.get("manual_devices", []) for role in ("alicat", "licor"))
        repeat_index = self.test_selector.findData("breakthrough_cycles")
        self.test_selector.model().item(repeat_index).setEnabled(not limited and not self._running)
        if limited and self.config().get("automatic_cutoff_enabled"):
            blockers = [*blockers, "Repeating cycles require USB CO₂ and flow readings. Choose a single Core test."]
        self.start_btn.setEnabled(self._connected and not self._running and not blockers)
        self.review_btn.setEnabled(not self._running)
        for edit in self.inputs.values():
            edit.setEnabled(not self._running)
        self.connections_btn.setVisible(not self._running and (not self._connected or bool(blockers)))
        self.connect_btn.setVisible(not self._connected)
        shown = [str(item) for item in blockers[:2]]
        if len(blockers) > 2:
            shown.append(f"{len(blockers) - 2} more checks — open Connections for help.")
        self.status.setText("This test's settings are frozen. End it before setting up another." if self._running else "\n".join(shown) if blockers else "Ready to review hardware readiness." if self._connected else "Select USB devices in Connections and open Connection options.")

    def _start(self):
        self.start_requested.emit(self.config())

    def _save(self):
        config = self.config()
        self.save_requested.emit(config["recipe_name"] or "Custom recipe", config)
