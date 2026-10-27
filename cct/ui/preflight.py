"""Review a plain-language test plan before physical actuation."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from .widgets import banner, button, heading, label


class PreflightDialog(QDialog):
    def __init__(self, report, config, allow_start=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review before starting")
        self.resize(710, 720)
        self.setMinimumSize(560, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.addWidget(heading("Review before starting", "Checking this screen does not open a gas source or start heating."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        content = QVBoxLayout(body)
        plan = report.get("plan") or {}
        content.addWidget(label(f"{plan.get('title', 'Test')}  ·  {config.get('notebook_id', '')}  ·  Sample {config.get('sample_id', '')}", "sectionTitle", True))
        for check in report.get("checks", []):
            ready = check.get("state") == "ready"
            content.addWidget(label(("READY — " if ready else "CHECK — ") + check["title"], "eyebrow"))
            content.addWidget(label(check["detail"], "subtitle", True))
        if report.get("blockers"):
            content.addWidget(banner("\n".join(str(item) for item in report["blockers"]), True))
        content.addWidget(label("WHAT WILL HAPPEN", "eyebrow"))
        content.addWidget(label("\n".join(f"{index}. {step}" for index, step in enumerate(plan.get("steps", []), 1)), None, True))
        for key in ("ending", "shutdown", "safety", "measurement_note"):
            content.addWidget(label(plan.get(key, ""), "subtitle", True))
        content.addWidget(label("Records stay on this computer: " + report.get("data_folder", ""), "subtitle", True))
        content.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        content.addWidget(label("Before physical operation: check the sample details, gas connections, vent destination and physical stop procedure.", "subtitle", True))
        self.confirm = QCheckBox("I checked the physical setup and sample details.")
        self.confirm.setAccessibleName("Physical setup checked by operator")
        self.confirm.setVisible(allow_start and report.get("mode") == "hardware")
        layout.addWidget(self.confirm)
        manual = report.get("mode") == "hardware" and report.get("calibrator_mode") == "manual"
        self.manual_confirm = QCheckBox("I set the 146C to Local/manual and checked the required gas supplies.")
        self.manual_confirm.setVisible(allow_start and manual)
        layout.addWidget(self.manual_confirm)
        manual_flow = report.get("mode") == "hardware" and "alicat" in report.get("manual_devices", [])
        self.flow_confirm = QCheckBox("I checked gas settings and will continuously monitor Alicat flow.")
        self.flow_confirm.setToolTip("Manual Alicat mode provides no app flow measurement or automatic flow-loss protection. Verify gas correction and the real gas path at the instrument.")
        self.flow_confirm.setVisible(allow_start and manual_flow)
        layout.addWidget(self.flow_confirm)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel_btn = button("Back to setup" if allow_start else "Close", self.reject)
        row.addWidget(self.cancel_btn)
        self.start_btn = button("Start hardware test", self.accept, "primary")
        self.start_btn.setVisible(allow_start)
        self.start_btn.setEnabled(bool(report.get("ready")) and report.get("mode") != "hardware")
        def update_start():
            self.start_btn.setEnabled(bool(report.get("ready")) and self.confirm.isChecked() and (not manual or self.manual_confirm.isChecked()) and (not manual_flow or self.flow_confirm.isChecked()))
        self.confirm.toggled.connect(update_start)
        self.manual_confirm.toggled.connect(update_start)
        self.flow_confirm.toggled.connect(update_start)
        row.addWidget(self.start_btn)
        layout.addLayout(row)
        self.cancel_btn.setDefault(True)
