"""Application coordinator. Widgets depend only on the LabService facade."""

import logging
import time

from PySide6.QtCore import QThreadPool, QTimer, Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from .connections import DevicesPage
from .help import HelpDialog
from .pages import HistoryPage, LivePage
from .preflight import PreflightDialog
from .setup import ExperimentPage
from .theme import STYLE
from .widgets import button, label
from .workers import HistoryWorker, load_for_display

LOG = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, service=None):
        super().__init__()
        if service is None:
            from cct.service import LabService
            service = LabService()
        self.service = service
        self._last_tick = time.monotonic()
        self._error_message = ""
        self._snapshot = {}
        self._close_after_stop = False
        self._saved_status_key = None
        self._history_generation = 0
        self._run_generation = 0
        self._workers = {}
        self._worker_receivers = {}
        self._worker_serial = 0
        self._exporting = False
        self._importing = False
        self._worker_pool = QThreadPool(self)
        self._worker_pool.setMaxThreadCount(2)
        self.setWindowTitle("CCT Control · Laboratory tests")
        self.resize(1440, 1000)
        self.setMinimumSize(1080, 660)
        self.setStyleSheet(STYLE)
        surface = QWidget()
        surface.setObjectName("mainSurface")
        layout = QHBoxLayout(surface)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(19, 30, 19, 22)
        side.setSpacing(7)
        side.addWidget(label("CCT CONTROL", "brand"))
        side.addWidget(label("BREAKTHROUGH LABORATORY", "sidebarCaption"))
        side.addSpacing(28)
        self.new_btn = button("Choose a test", lambda: self.show_page(0), "sidePrimary")
        side.addWidget(self.new_btn)
        side.addSpacing(20)
        side.addWidget(label("WORKSPACE", "sidebarCaption"))
        side.addSpacing(4)
        self.nav = []
        for idx, (icon, name) in enumerate((("01", "Tests"), ("02", "Live test"), ("03", "Results"), ("04", "Connections"))):
            control = button(f"{icon}   {name}", lambda checked=False, page=idx: self.show_page(page), "nav")
            control.setCheckable(True)
            control.setAccessibleName(name)
            side.addWidget(control)
            self.nav.append(control)
        side.addStretch()
        self.help_btn = button("Help and device guide", self.show_help, "nav")
        side.addWidget(self.help_btn)
        self.persistent_stop = button("End test", self.stop_run, "danger")
        self.persistent_stop.setToolTip("Request controlled shutdown; recording continues. Ctrl+Shift+S")
        self.persistent_stop.hide()
        side.addWidget(self.persistent_stop)
        side.addSpacing(10)
        self.sidebar_state = label("●   NOT CONNECTED", "sideFoot")
        side.addWidget(self.sidebar_state)
        side.addSpacing(7)
        side.addWidget(label("LOCAL DATA\nYOUR COMPUTER", "sidebarCaption"))
        side.addWidget(label("Computer coordinates.\nCore2 regulates locally.", "sideFoot"))
        layout.addWidget(sidebar)
        workspace = QWidget()
        work = QVBoxLayout(workspace)
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(0)
        top = QWidget()
        top.setObjectName("topbar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(27, 14, 27, 14)
        self.breadcrumb = label("WORKSPACE  /  TESTS", "eyebrow")
        top_layout.addWidget(self.breadcrumb)
        top_layout.addStretch()
        self.mode_pill = label("SIMULATION", "pill")
        self.connection = label("○  Disconnected", "subtitle")
        self.save_state = label("Local storage ready", "subtitle")
        top_layout.addWidget(self.mode_pill)
        top_layout.addSpacing(15)
        top_layout.addWidget(self.connection)
        top_layout.addSpacing(18)
        top_layout.addWidget(self.save_state)
        work.addWidget(top)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        self.notice.setContentsMargins(27, 9, 27, 9)
        self.notice.setStyleSheet("background: #fff5e4; color: #825b20; border-bottom: 1px solid #ebd8b4; font-size: 12px;")
        self.notice.setText("SIMULATION WORKSPACE   ·   Generated data only. Connect the simulator to explore the complete experiment workflow.")
        work.addWidget(self.notice)
        self.error = QLabel()
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setStyleSheet("background: #fff0ec; color: #a23e35; padding: 10px 27px; border-bottom: 1px solid #e8bfb7;")
        self.error.hide()
        work.addWidget(self.error)
        self.stack = QStackedWidget()
        self.live = LivePage()
        self.setup = ExperimentPage()
        self.devices = DevicesPage()
        self.connection_options = button("Connection options", self.show_connection_settings)
        top_layout.addSpacing(15)
        top_layout.addWidget(self.connection_options)
        self._connection_dialog = None
        self.history = HistoryPage()
        self.pages = [self.setup, self.live, self.history, self.devices]
        for page in self.pages:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            wrapper = QWidget()
            wrapper.setObjectName("pageSurface")
            wrapper_layout = QVBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(27, 25, 27, 25)
            wrapper_layout.addWidget(page)
            scroll.setWidget(wrapper)
            self.stack.addWidget(scroll)
        work.addWidget(self.stack, 1)
        layout.addWidget(workspace, 1)
        self.setCentralWidget(surface)
        self.live.connect_requested.connect(self.show_connection_settings)
        self.live.setup_requested.connect(lambda: self.show_page(0))
        self.live.stop_requested.connect(self.stop_run)
        self.live.observation_requested.connect(self.save_observation)
        self.live.core_action_requested.connect(lambda action, value: self.call(self.service.core_action, action, value))
        self.live.connections_requested.connect(lambda: self.show_page(3))
        self.setup.start_requested.connect(self.start_run)
        self.setup.connect_requested.connect(self.show_connection_settings)
        self.setup.connections_requested.connect(lambda: self.show_page(3))
        self.setup.review_requested.connect(self.review_checks)
        self.devices.connect_requested.connect(self.connect_service)
        self.devices.disconnect_requested.connect(lambda: self.call(self.service.disconnect))
        self.devices.load_starter_requested.connect(self.load_starter_profile)
        self.devices.save_pairing_requested.connect(self.save_pairing)
        self.devices.diagnostic_requested.connect(self.connection_report)
        self.history.refresh_requested.connect(self.refresh_history)
        self.history.load_requested.connect(self.load_run)
        self.history.export_requested.connect(self.export_run)
        self.history.import_requested.connect(self.import_test)
        for idx in range(len(self.pages)):
            shortcut = QAction(self)
            shortcut.setShortcut(QKeySequence(f"Ctrl+{idx + 1}"))
            shortcut.triggered.connect(lambda checked=False, page=idx: self.show_page(page))
            self.addAction(shortcut)
        stop_action = QAction(self)
        stop_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        stop_action.triggered.connect(self.stop_run)
        self.addAction(stop_action)
        help_action = QAction(self)
        help_action.setShortcut(QKeySequence("F1"))
        help_action.triggered.connect(self.show_help)
        self.addAction(help_action)
        self.show_page(0)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self.load_starter_profile()
        self.refresh()
        self.timer.start()

    def show_connection_settings(self):
        if self._connection_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Connection options")
            dialog.resize(1000, 800)
            layout = QVBoxLayout(dialog)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(self.devices.settings)
            self.devices.settings.show()
            layout.addWidget(scroll)
            layout.addWidget(button("Close", dialog.close))
            self._connection_dialog = dialog
        # Modeless so End test remains available in the main window.
        self._connection_dialog.show()
        self._connection_dialog.raise_()
        self._connection_dialog.activateWindow()

    def show_page(self, idx):
        self.stack.setCurrentIndex(idx)
        for n, nav in enumerate(self.nav):
            nav.setChecked(n == idx)
        names = ["TESTS", "LIVE TEST", "RESULTS", "CONNECTIONS"]
        self.breadcrumb.setText("WORKSPACE  /  " + names[idx])
        if idx == 2:
            self.refresh_history()

    def call(self, operation, *args, **kwargs):
        try:
            result = operation(*args, **kwargs)
            self._error_message = ""
            self.error.hide()
            self.refresh()
            return result
        except Exception as exc:
            LOG.exception("Application operation failed")
            self.show_error(str(exc))
            return None

    def show_error(self, message):
        self._error_message = message
        self.error.setText("Action needs attention   ·   " + message)
        self.error.show()

    def tick(self):
        now = time.monotonic()
        dt = max(0, now - self._last_tick)
        self._last_tick = now
        try:
            self.service.update(dt)
            self.refresh()
            if self._close_after_stop and not self._snapshot.get("running"):
                self._close_after_stop = False
                self.close()
        except Exception as exc:
            # A persistent fault stays visible without opening repeated modal dialogs.
            if str(exc) != self._error_message:
                LOG.exception("Background acquisition error")
                self.show_error(str(exc))

    def refresh(self):
        snapshot = self.service.snapshot()
        snapshot.setdefault("test_type", self.service.config.get("test_type"))
        snapshot.setdefault("automatic_cutoff_enabled", self.service.config.get("automatic_cutoff_enabled", False))
        self._snapshot = snapshot
        connected, running = bool(snapshot.get("connected")), bool(snapshot.get("running"))
        simulated = snapshot.get("mode", "simulation") == "simulation"
        self.mode_pill.setText("OFFLINE" if not connected else "SIMULATION" if simulated else "HARDWARE")
        self.mode_pill.setStyleSheet("" if simulated else "background: #fff0d5; color: #94600e;")
        needs_setup = connected and not simulated and bool(snapshot.get("blockers"))
        self.connection.setText("△  Needs attention" if needs_setup else "●  Connected" if connected else "○  Disconnected")
        self.save_state.setText(str(snapshot.get("save_status") or "Local storage ready"))
        self.sidebar_state.setText("●   RUNNING" if running else "△   NEEDS ATTENTION" if needs_setup else "●   CONNECTED" if connected else "○   NOT CONNECTED")
        self.new_btn.setEnabled(not running)
        self.persistent_stop.setVisible(running)
        shutting_down = snapshot.get("stage") in {"stopping", "shutdown"}
        self.persistent_stop.setText("Shutting down…" if shutting_down else "End test")
        self.persistent_stop.setEnabled(running and not shutting_down)
        self.notice.setVisible(simulated or bool(snapshot.get("warnings")))
        self.notice.setText("SIMULATION   ·   Generated data only. Saved results are labeled as simulation." if simulated else "   ·   ".join(str(w) for w in snapshot.get("warnings", [])))
        if not connected:
            self.notice.setVisible(True)
            self.notice.setText("OFFLINE   ·   Select USB devices in Connections, then connect using Connection options.")
        if snapshot.get("stage") == "fault":
            self.notice.setVisible(True)
            self.notice.setText("EXPERIMENT FAULT   ·   " + str(snapshot.get("waiting_for") or "Shutdown requested. Review activity and confirm the apparatus state before restarting."))
            self.notice.setStyleSheet("background: #fff0ec; color: #a23e35; border-bottom: 1px solid #e8bfb7; padding: 9px 27px;")
        else:
            self.notice.setStyleSheet("background: #fff5e4; color: #825b20; border-bottom: 1px solid #ebd8b4; padding: 9px 27px;")
        for page in (self.live, self.setup, self.devices):
            page.update_state(snapshot)

    def connect_service(self, mode, profile_path):
        if mode == "hardware" and not profile_path:
            self.show_error("Choose a commissioned hardware profile before connecting.")
            self.show_page(3)
            return
        result = self.call(self.service.connect, mode=mode, profile_path=profile_path or None)
        if self._snapshot.get("connected"):
            self.statusBar().showMessage("Simulator connected. Choose a test when ready." if mode == "simulation" else "Hardware session opened. Review each device in Connections before starting.", 7000)

    def start_run(self, config):
        try:
            errors = self.service.validate_setup(config)
        except Exception as exc:
            errors = [str(exc)]
        self.setup.set_validation(errors)
        if errors:
            return
        if self.service.mode == "hardware":
            report = self.service.preflight_report(config)
            dialog = PreflightDialog(report, config, allow_start=True, parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if report.get("calibrator_mode") == "manual":
                config = dict(config, manual_calibrator_confirmed=True)
            if "alicat" in report.get("manual_devices", []):
                config = dict(config, manual_flow_confirmed=True)
        if self.call(self.service.start, config) is not None or self.service.snapshot().get("running"):
            self.show_page(1)
            self.stack.currentWidget().verticalScrollBar().setValue(0)

    def show_help(self):
        HelpDialog(self).exec()

    def review_checks(self, config):
        try:
            report = self.service.preflight_report(config)
        except Exception as error:
            self.show_error(str(error))
            return
        PreflightDialog(report, config, parent=self).exec()

    def load_starter_profile(self):
        profile = self.call(self.service.new_apparatus_profile)
        if profile is not None:
            self.devices.set_profile(profile)

    def save_pairing(self, profile):
        path = self.call(self.service.save_apparatus_profile, profile)
        if path is not None:
            self.devices.set_profile(path)
            self.statusBar().showMessage("Pairing saved locally. Physical commissioning is separate from adapter identification.", 12000)

    def save_observation(self, text):
        try:
            self.service.add_observation(text)
        except Exception as error:
            self.show_error(str(error))
            return
        self.live.observation.clear()
        self.statusBar().showMessage("Observation saved with the current test time.", 6000)
        self.refresh()

    def connection_report(self):
        self.statusBar().showMessage("Preparing a local connection report without measurements, profiles or personal details…")
        self._background(self.service.export_support_bundle, (),
                         lambda path: self.statusBar().showMessage(f"Connection report saved: {path}", 15000))

    def import_test(self):
        if self._importing:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose a completed test folder containing run.sqlite")
        if not folder:
            return
        self._importing = True
        self.history.import_btn.setEnabled(False)
        self._background(self.service.import_completed_run, (folder,), self._import_complete, self._import_failed)

    def _import_complete(self, run_id):
        self._importing = False
        self.history.import_btn.setEnabled(True)
        self.refresh_history()
        self.statusBar().showMessage("Completed test imported as a new local copy. No equipment was connected.", 12000)

    def _import_failed(self, message):
        self._importing = False
        self.history.import_btn.setEnabled(True)
        self.show_error(message)

    def stop_run(self):
        if not self._snapshot.get("running"):
            return
        self.call(self.service.stop)
        self.statusBar().showMessage("Controlled stop requested. Recording continues through shutdown.", 8000)

    def refresh_history(self):
        self._history_generation += 1
        generation = self._history_generation
        self.history.empty.setText("Reading saved tests…")
        self._background(self.service.list_runs, (), lambda runs: self._receive_runs(generation, runs))

    def _receive_runs(self, generation, runs):
        if generation != self._history_generation:
            return
        self.history.empty.setText("Your tests will appear here. Simulation data is labeled in the saved record.")
        self.history.set_runs(runs)

    def load_run(self, run_id):
        self._run_generation += 1
        generation = self._run_generation
        self.history.detail_title.setText("Loading test…")
        self.history.export_btn.setEnabled(False)
        self._background(load_for_display, (self.service, run_id), lambda run: self._receive_run(generation, run))

    def _receive_run(self, generation, run):
        if generation != self._run_generation:
            return
        self.history.show_run(run)
        self.history.export_btn.setEnabled(not self._exporting)

    def export_run(self, run_id):
        if not run_id or self._exporting:
            return
        self._exporting = True
        self.history.export_btn.setEnabled(False)
        self.statusBar().showMessage("Exporting full-resolution CSV data. Acquisition continues…")
        self._background(self.service.export_run, (run_id,), self._export_complete, self._export_failed)

    def _export_complete(self, path):
        self._exporting = False
        self.history.export_btn.setEnabled(bool(self.history._current))
        self.statusBar().showMessage(f"CSV exports saved to {path}", 15000)

    def _export_failed(self, message):
        self._exporting = False
        self.history.export_btn.setEnabled(bool(self.history._current))
        self.show_error(message)

    def _background(self, operation, args, receiver, error_receiver=None):
        self._worker_serial += 1
        key = self._worker_serial
        worker = HistoryWorker(key, operation, *args)
        self._workers[key] = worker
        self._worker_receivers[key] = (receiver, error_receiver or self.show_error)
        worker.signals.result.connect(self._worker_result)
        worker.signals.error.connect(self._worker_error)
        worker.signals.finished.connect(self._worker_finished)
        self._worker_pool.start(worker)

    @Slot(int, object)
    def _worker_result(self, key, value):
        receiver = self._worker_receivers.get(key)
        if receiver:
            receiver[0](value)

    @Slot(int, str)
    def _worker_error(self, key, message):
        receiver = self._worker_receivers.get(key)
        if receiver:
            receiver[1](message)

    @Slot(int)
    def _worker_finished(self, key):
        self._workers.pop(key, None)
        self._worker_receivers.pop(key, None)

    def closeEvent(self, event: QCloseEvent):
        if self._snapshot.get("running"):
            message = QMessageBox(self)
            message.setWindowTitle("End the test before closing")
            message.setText("A test is still active.")
            message.setInformativeText("Request a controlled stop and keep the app open while the apparatus shuts down. Data recording continues until shutdown completes.")
            finish = message.addButton("Stop safely, then close", QMessageBox.ButtonRole.AcceptRole)
            message.addButton("Keep app open", QMessageBox.ButtonRole.RejectRole)
            message.setDefaultButton(finish)
            message.exec()
            event.ignore()
            if message.clickedButton() is finish:
                self._close_after_stop = True
                self.stop_run()
            return
        self.timer.stop()
        self.devices._inventory_timer.stop()
        if self._connection_dialog is not None:
            self._connection_dialog.close()
        try:
            self.service.disconnect()
        except Exception:
            LOG.exception("Could not disconnect during close")
        event.accept()
