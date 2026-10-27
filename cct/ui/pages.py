"""Monitoring, connection, history, recipe, and commissioning screens."""

from pathlib import Path

from PySide6.QtCore import QDateTime, QTimeZone, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLineEdit, QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from cct.contracts import CHANNELS
from cct.named_tests import TESTS
from cct.operator import recovery_hint
from .plots import CycleOverlay, RunPlots
from .theme import COLORS
from .widgets import QComboBox, MetricCard, banner, button, card, duration, heading, label, number

DEVICE_NAMES = {"core2": ("Core2 controller", "Temperature, ambient sensors & local controls"), "licor": ("LI-COR LI-840A", "CO₂, H₂O & analyzer measurements"), "alicat": ("Alicat B-500C", "Gas flow measurement"), "calibrator": ("Thermo 146C", "Air / N₂ selection & gas delivery")}


class LivePage(QWidget):
    connect_requested = Signal()
    setup_requested = Signal()
    stop_requested = Signal()
    observation_requested = Signal(str)
    connections_requested = Signal()
    core_action_requested = Signal(str, object)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(17)
        title_row = QHBoxLayout()
        title_row.addWidget(heading("Live test", "Watch measurements and progress. Recording continues until you end the test."), 1)
        layout.addLayout(title_row)
        self.welcome, welcome_body = card()
        self.welcome_title = label("Choose a test to begin.", "sectionTitle")
        welcome_body.addWidget(self.welcome_title)
        self.welcome_detail = label("Choose USB devices in Connections, then connect using Connection options.", "subtitle", True)
        welcome_body.addWidget(self.welcome_detail)
        row = QHBoxLayout()
        self.connect_btn = button("Connection options", self.connect_requested.emit, "primary")
        self.new_btn = button("Choose a test", self.setup_requested.emit)
        row.addWidget(self.connect_btn)
        row.addWidget(self.new_btn)
        row.addStretch()
        row.addWidget(label("01  CONNECT     →     02  SET UP     →     03  RUN & REVIEW", "eyebrow"))
        welcome_body.addLayout(row)
        layout.addWidget(self.welcome)
        metrics = QHBoxLayout()
        metrics.setSpacing(13)
        self.metrics = {}
        for key in ("co2_ppm", "flow_sccm", "column_temp_c"):
            title, units, _ = CHANNELS[key]
            metric = MetricCard(title, units, COLORS[key])
            self.metrics[key] = metric
            metrics.addWidget(metric, 1)
        layout.addLayout(metrics)
        self.stage_card, stage_body = card()
        stage_body.setContentsMargins(18, 12, 18, 12)
        stage_body.setSpacing(6)
        row = QHBoxLayout()
        left = QVBoxLayout()
        self.cycle = label("RUN STATUS", "eyebrow")
        self.stage = label("Ready when you are", "sectionTitle")
        self.waiting = label("No active experiment", "subtitle", True)
        left.addWidget(self.cycle)
        left.addWidget(self.stage)
        left.addWidget(self.waiting)
        row.addLayout(left, 1)
        right = QVBoxLayout()
        right.addWidget(label("ELAPSED", "eyebrow"))
        self.elapsed = label("00:00:00", "sectionTitle")
        right.addWidget(self.elapsed)
        row.addLayout(right)
        row.addSpacing(22)
        self.stop_btn = button("End test", self.stop_requested.emit, "danger")
        self.stop_btn.setToolTip("Ends feed and requests the configured controlled stop. Recording continues during shutdown.")
        row.addWidget(self.stop_btn)
        stage_body.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        stage_body.addWidget(self.progress)
        self.run_detail = label("Data will be saved automatically when an experiment starts.", "subtitle", True)
        stage_body.addWidget(self.run_detail)
        layout.addWidget(self.stage_card)
        self.core_card, core_body = card()
        core_body.addWidget(label("CORE TEST CONTROLS", "eyebrow"))
        core_row = QHBoxLayout()
        self.air_btn = button("Air on", lambda checked: self.core_action_requested.emit("air", checked))
        self.air_btn.setCheckable(True)
        self.column_btn = button("Through column", lambda checked: self.core_action_requested.emit("column", checked))
        self.column_btn.setCheckable(True)
        self.heat_target = QDoubleSpinBox()
        self.heat_target.setRange(50, 170)
        self.heat_target.setValue(150)
        self.heat_target.setSuffix(" °C")
        self.heat_btn = button("Heat to target", lambda: self.core_action_requested.emit("heater", self.heat_target.value()))
        self.heat150_btn = button("150 °C heat", lambda: self.core_action_requested.emit("heater", 150.0))
        self.heat_off_btn = button("Heater off", lambda: self.core_action_requested.emit("heater", None))
        for control in (self.air_btn, self.column_btn, self.heat_target, self.heat_btn, self.heat150_btn, self.heat_off_btn):
            core_row.addWidget(control)
        core_row.addStretch()
        core_body.addLayout(core_row)
        self.core_card.hide()
        layout.addWidget(self.core_card)
        self.recovery = label("", "error", True)
        self.recovery.hide()
        layout.addWidget(self.recovery)
        self.connections_btn = button("Review connections", self.connections_requested.emit)
        self.connections_btn.hide()
        layout.addWidget(self.connections_btn)
        note_row = QHBoxLayout()
        self.observation = QLineEdit()
        self.observation.setPlaceholderText("Observation during this test — saved with the current time")
        self.observation.setMaxLength(2000)
        self.observation.setAccessibleName("Time-stamped test observation")
        self.note_btn = button("Save observation", lambda: self.observation_requested.emit(self.observation.text()))
        self.observation.returnPressed.connect(self.note_btn.click)
        note_row.addWidget(self.observation, 1)
        note_row.addWidget(self.note_btn)
        layout.addLayout(note_row)
        plot_card, plot_body = card()
        self.plots = RunPlots()
        plot_body.addWidget(self.plots)
        layout.addWidget(plot_card)
        activity, body = card()
        header = QHBoxLayout()
        header.addWidget(label("Test activity", "sectionTitle"))
        header.addStretch()
        header.addWidget(label("TRANSITIONS · COMMANDS · FAULTS", "eyebrow"))
        body.addLayout(header)
        self.activity = QTableWidget(0, 3)
        self.activity.setHorizontalHeaderLabels(["ELAPSED", "EVENT", "DETAIL"])
        self.activity.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.activity.setColumnWidth(0, 95)
        self.activity.setColumnWidth(1, 130)
        self.activity.verticalHeader().hide()
        self.activity.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.activity.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.activity.setMaximumHeight(205)
        self.activity.setMinimumHeight(130)
        body.addWidget(self.activity)
        layout.addWidget(activity)
        layout.addStretch()
        self._events_key = None

    def update_state(self, snapshot):
        connected, running = bool(snapshot.get("connected")), bool(snapshot.get("running"))
        mode = snapshot.get("mode", "simulation")
        self.observation.setEnabled(running)
        self.note_btn.setEnabled(running)
        fault = snapshot.get("stage") == "fault"
        self.recovery.setVisible(fault)
        self.connections_btn.setVisible(fault)
        if fault:
            self.recovery.setText("Next: " + recovery_hint(snapshot.get("waiting_for", "")))
        automatic = bool(snapshot.get("automatic_cutoff_enabled"))
        controls = snapshot.get("core_controls") or {}
        self.core_card.setVisible(running and bool(controls) and controls.get("test_type") != "zero")
        kind = controls.get("test_type")
        self.air_btn.setVisible(kind == "adsorption")
        self.air_btn.setChecked(bool(controls.get("air_on")))
        self.air_btn.setText("Air off" if controls.get("air_on") else "Air on")
        self.column_btn.setVisible(kind == "breakthrough")
        self.column_btn.setChecked(bool(controls.get("column_on")))
        self.column_btn.setText("Return to bypass" if controls.get("column_on") else "Through column")
        for control in (self.heat_target, self.heat_btn, self.heat150_btn, self.heat_off_btn):
            control.setVisible(kind == "desorption")
        for control in (self.air_btn, self.column_btn, self.heat_target, self.heat_btn, self.heat150_btn, self.heat_off_btn):
            control.setEnabled(bool(controls.get("ready")))
        self.plots.ratio.setVisible(automatic)
        if not automatic:
            self.plots.ratio.setChecked(False)
        self.connect_btn.setVisible(not connected)
        self.new_btn.setEnabled(not running)
        self.welcome.setVisible(not running and not snapshot.get("run_id"))
        self.welcome_title.setText("Connected. Choose a test." if connected else "Choose a test to begin.")
        self.welcome_detail.setText("Choose a test in Tests. Manual instruments have no app readings or commands." if connected else "Select USB devices in Connections, then open Connection options to connect.")
        values, ages = snapshot.get("values") or {}, snapshot.get("ages") or {}
        for key, metric in self.metrics.items():
            metric.update_value(values.get(key), ages.get(key), connected, mode == "simulation")
            if CHANNELS[key][2] in snapshot.get("manual_devices", []):
                metric.value.setText("—")
                metric.detail.setText("Manual — not recorded")
        self.plots.plot_flow.setVisible("alicat" not in snapshot.get("manual_devices", []))
        shutting_down = snapshot.get("stage") in {"stopping", "shutdown"}
        self.stop_btn.setText("Shutting down…" if running and shutting_down else "End test")
        self.stop_btn.setEnabled(running and not shutting_down)
        self.cycle.setText(f"CYCLE {snapshot.get('cycle', 0)} OF {snapshot.get('total_cycles', 0)}" if running and snapshot.get("automatic_cutoff_enabled") else str(snapshot.get("test_label") or snapshot.get("test_type") or "TEST STATUS").upper())
        self.stage.setText(str(snapshot.get("stage_label") or "Ready when you are"))
        waiting = snapshot.get("waiting_for") or snapshot.get("stage_description") or "No active experiment"
        self.waiting.setText(str(waiting))
        self.elapsed.setText(duration(snapshot.get("elapsed_s")))
        stage_progress = snapshot.get("stage_progress")
        self.progress.setRange(0, 0 if running and stage_progress is None else 1000)
        # Continuous recording has no predictable completion fraction.
        self.progress.setVisible(stage_progress is not None)
        if stage_progress is not None:
            self.progress.setValue(int(max(0, min(1, float(stage_progress))) * 1000))
        run_path = snapshot.get("run_path")
        self.run_detail.setText(f"{snapshot.get('save_status', 'Recording')}   ·   Run {snapshot.get('run_id', '')}" if run_path else "Data will be saved automatically when an experiment starts.")
        self.run_detail.setToolTip(str(run_path or ""))
        self.plots.update_data(snapshot.get("series"), snapshot.get("baseline_ppm"), snapshot.get("threshold_ratio"), snapshot.get("events"), snapshot.get("run_id"))
        events = (snapshot.get("events") or [])[-25:]
        key = repr(events)
        if key != self._events_key:
            self._events_key = key
            self.activity.setRowCount(len(events))
            for n, event in enumerate(reversed(events)):
                for col, value in enumerate((duration(event.get("elapsed_s")), str(event.get("event", event.get("type", "event"))).replace("_", " "), str(event.get("message", "")))):
                    self.activity.setItem(n, col, QTableWidgetItem(value))


class DevicesPage(QWidget):
    connect_requested = Signal(str, str)
    disconnect_requested = Signal()
    fault_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.profile_path = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(heading("Connections", "Connect the simulator or check the four devices attached through the USB hub."))
        connect_card, body = card()
        body.addWidget(label("CONNECTION SOURCE", "eyebrow"))
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("Built-in simulator", "simulation")
        self.mode.addItem("Hardware · commissioned profile", "hardware")
        self.mode.setMinimumWidth(250)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(self.mode)
        self.profile_btn = button("Choose profile…", self._choose_profile)
        row.addWidget(self.profile_btn)
        self.profile_btn.hide()
        row.addStretch()
        self.connect_btn = button("Connect simulator", self._connect, "primary")
        self.disconnect_btn = button("Disconnect", self.disconnect_requested.emit)
        row.addWidget(self.connect_btn)
        row.addWidget(self.disconnect_btn)
        body.addLayout(row)
        self.profile_label = label("No serial ports are opened in simulation mode.", "subtitle", True)
        body.addWidget(self.profile_label)
        layout.addWidget(connect_card)
        grid = QGridLayout()
        grid.setSpacing(16)
        self.device_labels = {}
        for n, (key, (title, description)) in enumerate(DEVICE_NAMES.items()):
            device_card, device_body = card()
            row = QHBoxLayout()
            row.addWidget(label(title, "sectionTitle"), 1)
            pill = label("DISCONNECTED", "pill")
            row.addWidget(pill)
            device_body.addLayout(row)
            device_body.addWidget(label(description, "subtitle", True))
            status = label("Waiting for connection", None, True)
            device_body.addWidget(status)
            freshness = label("No measurements yet", "subtitle", True)
            device_body.addWidget(freshness)
            self.device_labels[key] = (pill, status, freshness)
            grid.addWidget(device_card, n // 2, n % 2)
        layout.addLayout(grid)
        self.blockers = label("", "error", True)
        layout.addWidget(self.blockers)
        layout.addWidget(banner("Hardware readiness is read from the selected profile. Routing, gas commands, sensor units, and local shutdown behavior must be commissioned before an automated hardware run."))
        self.testing, test_body = card()
        self.test_toggle = button("+  Simulation testing tools")
        self.test_toggle.setCheckable(True)
        test_body.addWidget(self.test_toggle)
        self.test_controls = QWidget()
        test_controls = QVBoxLayout(self.test_controls)
        test_controls.setContentsMargins(0, 0, 0, 0)
        test_controls.addWidget(label("Inject a simulated fault to inspect detection, shutdown, and saved events. These tools never send hardware commands.", "subtitle", True))
        row = QHBoxLayout()
        self.fault = QComboBox()
        for title, key in (("Stale analyzer readings", "stale"), ("LI-COR disconnected", "licor_disconnect"), ("Column overtemperature", "overtemp"), ("Flow lost", "flow_loss"), ("Clear injected fault", "clear")):
            self.fault.addItem(title, key)
        row.addWidget(self.fault, 1)
        self.inject_btn = button("Apply simulation fault", lambda: self.fault_requested.emit(self.fault.currentData()))
        row.addWidget(self.inject_btn)
        test_controls.addLayout(row)
        test_body.addWidget(self.test_controls)
        self.test_controls.hide()
        self.test_toggle.toggled.connect(self.test_controls.setVisible)
        self.test_toggle.toggled.connect(lambda enabled: self.test_toggle.setText(("-" if enabled else "+") + "  Simulation testing tools"))
        layout.addWidget(self.testing)
        layout.addStretch()

    def _mode_changed(self):
        hardware = self.mode.currentData() == "hardware"
        self.profile_btn.setVisible(hardware)
        self.connect_btn.setText("Connect hardware" if hardware else "Connect simulator")
        self.profile_label.setText(self.profile_path or "Choose a commissioned JSON hardware profile." if hardware else "No serial ports are opened in simulation mode.")

    def _choose_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose hardware profile", "", "JSON profile (*.json)")
        if path:
            self.profile_path = path
            self.profile_label.setText(path)

    def _connect(self):
        self.connect_requested.emit(self.mode.currentData(), self.profile_path)

    def update_state(self, snapshot):
        connected, running = bool(snapshot.get("connected")), bool(snapshot.get("running"))
        self.connect_btn.setEnabled(not connected and not running)
        self.disconnect_btn.setEnabled(connected and not running)
        self.mode.setEnabled(not connected and not running)
        self.profile_btn.setEnabled(not connected and not running)
        self.testing.setVisible(snapshot.get("mode", "simulation") == "simulation")
        self.inject_btn.setEnabled(connected)
        for key, (pill, status, freshness) in self.device_labels.items():
            info = (snapshot.get("devices") or {}).get(key) or {}
            device_connected, fresh = bool(info.get("connected")), bool(info.get("fresh"))
            pill.setText("SIMULATED" if device_connected and snapshot.get("mode") == "simulation" and fresh else "CONNECTED" if device_connected and fresh else "STALE" if device_connected else "DISCONNECTED")
            pill.setStyleSheet("color: #a36c16; background: #fff2d9;" if device_connected and not fresh else "")
            status.setText(str(info.get("message") or ("Ready" if device_connected else "Waiting for connection")))
            ages = [(snapshot.get("ages") or {}).get(ch) for ch, (_, _, device) in CHANNELS.items() if device == key]
            ages = [float(age) for age in ages if age is not None]
            freshness.setText(f"Oldest measurement · {number(max(ages))} s ago" if ages else "Command / status connection" if key == "calibrator" and device_connected else "No measurements yet")
        self.blockers.setText("\n".join(str(s) for s in (snapshot.get("blockers") or [])))


class RecipesPage(QWidget):
    use_requested = Signal(dict)
    refresh_requested = Signal()

    def __init__(self):
        super().__init__()
        self.recipes = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(heading("Recipes", "Reusable procedure settings keep experiments consistent and easy to change."))
        layout.addWidget(banner("A run stores its own recipe snapshot. Editing a recipe never changes an experiment already recorded."))
        body_card, body = card()
        row = QHBoxLayout()
        row.addWidget(label("Your recipe library", "sectionTitle"), 1)
        row.addWidget(button("Refresh", self.refresh_requested.emit))
        body.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["RECIPE", "CYCLES", "BREAKTHROUGH", "REGENERATION"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(250)
        body.addWidget(self.table)
        self.empty = label("No saved recipes yet. Adjust a recipe in New experiment and choose Save as recipe.", "subtitle", True)
        body.addWidget(self.empty)
        row = QHBoxLayout()
        row.addWidget(label("Load a recipe, edit any setting, then save it under a new name to create a variant.", "subtitle", True), 1)
        self.use_btn = button("Use selected recipe  →", self._use, "primary")
        row.addWidget(self.use_btn)
        body.addLayout(row)
        layout.addWidget(body_card)
        layout.addStretch()

    def set_recipes(self, recipes):
        self.recipes = recipes or []
        self.table.setRowCount(len(self.recipes))
        for n, recipe in enumerate(self.recipes):
            config = recipe.get("config") or {}
            values = (recipe.get("name", "Recipe"), str(config.get("cycles", "—")), f"{config.get('breakthrough_pct', '—')}%", f"{config.get('desorption_target_c', '—')} °C")
            for col, value in enumerate(values):
                self.table.setItem(n, col, QTableWidgetItem(str(value)))
        self.empty.setVisible(not self.recipes)
        self.use_btn.setEnabled(bool(self.recipes))
        if self.recipes:
            self.table.selectRow(0)

    def _use(self):
        row = self.table.currentRow()
        if row >= 0:
            self.use_requested.emit(self.recipes[row].get("config") or {})


class HistoryPage(QWidget):
    load_requested = Signal(str)
    export_requested = Signal(str)
    refresh_requested = Signal()
    import_requested = Signal()

    def __init__(self):
        super().__init__()
        self.runs = []
        self._current = None
        self._path = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(heading("Results", "Open a saved test, inspect the measurements, and export data."))
        body_card, body = card()
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a test by notebook, sample, date, or outcome…")
        self.search.textChanged.connect(self._render)
        row.addWidget(self.search, 1)
        self.mode_filter = QComboBox()
        self.mode_filter.addItem("All data", "all")
        self.mode_filter.addItem("Real apparatus only", "hardware")
        self.mode_filter.setAccessibleName("Results data source filter")
        self.mode_filter.currentIndexChanged.connect(self._render)
        row.addWidget(self.mode_filter)
        self.import_btn = button("Import completed test", self.import_requested.emit)
        row.addWidget(self.import_btn)
        row.addWidget(button("Refresh", self.refresh_requested.emit))
        body.addLayout(row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["TEST ID", "SAMPLE", "STARTED (LOCAL)", "MODE", "OUTCOME", "TEST"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(200)
        self.table.setMaximumHeight(290)
        self.table.itemSelectionChanged.connect(self._select)
        body.addWidget(self.table)
        self.empty = label("Your saved tests will appear here. Simulation measurements are clearly labeled.", "subtitle", True)
        body.addWidget(self.empty)
        layout.addWidget(body_card)
        self.detail_card, detail = card()
        row = QHBoxLayout()
        self.detail_title = label("Select a test", "sectionTitle")
        row.addWidget(self.detail_title, 1)
        self.export_btn = button("Export CSV", lambda: self.export_requested.emit(self._current))
        self.folder_btn = button("Open folder", self._open_folder)
        self.overlay_toggle = button("Overlay cycles")
        self.overlay_toggle.setCheckable(True)
        self.overlay_toggle.toggled.connect(self._toggle_overlay)
        row.addWidget(self.overlay_toggle)
        row.addWidget(self.export_btn)
        row.addWidget(self.folder_btn)
        detail.addLayout(row)
        self.metadata = label("Choose a test above to see its settings and recorded measurements.", "subtitle", True)
        detail.addWidget(self.metadata)
        self.summary = label("", None, True)
        detail.addWidget(self.summary)
        self.cycle_table = QTableWidget(0, 5)
        self.cycle_table.setHorizontalHeaderLabels(["CYCLE", "INLET CO₂", "THRESHOLD", "BREAKTHROUGH TIME", "OUTCOME"])
        self.cycle_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.cycle_table.verticalHeader().hide()
        self.cycle_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cycle_table.setMaximumHeight(200)
        self.cycle_table.setMinimumHeight(100)
        self.cycle_table.hide()
        detail.addWidget(self.cycle_table)
        self.plots = RunPlots(compact=True)
        self.plots.empty.setText("Select a saved experiment to inspect its signals.")
        self.plots.follow.setChecked(False)
        detail.addWidget(self.plots)
        self.overlay = CycleOverlay()
        self.overlay.hide()
        detail.addWidget(self.overlay)
        layout.addWidget(self.detail_card)
        layout.addStretch()
        self.export_btn.setEnabled(False)
        self.folder_btn.setEnabled(False)
        self.overlay_toggle.setEnabled(False)

    def set_runs(self, runs):
        self.runs = runs or []
        self._render()

    def _render(self):
        query = self.search.text().strip().lower()
        mode = self.mode_filter.currentData()
        self.filtered = [r for r in self.runs if (mode == "all" or r.get("mode") == mode)
                         and query in " ".join(str(v) for v in r.values()).lower()]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.filtered))
        for n, run in enumerate(self.filtered):
            stamp = QDateTime.fromString(str(run.get("started_at", "")), Qt.DateFormat.ISODateWithMs)
            started = stamp.toLocalTime().toString("yyyy-MM-dd HH:mm:ss t") if stamp.isValid() else str(run.get("started_at", "—"))
            values = (run.get("notebook_id", run.get("id", "—")), run.get("sample_id", "—"), started, str(run.get("mode", "unknown")).upper(), str(run.get("status", "—")).replace("_", " "), str(run.get("test_type", "Legacy cycles")).capitalize())
            for col, value in enumerate(values):
                self.table.setItem(n, col, QTableWidgetItem(str(value)))
        self.table.blockSignals(False)
        self.empty.setVisible(not self.filtered)

    def _select(self):
        row = self.table.currentRow()
        if row >= 0 and row < len(self.filtered):
            self._current = str(self.filtered[row].get("id"))
            self.load_requested.emit(self._current)

    def show_run(self, run):
        meta = run.get("metadata") or {}
        config = meta.get("config", meta.get("recipe", meta)) or {}
        self._path = meta.get("path", run.get("path"))
        if not self._path:
            self._path = next((r.get("path") for r in self.runs if str(r.get("id")) == self._current), None)
        name = config.get("notebook_id", meta.get("notebook_id", self._current))
        mode = str(meta.get("mode", "")).upper()
        self.detail_title.setText(f"{name}  ·  {mode}" if mode else str(name))
        self.metadata.setText(f"Sample {config.get('sample_id', '—')}   ·   {config.get('sample_mass_g', '—')} g   ·   Operator {config.get('operator') or 'not recorded'}\n{config.get('notes') or 'No notes recorded.'}")
        summary = run.get("summary") or {}
        if config.get("test_type") and not config.get("automatic_cutoff_enabled", False):
            details = [TESTS.get(config["test_type"], ("Test",))[0]]
            if summary.get("measurement_duration_s") is not None:
                details.append("Measurement time " + duration(summary["measurement_duration_s"]))
            if summary.get("ended_by") == "operator":
                details.append("Ended by operator")
            self.summary.setText("   ·   ".join(details))
        else:
            self.summary.setText("   ·   ".join(f"{str(k).replace('_', ' ').capitalize()}: {v}" for k, v in summary.items() if not isinstance(v, (list, dict)))[:1500])
        cycles = summary.get("cycles") or []
        automatic = bool(config.get("automatic_cutoff_enabled")) or "test_type" not in config
        threshold_value = config.get("breakthrough_pct")
        self.overlay_toggle.setVisible(len(cycles) > 1 and automatic)
        if not automatic or len(cycles) <= 1:
            self.overlay_toggle.setChecked(False)
        self.plots.ratio.setVisible(automatic)
        if not automatic:
            self.plots.ratio.setChecked(False)
        self.cycle_table.setRowCount(len(cycles))
        for idx, cycle in enumerate(cycles):
            cycle_threshold = cycle.get("threshold_ratio")
            values = (str(cycle.get("cycle", "—")), f"{number(cycle.get('inlet_co2_ppm'))} ppm", f"{number(100 * float(cycle_threshold))}%" if cycle_threshold is not None else "Not enabled", duration(cycle.get("breakthrough_time_s")) if cycle.get("breakthrough_time_s") is not None else "Not reached", str(cycle.get("status", "—")).capitalize())
            for col, value in enumerate(values):
                self.cycle_table.setItem(idx, col, QTableWidgetItem(value))
        self.cycle_table.setVisible(bool(cycles) and automatic)
        samples = run.get("samples") or []
        sample_count = run.get("_sample_count", len(samples))
        self.plots.caption.setText(f"Display uses {len(samples):,} representative points from {sample_count:,} saved measurements. Full-resolution data are retained in the database and CSV exports." if sample_count > len(samples) else "All stages are shown. Gaps indicate missing readings. Drag to zoom; right-click a plot to reset.")
        baseline = next((r.get("baseline_ppm") for r in reversed(samples) if r.get("baseline_ppm")), None)
        threshold = float(threshold_value) / 100 if automatic and threshold_value is not None else None
        self.plots.update_data(samples, baseline, threshold, run.get("events"), self._current)
        self.overlay.set_rows(samples)
        if samples:
            self.plots.plot_co2.enableAutoRange()
        self.export_btn.setEnabled(bool(self._current))
        self.folder_btn.setEnabled(bool(self._path))
        self.overlay_toggle.setEnabled(bool(samples))

    def _toggle_overlay(self, enabled):
        self.overlay_toggle.setText("Show full run" if enabled else "Overlay cycles")
        self.plots.setVisible(not enabled)
        self.overlay.setVisible(enabled)

    def _open_folder(self):
        if self._path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._path).resolve())))


class ManualPage(QWidget):
    action_requested = Signal(str, dict)

    def __init__(self):
        super().__init__()
        self.controls = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(heading("Manual controls", "Named operations for supervised setup and commissioning between experiments."))
        layout.addWidget(banner("Output readback reports electrical command state. It does not prove physical valve position or gas flow. Check actual instrument readings and the apparatus.", True))
        self.availability = label("Connect the simulator to explore manual controls.", "subtitle", True)
        layout.addWidget(self.availability)
        grid = QGridLayout()
        grid.setSpacing(16)
        routes, body = card()
        body.addWidget(label("Column routing", "sectionTitle"))
        body.addWidget(label("Select a commissioned route.", "subtitle"))
        for title, name in (("Bypass column", "bypass"), ("Through column", "column"), ("N₂ purge route", "purge"), ("Stop feed & vent", "vent"), ("Set idle routing", "closed")):
            control = button(title, lambda checked=False, route=name: self.action_requested.emit("safe_state", {}) if route == "vent" else self.action_requested.emit("route", {"name": route}))
            control.setToolTip("Turns off heat and source gas, then applies the reviewed vent state." if name == "vent" else "Changes routing only. Gas delivery and heater controls are separate.")
            self.controls.append(control)
            body.addWidget(control)
        grid.addWidget(routes, 0, 0)
        gas, body = card()
        body.addWidget(label("Gas delivery · 146C", "sectionTitle"))
        body.addWidget(label("Use the configured gas selector commands.", "subtitle"))
        for title, name in (("Select Air", "air"), ("Select N₂", "n2"), ("Gas off", "off")):
            control = button(title, lambda checked=False, gas_name=name: self.action_requested.emit("gas", {"name": gas_name}))
            self.controls.append(control)
            body.addWidget(control)
        body.addStretch()
        grid.addWidget(gas, 0, 1)
        heat, body = card()
        body.addWidget(label("Column heater", "sectionTitle"))
        body.addWidget(label("Temperature regulation stays on the Core2.", "subtitle", True))
        self.target = QDoubleSpinBox()
        self.target.setRange(20, 175)
        self.target.setSuffix(" °C")
        self.target.setValue(150)
        body.addWidget(self.target)
        self.controls.append(self.target)
        for title, callback in (("Apply heater target", lambda: self.action_requested.emit("heater", {"target_c": self.target.value()})), ("Heater off", lambda: self.action_requested.emit("heater", {"target_c": None}))):
            control = button(title, callback)
            self.controls.append(control)
            body.addWidget(control)
        grid.addWidget(heat, 1, 0)
        readback, body = card()
        body.addWidget(label("Reported output state", "sectionTitle"))
        self.outputs = label("No output state received.", None, True)
        self.outputs.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.outputs)
        body.addStretch()
        self.safe_btn = button("Request safe state", lambda: self.action_requested.emit("safe_state", {}), "danger")
        self.controls.append(self.safe_btn)
        body.addWidget(self.safe_btn)
        grid.addWidget(readback, 1, 1)
        layout.addLayout(grid)
        layout.addStretch()

    def update_state(self, snapshot):
        connected_idle = bool(snapshot.get("connected")) and not snapshot.get("running")
        blockers = snapshot.get("blockers") or []
        allowed = connected_idle and not (snapshot.get("mode") != "simulation" and blockers)
        for control in self.controls:
            control.setEnabled(allowed)
        self.safe_btn.setEnabled(connected_idle)
        self.availability.setText("An automated run owns these controls. Use Stop run on the live page to finish safely." if snapshot.get("running") else "Manual commands apply immediately to the simulator." if allowed and snapshot.get("mode") == "simulation" else "Commands use your commissioned hardware profile." if allowed else "Hardware commands unavailable: " + "; ".join(str(b) for b in blockers) if connected_idle else "Connect the simulator to explore manual controls.")
        outputs = snapshot.get("outputs") or {}
        self.outputs.setText("\n".join(f"{str(k).replace('_', ' ').capitalize()}: {v}" for k, v in outputs.items()) if outputs else "No output state received.")
