"""Operator connection setup. USB discovery never opens an instrument port.

The page owns only a draft and passive USB inventory. Profile persistence and
instrument connection remain explicit requests handled by the main window.
"""

import json
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from .widgets import QComboBox, banner, button, card, heading, label
from .workers import HistoryWorker
from cct.contracts import required_devices


ROLES = ("core2", "licor", "alicat", "calibrator")
FALLBACK_GUIDES = {
    "core2": ("Core2 controller", "Column temperature, ambient sensors, and local control"),
    "licor": ("LI-COR LI-840A", "CO₂ measurements"),
    "alicat": ("Alicat B-500C", "Gas flow measurement"),
    "calibrator": ("Thermo 146C", "Air / N₂ selection and gas delivery"),
}


def _enumerate_usb():
    from cct.drivers.discovery import enumerate_adapters
    return enumerate_adapters()


def _resolve(profile, adapters):
    from cct.drivers.discovery import resolve_profile
    return resolve_profile(profile, adapters)


def _read_profile(path):
    file_path = Path(path)
    if file_path.stat().st_size > 1024 * 1024:
        raise ValueError("This settings file is too large. Choose an apparatus profile smaller than 1 MB.")
    value = json.loads(file_path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_fields)
    if not isinstance(value, dict) or not isinstance(value.get("devices"), dict):
        raise ValueError("This file does not contain apparatus settings. Choose an apparatus profile with device settings.")
    return value


def _unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate apparatus setting or device role: " + str(key) + ". Correct the imported profile before pairing cables.")
        value[key] = item
    return value


class ConnectionSettingsPage(QWidget):
    """A service-independent simulation / physical connection wizard."""

    connect_requested = Signal(str, str)
    disconnect_requested = Signal()
    save_pairing_requested = Signal(dict)
    load_starter_requested = Signal()
    diagnostic_requested = Signal()

    def __init__(self, discovery_callback=None, resolver_callback=None):
        super().__init__()
        self.profile_path = ""
        self._profile = None
        self._draft = None
        self._adapters = []
        self._scanned = False
        self._resolution = {}
        self._dirty = False
        self._connected = False
        self._running = False
        self._busy = False
        self._profile_generation = 0
        self._worker_key = 0
        self._workers = {}
        self._receivers = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._discover = discovery_callback or _enumerate_usb
        self._resolver = resolver_callback or _resolve
        self._guides = self._load_guides()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        layout.addWidget(heading("Connection options", "Import reviewed settings, save USB pairing, and connect the apparatus."))
        source, body = card()
        body.addWidget(label("CONNECTION SOURCE", "eyebrow"))
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.setAccessibleName("Connection source")
        self.mode.addItem("My apparatus · USB hub", "hardware")
        self.mode.hide()
        self.connect_btn = button("Connect simulator", self._connect, "primary")
        self.disconnect_btn = button("Disconnect", self.disconnect_requested.emit)
        row.addWidget(self.connect_btn)
        row.addWidget(self.disconnect_btn)
        body.addLayout(row)
        self.manual_calibrator = QCheckBox("146C operated manually — no 146C USB connection or commands")
        self.manual_calibrator.setChecked(True)
        self.manual_calibrator.toggled.connect(self._manual_changed)
        body.addWidget(self.manual_calibrator)
        self.manual_instruments = {}
        for role, title in (("alicat", "Alicat"), ("licor", "LI-COR")):
            control = QCheckBox(title + " operated manually — no USB commands or readings")
            control.toggled.connect(lambda checked, name=role: self._manual_sensor_changed(name, checked))
            self.manual_instruments[role] = control
            body.addWidget(control)
        self.manual_note = label("Set the 146C to Local/manual at its front panel. Manual instruments have no app commands or readings. Core valves and heating remain controlled through the S3.", "subtitle", True)
        body.addWidget(self.manual_note)
        self.connection_note = label("Simulation does not open USB or serial connections.", "subtitle", True)
        body.addWidget(self.connection_note)
        self.diagnostic_btn = button("Save connection report", self.diagnostic_requested.emit)
        self.diagnostic_btn.setToolTip("Creates a connection troubleshooting report. It excludes sample notes, measurements, logs, and profile identifiers.")
        diagnostic_row = QHBoxLayout()
        diagnostic_row.addWidget(self.diagnostic_btn)
        diagnostic_row.addStretch()
        body.addLayout(diagnostic_row)
        layout.addWidget(source)

        self.hardware = QWidget()
        hardware_layout = QVBoxLayout(self.hardware)
        hardware_layout.setContentsMargins(0, 0, 0, 0)
        hardware_layout.setSpacing(16)
        hardware_layout.addWidget(banner("USB adapter identified ≠ instrument verified. Scanning only lists adapters. The instrument is checked after you explicitly choose Connect apparatus.", True))
        settings, body = card()
        body.addWidget(label("1   LOAD YOUR APPARATUS SETTINGS", "eyebrow"))
        body.addWidget(label("An apparatus profile stores wiring, instrument settings, and reviewed shutdown behavior. Cable pairing is a separate step.", "subtitle", True))
        row = QHBoxLayout()
        self.profile_btn = button("Import apparatus profile…", self._choose_profile)
        self.starter_btn = button("Create starter settings", self.load_starter_requested.emit)
        row.addWidget(self.profile_btn)
        row.addWidget(self.starter_btn)
        row.addStretch()
        body.addLayout(row)
        self.profile_label = label("No apparatus settings loaded.", None, True)
        self.profile_state = label("A starter profile needs review before it can control an experiment.", "subtitle", True)
        body.addWidget(self.profile_label)
        body.addWidget(self.profile_state)
        hardware_layout.addWidget(settings)

        scan, body = card()
        body.addWidget(label("2   FIND YOUR USB ADAPTERS", "eyebrow"))
        row = QHBoxLayout()
        self.scan_btn = button("Scan USB adapters", self.scan_adapters)
        row.addWidget(self.scan_btn)
        self.scan_status = label("No scan has been run. Connect your powered instruments and USB hub, then scan.", "subtitle", True)
        row.addWidget(self.scan_status, 1)
        body.addLayout(row)
        body.addWidget(label("Scanning is passive: it never opens a port, probes an instrument, changes valves, or turns on a heater.", "subtitle", True))
        hardware_layout.addWidget(scan)
        hardware_layout.addWidget(label("3   MATCH EACH LABELED CABLE", "eyebrow"))
        grid = QGridLayout()
        grid.setSpacing(16)
        self.adapter_selectors = {}
        self.device_labels = {}
        self.device_cards = {}
        self.instruction_buttons = {}
        self.instructions = {}
        for index, role in enumerate(ROLES):
            guide = self._guides[role]
            device, body = card()
            self.device_cards[role] = device
            body.addWidget(label(guide["title"], "sectionTitle"))
            body.addWidget(label(guide["summary"], "subtitle", True))
            selector = QComboBox()
            selector.setAccessibleName(guide["title"] + " USB adapter")
            selector.setMinimumContentsLength(20)
            selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            selector.addItem("Choose a USB adapter…", None)
            selector.currentIndexChanged.connect(lambda selected, device_role=role: self._binding_changed(device_role))
            body.addWidget(selector)
            self.adapter_selectors[role] = selector
            matching = label("Not paired · load settings and scan USB adapters.", None, True)
            matching.setStyleSheet("color: #94600e;")
            verified = label("Instrument not verified. Connect apparatus to check communication.", "subtitle", True)
            body.addWidget(matching)
            body.addWidget(verified)
            self.device_labels[role] = (matching, verified)
            toggle = button("+  Wiring and setup instructions")
            toggle.setCheckable(True)
            instructions = self._instruction_widget(guide)
            instructions.hide()
            toggle.toggled.connect(instructions.setVisible)
            toggle.toggled.connect(lambda open_now, control=toggle: control.setText(("−" if open_now else "+") + "  Wiring and setup instructions"))
            body.addWidget(toggle)
            body.addWidget(instructions)
            self.instruction_buttons[role] = toggle
            self.instructions[role] = instructions
            grid.addWidget(device, index // 2, index % 2)
        hardware_layout.addLayout(grid)

        pairing, body = card()
        body.addWidget(label("4   CHECK THE LABELS & SAVE PAIRING", "eyebrow"))
        self.labels_confirmed = QCheckBox("I checked the cable labels and selected the correct adapter for every instrument.")
        self.labels_confirmed.setAccessibleName("Cable labels confirmed")
        self.labels_confirmed.toggled.connect(self._update_controls)
        body.addWidget(self.labels_confirmed)
        body.addWidget(label("A different cable pairing requires a new review. Saving pairing does not commission the apparatus or verify its plumbing.", "subtitle", True))
        row = QHBoxLayout()
        self.save_btn = button("Save pairing as new profile", self._save_pairing)
        row.addWidget(self.save_btn)
        self.connect_after_pairing_btn = button("Connect apparatus", self._connect, "primary")
        row.addWidget(self.connect_after_pairing_btn)
        row.addStretch()
        body.addLayout(row)
        self.pairing_note = label("Select one unique adapter for each required instrument. Manual 146C needs no port.", "subtitle", True)
        body.addWidget(self.pairing_note)
        hardware_layout.addWidget(pairing)
        self.blockers = label("", "error", True)
        hardware_layout.addWidget(self.blockers)
        self.error = label("", "error", True)
        hardware_layout.addWidget(self.error)
        layout.addWidget(self.hardware)

        layout.addStretch()
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

    @staticmethod
    def _load_guides():
        try:
            from cct.device_guides import DEVICE_GUIDES
        except ImportError:
            DEVICE_GUIDES = {}
        guides = {}
        for role, (title, summary) in FALLBACK_GUIDES.items():
            guide = DEVICE_GUIDES.get(role) or {}
            guides[role] = {"title": guide.get("title", title), "summary": guide.get("summary", summary), "steps": guide.get("steps", []), "notes": guide.get("notes", ""), "reading": guide.get("reading", ""), "controls": guide.get("controls", "")}
        return guides

    @staticmethod
    def _instruction_widget(guide):
        widget = QWidget()
        body = QVBoxLayout(widget)
        body.setContentsMargins(2, 4, 2, 4)
        body.setSpacing(8)
        for index, step in enumerate(guide.get("steps") or []):
            body.addWidget(label(f"{index + 1}. {step}", "subtitle", True))
        for key, prefix in (("notes", "Check: "), ("reading", "Expected readings: "), ("controls", "Computer controls: ")):
            if guide.get(key):
                body.addWidget(label(prefix + str(guide[key]), "subtitle", True))
        if body.count() == 0:
            body.addWidget(label("Use the instrument wiring instructions supplied with your apparatus profile. Adapter identity alone does not verify the connected instrument.", "subtitle", True))
        return widget

    def _mode_changed(self):
        hardware = self.mode.currentData() == "hardware"
        self.manual_calibrator.setVisible(hardware)
        self.manual_note.setVisible(hardware)
        for role, device in self.device_cards.items():
            device.setVisible(role in self._required_roles())
        self.hardware.setVisible(hardware)
        self.connect_btn.setText("Connect apparatus" if hardware else "Connect simulator")
        self.connection_note.setText("Load settings, match the labeled cables, then explicitly connect. A reviewed profile is required for experiment control." if hardware else "Simulation does not open USB or serial connections.")
        self._update_controls()

    def _choose_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import apparatus settings", "", "Apparatus profile (*.json)")
        if path:
            self.set_profile(path)

    def set_profile(self, profile, path=None):
        """Load a new draft without scanning, connecting, or writing any file.

        Accept an imported/saved path or the unsaved starter dictionary returned
        by the service. Path reads happen off the GUI thread.
        """
        if self._connected or self._running:
            self.error.setText("Disconnect the apparatus before changing its settings.")
            return False
        self._profile_generation += 1
        generation = self._profile_generation
        if isinstance(profile, (str, Path)):
            profile_path = str(Path(profile).resolve())
            self._background(_read_profile, (profile_path,), lambda value: self._accept_profile(value, profile_path, generation))
        elif isinstance(profile, dict):
            self._accept_profile(deepcopy(profile), str(path or ""), generation)
        else:
            self.error.setText("Choose an apparatus profile or create starter settings.")
            return False
        return True

    def _accept_profile(self, profile, path, generation):
        if generation != self._profile_generation:
            return
        if not isinstance(profile.get("devices"), dict):
            self.error.setText("The profile needs a device settings section.")
            return
        self._profile = deepcopy(profile)
        self._draft = deepcopy(profile)
        self.manual_calibrator.blockSignals(True)
        self.manual_calibrator.setChecked(profile.get("calibrator_mode", "automatic") == "manual")
        self.manual_calibrator.blockSignals(False)
        for role, control in self.manual_instruments.items():
            control.blockSignals(True)
            control.setChecked(profile.get(role + "_mode") == "manual")
            control.blockSignals(False)
        self.profile_path = path
        self._dirty = False
        self.labels_confirmed.setChecked(False)
        self.error.clear()
        self.profile_label.setText(Path(path).name if path else "Starter settings · not saved yet")
        self.profile_label.setToolTip(path)
        self._resolve_pairing()

    def _manual_changed(self, checked):
        if self._connected or self._running:
            return
        if self._draft is not None:
            self._draft["calibrator_mode"] = "manual" if checked else "automatic"
            self._draft["commissioned"] = False
            self._dirty = True
            self.labels_confirmed.setChecked(False)
            self._resolve_pairing()

    def _required_roles(self):
        return required_devices(self._draft or {"calibrator_mode": "manual" if self.manual_calibrator.isChecked() else "automatic"})

    def _manual_sensor_changed(self, role, checked):
        if self._connected or self._running or self._draft is None:
            return
        self._draft[role + "_mode"] = "manual" if checked else "automatic"
        self._draft["commissioned"] = False
        self._dirty = True
        self.labels_confirmed.setChecked(False)
        self._resolve_pairing()

    def scan_adapters(self):
        if self._busy or self._connected or self._running:
            return
        self.scan_status.setText("Looking for USB adapters… No instrument ports are opened.")
        self.error.clear()
        self._background(self._discover, (), self._receive_adapters, self._scan_error)

    def _receive_adapters(self, adapters):
        if not isinstance(adapters, list):
            raise ValueError("USB scan returned an invalid adapter list.")
        self._adapters = [dict(adapter) for adapter in adapters if isinstance(adapter, dict) and adapter.get("port")]
        self._scanned = True
        self.scan_status.setText(f"Found {len(self._adapters)} USB / serial adapter{'s' if len(self._adapters) != 1 else ''}. Instrument communication has not been checked." if self._adapters else "No adapters found. Check the USB data cable, hub connection, and adapter drivers, then scan again.")
        self._resolve_pairing()

    def _scan_error(self, message):
        self._adapters = []
        self._scanned = False
        self.scan_status.setText("USB scan could not finish. Check the connection and try again.")
        self.error.setText(str(message))
        self._resolve_pairing()

    def _resolve_pairing(self):
        if self._draft is not None:
            try:
                self._resolution = self._resolver(self._draft, self._adapters)
                self._draft = deepcopy(self._resolution.get("profile", self._draft))
            except Exception as exc:
                self.error.setText("Could not match the adapters: " + str(exc))
                self._resolution = {}
        else:
            self._resolution = {}
        self._render_adapters()
        self._update_controls()

    @staticmethod
    def _adapter_text(adapter):
        serial = adapter.get("usb_serial")
        description = adapter.get("description") or "USB serial adapter"
        return f"{adapter.get('port', '?')} · {description} · {'serial ' + str(serial) if serial else 'no USB serial number'}"

    def _render_adapters(self):
        devices = self._resolution.get("devices") or {}
        for role in ROLES:
            self.device_cards[role].setVisible(role in self._required_roles())
            combo = self.adapter_selectors[role]
            info = devices.get(role) or {}
            current_port = info.get("port")
            expected_serial = ((self._draft or {}).get("devices", {}).get(role) or {}).get("expected_usb_serial")
            serial_matches = [adapter for adapter in self._adapters if adapter.get("usb_serial") and adapter.get("usb_serial") == expected_serial]
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Choose a USB adapter…", None)
            selected = 0
            for adapter in self._adapters:
                combo.addItem(self._adapter_text(adapter), adapter)
                if (current_port and adapter.get("port") == current_port) or (not current_port and len(serial_matches) == 1 and adapter.get("usb_serial") == expected_serial):
                    selected = combo.count() - 1
            combo.setCurrentIndex(selected)
            combo.blockSignals(False)
            state = info.get("state", "unpaired")
            caption = {"matched": "USB adapter matched · instrument not yet verified", "missing": "Saved adapter is missing", "ambiguous": "Adapter identity is ambiguous · choose distinct labeled cables", "unpaired": "No saved cable pairing"}.get(state, "Not paired")
            if not self._scanned:
                caption = "Not scanned · scan USB adapters to check this cable"
            self.device_labels[role][0].setText(caption)
            self.device_labels[role][0].setToolTip(str(info.get("message", "")))
        self._update_profile_note()

    def _binding_changed(self, role):
        if self._connected or self._running or self._draft is None:
            return
        adapter = self.adapter_selectors[role].currentData()
        device = self._draft.setdefault("devices", {}).setdefault(role, {})
        device.update(port=adapter.get("port", "") if adapter else "", expected_usb_serial=adapter.get("usb_serial") if adapter else None)
        for source, target in (("vid", "expected_usb_vid"), ("pid", "expected_usb_pid")):
            if adapter and adapter.get(source) is not None:
                device[target] = adapter[source]
            else:
                device.pop(target, None)
        self._draft["commissioned"] = False
        self._dirty = True
        self.labels_confirmed.setChecked(False)
        self._resolve_pairing()

    def _update_profile_note(self):
        if self._draft is None:
            return
        if self._dirty:
            self.profile_state.setText("Cable pairing changed. Apparatus review notes are preserved, but this draft is not commissioned. Save a new profile and arrange a new review.")
        elif self._draft.get("commissioned") is True:
            self.profile_state.setText("This imported profile records an apparatus review. USB matching does not independently verify that review or the connected instruments.")
        else:
            self.profile_state.setText("This profile is not commissioned. Connection can check instruments; experiment control stays blocked until the apparatus is reviewed.")

    def _all_paired(self):
        devices = self._resolution.get("devices") or {}
        return self._scanned and all((devices.get(role) or {}).get("state") == "matched" and (devices.get(role) or {}).get("usb_serial") for role in self._required_roles())

    def _save_pairing(self):
        if self._connected or self._running or not self._draft or not self._all_paired() or not self.labels_confirmed.isChecked():
            return
        # Persistence is delegated. The window creates a new unique local file;
        # this page never opens the imported profile for writing.
        self.save_pairing_requested.emit(deepcopy(self._draft))

    def _connect(self):
        if self._connected or self._running or self._busy:
            return
        mode = self.mode.currentData()
        if mode == "hardware" and (not self.profile_path or self._dirty or not self._all_paired()):
            self.error.setText("Load settings, scan adapters, and save any new cable pairing before connecting.")
            return
        self.connect_requested.emit(mode, self.profile_path if mode == "hardware" else "")

    def _update_controls(self, *_):
        locked = self._connected or self._running or self._busy
        hardware = self.mode.currentData() == "hardware"
        self.mode.setEnabled(not locked)
        self.mode.hide()
        self.manual_calibrator.setEnabled(not locked)
        for control in self.manual_instruments.values():
            control.setEnabled(not locked)
        for control in (self.profile_btn, self.starter_btn, self.scan_btn):
            control.setEnabled(not locked)
        for role, combo in self.adapter_selectors.items():
            combo.setEnabled(not locked and self._draft is not None and self._scanned and role in self._required_roles())
        self.labels_confirmed.setEnabled(not locked and self._all_paired())
        self.save_btn.setEnabled(not locked and self._draft is not None and self._all_paired() and self.labels_confirmed.isChecked())
        self.connect_btn.setEnabled(not locked and (not hardware or bool(self.profile_path and not self._dirty and self._all_paired())))
        self.connect_after_pairing_btn.setEnabled(not locked and hardware and bool(self.profile_path and not self._dirty and self._all_paired()))
        self.disconnect_btn.setEnabled(self._connected and not self._running and not self._busy)
        if self._dirty:
            self.pairing_note.setText("Unsaved pairing · save a new profile before connecting. The imported file remains unchanged.")
        elif self._draft is not None and self._all_paired():
            self.pairing_note.setText(f"All {len(self._required_roles())} required USB adapters are identified. Check labels before saving. Instruments are checked only after Connect.")
        elif self._scanned:
            self.pairing_note.setText("Each required instrument needs its own unique USB adapter. The 146C is not required when operated manually.")

    def update_state(self, snapshot):
        self._connected = bool(snapshot.get("connected"))
        self._running = bool(snapshot.get("running"))
        if self._connected:
            selected_mode = self.mode.findData(snapshot.get("mode", "simulation"))
            if selected_mode >= 0 and selected_mode != self.mode.currentIndex():
                self.mode.setCurrentIndex(selected_mode)
        for role in ROLES:
            info = (snapshot.get("devices") or {}).get(role) or {}
            status = self.device_labels[role][1]
            if not self._connected:
                status.setText("Instrument not verified. Choose Connect apparatus to check communication.")
                status.setStyleSheet("color: #6b7e89;")
            elif info.get("connected") and info.get("fresh"):
                status.setText("Simulated instrument ready" if snapshot.get("mode") == "simulation" else "Instrument communicating · " + str(info.get("message") or "fresh response received"))
                status.setStyleSheet("color: #087f83;")
            elif info.get("connected"):
                status.setText("Instrument response is stale · " + str(info.get("message") or "check communication"))
                status.setStyleSheet("color: #94600e;")
            else:
                status.setText(str(info.get("message") or "Instrument not verified. Connect apparatus to check communication."))
                status.setStyleSheet("color: #6b7e89;")
        self.blockers.setText("\n".join(str(item) for item in (snapshot.get("blockers") or [])) if self._connected and snapshot.get("mode") == "hardware" else "")
        self._update_controls()

    def _background(self, operation, args, receiver, error_receiver=None):
        self._worker_key += 1
        key = self._worker_key
        self._busy = True
        worker = HistoryWorker(key, operation, *args)
        self._workers[key] = worker
        self._receivers[key] = (receiver, error_receiver or self._operation_error)
        worker.signals.result.connect(self._worker_result)
        worker.signals.error.connect(self._worker_error)
        worker.signals.finished.connect(self._worker_finished)
        self._update_controls()
        self._pool.start(worker)

    def _operation_error(self, message):
        self.error.setText(str(message))

    @Slot(int, object)
    def _worker_result(self, key, result):
        receivers = self._receivers.get(key)
        if receivers:
            try:
                receivers[0](result)
            except Exception as exc:
                receivers[1](str(exc))

    @Slot(int, str)
    def _worker_error(self, key, message):
        receivers = self._receivers.get(key)
        if receivers:
            receivers[1](message)

    @Slot(int)
    def _worker_finished(self, key):
        self._workers.pop(key, None)
        self._receivers.pop(key, None)
        self._busy = bool(self._workers)
        self._update_controls()

    def wait_for_done(self, timeout_ms=5000):
        """Wait only during application teardown, after the GUI event loop ends.

        Scanning is passive, so shutting down cannot leave an instrument command
        pending. The bounded pool has at most one filesystem / enumeration job.
        """
        return self._pool.waitForDone(max(0, int(timeout_ms)))


class DevicesPage(ConnectionSettingsPage):
    """Only instrument names and USB selectors; setup lives in a separate window."""

    MANUAL_146C = "manual-146c"
    MANUAL_VALUES = {"calibrator": MANUAL_146C, "alicat": "manual-alicat", "licor": "manual-licor"}

    def __init__(self, discovery_callback=None, resolver_callback=None):
        super().__init__(discovery_callback, resolver_callback)
        # Transfer the existing reviewed setup workflow to a separate surface.
        # Its state and service signals remain shared with the simple selectors.
        self.settings = QWidget(self)
        self.settings.setLayout(self.layout())
        self.settings.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        rows = QGridLayout()
        rows.setHorizontalSpacing(24)
        rows.setVerticalSpacing(20)
        self.row_labels = {}
        names = {"core2": "Core2", "licor": "LI-COR LI-840A", "alicat": "Alicat B-500C", "calibrator": "Thermo 146C"}
        for index, role in enumerate(ROLES):
            title = label(names[role], "sectionTitle")
            self.row_labels[role] = title
            combo = self.adapter_selectors[role]
            combo.setParent(self)
            title.setBuddy(combo)
            rows.addWidget(title, index, 0)
            rows.addWidget(combo, index, 1)
            combo.show()
        rows.setColumnStretch(1, 1)
        layout.addLayout(rows)
        layout.addStretch()
        self._inventory_timer = QTimer(self)
        self._inventory_timer.setInterval(3000)
        self._inventory_timer.timeout.connect(self.refresh_inventory)
        self._render_adapters()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_inventory()
        self._inventory_timer.start()

    def hideEvent(self, event):
        self._inventory_timer.stop()
        super().hideEvent(event)

    def refresh_inventory(self):
        # This lists OS adapters only. Never open ports or connect on selection.
        if self._busy or self._connected or self._running:
            return
        if any(combo.view().isVisible() for combo in self.adapter_selectors.values()):
            return
        self.scan_adapters()

    def _render_adapters(self):
        super()._render_adapters()
        for role, combo in self.adapter_selectors.items():
            combo.setItemText(0, "Select USB device…" if self._adapters else "No USB devices found")
        for role, value in self.MANUAL_VALUES.items():
            combo = self.adapter_selectors[role]
            combo.blockSignals(True)
            text = "Operated manually — no USB control" if role == "calibrator" else "Operated manually — no app readings"
            combo.insertItem(1, text, value)
            control = self.manual_calibrator if role == "calibrator" else self.manual_instruments[role]
            if control.isChecked():
                combo.setCurrentIndex(1)
            combo.blockSignals(False)

    def _binding_changed(self, role):
        if self._connected or self._running or self._draft is None:
            return
        selected = self.adapter_selectors[role].currentData()
        if role in self.MANUAL_VALUES:
            control = self.manual_calibrator if role == "calibrator" else self.manual_instruments[role]
            if selected == self.MANUAL_VALUES[role]:
                control.setChecked(True)
                return
            if isinstance(selected, dict) and control.isChecked():
                control.blockSignals(True)
                control.setChecked(False)
                control.blockSignals(False)
                self._draft[role + "_mode"] = "automatic"
        self.mode.setCurrentIndex(self.mode.findData("hardware"))
        super()._binding_changed(role)

    def _update_controls(self, *_):
        super()._update_controls()
        locked = self._connected or self._running or self._busy
        for combo in self.adapter_selectors.values():
            combo.setEnabled(not locked and self._draft is not None and self._scanned)
