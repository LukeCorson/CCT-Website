"""Desktop entry point: python -m cct.app. No equipment connects on startup."""

import argparse
import logging
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="CCT Control desktop laboratory interface")
    parser.add_argument("--data-dir", type=Path, help="Test records and log storage folder")
    parser.add_argument("--screenshot", type=Path, help="Save a screenshot of the initial application and exit")
    args = parser.parse_args(argv)
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QMessageBox
    from cct.contracts import default_data_root
    from cct.service import LabService
    from cct.ui.window import MainWindow

    data_root = (args.data_dir or default_data_root()).resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    log_dir = data_root / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(filename=log_dir / "app.log", encoding="utf-8", level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("CCT Control")
    app.setOrganizationName("CCT Laboratory")
    # Preserve native fonts on each desktop. Explicit Windows registration also
    # supports screenshot environments with an incomplete Qt font catalogue.
    if sys.platform == "win32":
        import os

        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for font_name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "seguisym.ttf"):
            font_path = font_dir / font_name
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont("Segoe UI", 10))
    else:
        native_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        if native_font.pointSize() <= 0:
            native_font.setPointSize(11)
        app.setFont(native_font)
    lock = QLockFile(str(data_root / ".cct-control.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "CCT Control is already open", "Another CCT Control instance is using this data folder. Return to that window to avoid competing device connections.")
        return 1

    window = MainWindow(LabService(data_root=data_root))
    available = app.primaryScreen().availableGeometry()
    window.resize(min(1440, available.width() - 40), min(1000, available.height() - 60))
    original_hook = sys.excepthook

    def report_exception(kind, value, traceback):
        logging.critical("Unhandled application exception", exc_info=(kind, value, traceback))
        window.show_error(f"Unexpected application error: {value}. Details were saved to {log_dir / 'app.log'}")
        original_hook(kind, value, traceback)

    sys.excepthook = report_exception
    window.show()
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
            window.timer.stop()
            app.quit()
        QTimer.singleShot(1000, capture)
    result = app.exec()
    window._worker_pool.waitForDone()
    window.devices.wait_for_done(5000)
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
