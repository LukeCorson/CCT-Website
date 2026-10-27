"""Exercise a complete operator workflow and render the actual Qt interface.

Uses only the simulator and writes verification artifacts beneath output/.
Run from the project root: .venv/Scripts/python.exe scripts/verify_desktop.py
"""

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from cct.contracts import DEFAULT_CONFIG
from cct.service import LabService
from cct.ui.window import MainWindow


def main():
    destination = ROOT / "output" / "verification"
    destination.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    for name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Segoe UI", 10))
    service = LabService(destination / "data")
    window = MainWindow(service)
    window.timer.stop()
    window.resize(1360, 900)
    window.show()
    app.processEvents()

    def wait_for(predicate, seconds=8):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(10)
        assert predicate(), "Background interface operation did not complete."

    assert not service.connected, "Opening the app must not connect equipment."
    window.grab().save(str(destination / "welcome.png"))

    assert len(window.nav) == 4
    window.connect_service("simulation", "")  # Internal regression fixture, not an operator feature.
    assert service.connected and service.mode == "simulation"
    assert window.stack.currentIndex() == 0
    window.setup.inputs["notebook_id"].clear()
    QTest.mouseClick(window.setup.start_btn, Qt.MouseButton.LeftButton)
    assert not service.snapshot()["running"]
    assert window.setup.validation.text(), "Explain invalid entries beside setup."

    config = dict(DEFAULT_CONFIG, notebook_id="CCT-VERIFICATION", sample_id="Demo sorbent",
                  test_type="breakthrough", automatic_cutoff_enabled=False)
    window.setup.load_config(config)
    assert not window.setup.inputs["breakthrough_pct"].isVisible()
    window.grab().save(str(destination / "tests.png"))
    QTest.mouseClick(window.setup.start_btn, Qt.MouseButton.LeftButton)
    assert service.snapshot()["running"] and window.stack.currentIndex() == 1
    assert service.engine.threshold is None
    service.set_speed(120)
    service.update(0.25)
    window.refresh()
    assert service.engine.stage == "core_breakthrough_bypass"
    window.live.column_btn.click()
    captured = False
    for _ in range(80):
        service.update(0.25)
        snapshot = service.snapshot()
        if snapshot["stage"] == "core_breakthrough_column" and snapshot["stage_elapsed_s"] > 500:
            window.refresh()
            app.processEvents()
            window.grab().save(str(destination / "live-run.png"))
            captured = True
            break
    assert captured and snapshot["running"], "A normal breakthrough test must not stop at a hidden cutoff."
    assert not window.live.plots.ratio.isVisible()
    QTest.mouseClick(window.persistent_stop, Qt.MouseButton.LeftButton)
    for _ in range(100):
        service.update(0.25)
        snapshot = service.snapshot()
        if not snapshot["running"]:
            break
    assert snapshot["outcome"] == "complete", snapshot
    run_id = snapshot["run_id"]
    saved = service.load_run(run_id)
    assert saved["metadata"]["test_type"] == "breakthrough"
    assert saved["metadata"]["recipe"]["breakthrough_pct"] is None
    assert all(row["threshold_ratio"] is None for row in saved["samples"])
    exports = service.export_run(run_id)
    assert (exports / "measurements_1Hz.csv").is_file()
    assert (exports / "test_settings.json").is_file()

    window.refresh()
    for index, name in ((3, "connections"), (2, "results")):
        QTest.mouseClick(window.nav[index], Qt.MouseButton.LeftButton)
        if index == 2:
            wait_for(lambda: not window._workers)
            # Select this verification run even when earlier QA records exist.
            wanted = next(i for i, row in enumerate(window.history.filtered) if row["id"] == run_id)
            window.history.table.selectRow(wanted)
            wait_for(lambda: not window._workers)
            assert not window.history.cycle_table.isVisible()
            assert not window.history.plots.threshold_line.isVisible()
        app.processEvents()
        window.grab().save(str(destination / f"{name}.png"))

    # The previously outlined automatic cycle is an explicit advanced choice.
    window.show_page(0)
    automatic = dict(config, notebook_id="CCT-AUTO-VERIFICATION", automatic_cutoff_enabled=True, cycles=2)
    window.setup.load_config(automatic)
    window.setup.advanced_toggle.setChecked(True)
    app.processEvents()
    assert window.setup.inputs["breakthrough_pct"].isVisible()
    window.grab().save(str(destination / "optional-automatic-cycles.png"))
    window.start_run(window.setup.config())
    for _ in range(500):
        service.update(0.25)
        if not service.snapshot()["running"]:
            break
    auto_run = service.load_run(service.run_id)
    assert auto_run["metadata"]["status"] == "complete"
    assert auto_run["metadata"]["cycles_completed"] == 2
    for test_type in ("zero", "adsorption", "desorption"):
        window.show_page(0)
        window.setup.load_config(dict(config, test_type=test_type, automatic_cutoff_enabled=False))
        window.setup.advanced_toggle.setChecked(False)
        app.processEvents()
        assert not window.setup.inputs["breakthrough_pct"].isVisible()
        window.grab().save(str(destination / f"test-{test_type}.png"))

    # A fault run should be saved separately, never erase the completed record.
    window.start_run(dict(config, notebook_id="CCT-FAULT-CHECK"))
    service.inject_fault("overtemp")
    service.update(0.25)
    window.refresh()
    assert service.snapshot()["outcome"] == "fault"
    fault_record = service.load_run(service.snapshot()["run_id"])
    assert fault_record["metadata"]["status"] == "fault"
    assert service.load_run(run_id)["metadata"]["status"] == "complete"
    app.processEvents()
    window.grab().save(str(destination / "fault-state.png"))

    report = {"operator_workflow": "passed", "navigation_pages": 4,
              "normal_breakthrough_has_no_cutoff": True, "optional_automatic_cycles_completed": 2,
              "recorded_samples": len(saved["samples"]), "recorded_events": len(saved["events"]),
              "simulated_duration_s": snapshot["elapsed_s"], "exports": str(exports),
              "fault_outcome": fault_record["metadata"]["status"], "hardware_actuated": False}
    (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
