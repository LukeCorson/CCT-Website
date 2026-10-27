"""Render and verify the novice/operator workflow with simulation and fake USB IDs.

No serial ports are opened. Images and generated data stay under output/.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cct import __version__
from cct.service import LabService
from cct.ui.help import HelpDialog
from cct.ui.preflight import PreflightDialog
from cct.ui.window import MainWindow


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = ROOT / "output" / f"product-verification-{stamp}"
    destination.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    if sys.platform == "win32":
        for name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont("Segoe UI", 10))
    service = LabService(destination / "data")
    window = MainWindow(service)
    window.timer.stop()
    window.resize(1360, 930)
    window.show()

    def wait(predicate):
        deadline = time.monotonic() + 8
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(5)
        assert predicate(), "A bounded background operation did not finish."

    def shot(widget, name):
        app.processEvents()
        assert widget.grab().save(str(destination / (name + ".png")))

    with patch("serial.Serial", side_effect=AssertionError("Product verification must never open instrument ports")):
        assert not service.connected
        shot(window, "01-first-open")
        window.connect_service("simulation", "")  # Internal QA fixture; absent from the operator UI.
        window.setup.inputs["notebook_id"].setText("CCT-PRODUCT-QA")
        window.setup.inputs["sample_id"].setText("Demonstration sorbent")
        window.setup.inputs["operator"].setText("Private QA operator")
        config = window.setup.config()
        window.setup.plan_toggle.setChecked(True)
        shot(window, "02-test-plan")
        report = service.preflight_report(config)
        assert report["ready"]
        review = PreflightDialog(report, config, parent=window)
        review.show()
        shot(review, "03-preflight")
        review.close()
        window.start_run(config)
        service.set_speed(120)
        service.update(0.25)
        window.refresh()
        assert service.engine.stage == "core_breakthrough_bypass"
        window.live.column_btn.click()
        for _ in range(100):
            service.update(0.25)
            if service.engine.stage == "core_breakthrough_column" and service.engine.stage_elapsed > 300:
                break
        assert service.engine.stage == "core_breakthrough_column" and service.engine.threshold is None
        window.refresh()
        assert all(not edit.isEnabled() for edit in window.setup.inputs.values())
        window.live.observation.setText("Sample holder checked; continue recording.")
        window.live.note_btn.click()
        assert not window.live.observation.text()
        shot(window, "04-live-recording")
        window.stop_run()
        for _ in range(100):
            service.update(0.25)
            if not service.engine.running:
                break
        assert service.engine.outcome == "complete"
        original = service.load_run(service.run_id)
        assert any(event["event"] == "operator_note" for event in original["events"])
        imported = service.import_completed_run(service.run_path)
        assert imported != service.run_id
        assert service.load_run(imported)["metadata"]["source_import"]["original_id"] == service.run_id
        support = service.export_support_bundle()
        window.refresh()
        window.show_page(2)
        wait(lambda: not window._workers)
        window.history._current = service.run_id
        window.load_run(service.run_id)
        wait(lambda: not window._workers)
        shot(window, "05-results-and-import")
        guide = HelpDialog(window)
        guide.show()
        shot(guide, "06-offline-help")
        guide.close()
        service.disconnect()
        window.refresh()
        window.show_page(3)
        window.devices.mode.setCurrentIndex(window.devices.mode.findData("hardware"))
        profile = service.new_apparatus_profile()
        profile["alicat_mode"] = "automatic"  # Three-device legacy commissioning fixture.
        adapters = []
        for index, role in enumerate(("core2", "licor", "alicat", "calibrator")):
            if role == "calibrator":
                continue
            serial = "QA-FAKE-" + role
            profile["devices"][role].update(port=f"COM{index + 1}", expected_usb_serial=serial)
            adapters.append({"port": f"COM{index + 11}", "usb_serial": serial, "vid": 1027,
                             "pid": 24577, "description": "Mock adapter — no real USB access",
                             "manufacturer": "QA fixture", "location": str(index)})
        saved_path = service.save_apparatus_profile(profile)
        window.devices.set_profile(saved_path)
        wait(lambda: not window.devices._workers)
        window.devices._discover = lambda: adapters
        window.devices.scan_btn.click()
        wait(lambda: not window.devices._workers)
        assert all(info["state"] == "matched" for role, info in window.devices._resolution["devices"].items() if role != "calibrator")
        assert window.devices._resolution["devices"]["calibrator"]["state"] == "disabled"
        assert window.devices.connect_btn.isEnabled()
        assert not window.devices._draft["commissioned"]
        # Do NOT choose Connect: this is passive pairing display QA only.
        shot(window, "07-passive-pairing-top")
        scroll = window.stack.currentWidget().verticalScrollBar()
        scroll.setValue(scroll.maximum())
        shot(window, "08-passive-pairing-bottom")
        window.show_connection_settings()
        window.devices.instruction_buttons["alicat"].setChecked(True)
        shot(window._connection_dialog, "09-device-instructions")
        report = {"app_version": __version__, "operator_workflow": "passed", "physical_ports_opened": 0,
                  "usb_inventory": "mocked", "no_automatic_cutoff": True, "observation_saved": True,
                  "imported_record": imported, "support_bundle": str(support), "screenshots": 9}
        (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "destination": str(destination)}, indent=2))
        window.close()
        app.processEvents()
        window.devices.wait_for_done(5000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
