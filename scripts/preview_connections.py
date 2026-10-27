"""Render the real Connections screen using mocked USB inventory, without ports."""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from cct.service import LabService
from cct.ui.window import MainWindow

app = QApplication.instance() or QApplication([])
font_dir = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
for name in ('segoeui.ttf', 'seguisb.ttf', 'segoeuib.ttf', 'seguisym.ttf'):
    QFontDatabase.addApplicationFont(str(font_dir / name))
app.setFont(QFont('Segoe UI', 10))
output = Path('output/connections-preview.png').resolve()
output.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as directory, patch('serial.Serial', side_effect=AssertionError('No real USB communication')):
    window = MainWindow(LabService(data_root=Path(directory)))
    window.devices._discover = lambda: [
        {'port': f'COM{index+7}', 'usb_serial': f'DEMO-{role}', 'vid': 1027, 'pid': 24577, 'description': 'USB Serial Adapter'}
        for index, role in enumerate(('core2', 'licor'))]
    window.show()
    window.show_page(3)
    deadline = time.monotonic() + 5
    while window.devices._workers and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert not window.devices._workers
    for role in ('core2', 'licor'):
        selector = window.devices.adapter_selectors[role]
        for index in range(selector.count()):
            data = selector.itemData(index)
            if isinstance(data, dict) and data.get('usb_serial') == f'DEMO-{role}':
                selector.setCurrentIndex(index)
                break
    app.processEvents()
    assert window.grab().save(str(output))
    window.close()
    window.devices.wait_for_done()
    window._worker_pool.waitForDone()
print(output)
