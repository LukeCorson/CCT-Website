"""Operator checks: simple USB rows and deliberate dropdown changes."""
import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox as QtComboBox, QLabel, QPushButton

from cct.ui.connections import DevicesPage, ROLES
from cct.ui.help import HelpDialog
from cct.ui.pages import HistoryPage, LivePage
from cct.ui.setup import ExperimentPage
from cct.ui.widgets import QComboBox


def wheel(widget, delta=-120):
    position = widget.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(widget.mapToGlobal(position)), QPoint(), QPoint(0, delta),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


class SimpleConnectionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self):
        adapters = [{'port': 'COM7', 'usb_serial': 'CORE', 'vid': 1027, 'pid': 24577, 'description': 'USB serial'}]
        page = DevicesPage(discovery_callback=lambda: adapters)
        page.set_profile({'calibrator_mode': 'manual', 'commissioned': False, 'devices': {role: {} for role in ROLES}})
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close)
        return page

    def wait(self, page):
        deadline = time.monotonic() + 5
        while page._workers and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertFalse(page._workers)

    def test_page_has_only_four_device_names_and_selectors(self):
        page = self.make_page()
        with patch('serial.Serial', side_effect=AssertionError('Selection must not open a port')):
            page.show()
            self.wait(page)
            labels = [item.text() for item in page.findChildren(QLabel) if item.isVisibleTo(page)]
            self.assertEqual(set(labels), {'Core2', 'LI-COR LI-840A', 'Alicat B-500C', 'Thermo 146C'})
            self.assertEqual(len([item for item in page.findChildren(QtComboBox) if item.isVisibleTo(page)]), 4)
            self.assertFalse(any(item.isVisibleTo(page) for item in page.findChildren(QPushButton)))
            combo = page.adapter_selectors['core2']
            combo.setCurrentIndex(1)
            self.assertEqual(page._draft['devices']['core2']['expected_usb_serial'], 'CORE')
            self.assertEqual(page.adapter_selectors['calibrator'].currentData(), page.MANUAL_146C)
            self.assertFalse(page._connected)

    def test_scan_does_not_refresh_while_a_dropdown_is_open(self):
        page = self.make_page()
        page.show()
        self.wait(page)
        combo = page.adapter_selectors['core2']
        combo.showPopup()
        with patch.object(page, 'scan_adapters') as scan:
            page.refresh_inventory()
            scan.assert_not_called()
        combo.hidePopup()

    def test_connected_or_running_locks_all_four_selectors(self):
        page = self.make_page()
        page.show()
        self.wait(page)
        page.update_state({'connected': True, 'running': True, 'mode': 'hardware'})
        self.assertTrue(all(not combo.isEnabled() for combo in page.adapter_selectors.values()))

    def test_closed_dropdown_ignores_wheel_even_with_focus_but_accepts_keys(self):
        combo = QComboBox()
        combo.addItems(['One', 'Two', 'Three'])
        combo.setCurrentIndex(1)
        combo.show()
        self.addCleanup(combo.close)
        for focused in (False, True):
            combo.setFocus() if focused else combo.clearFocus()
            wheel(combo, -120)
            wheel(combo, 120)
            self.assertEqual(combo.currentIndex(), 1)
        QTest.keyClick(combo, Qt.Key.Key_Down)
        self.assertEqual(combo.currentIndex(), 2)

    def test_open_dropdown_list_still_scrolls(self):
        combo = QComboBox()
        combo.addItems([str(i) for i in range(100)])
        combo.setMaxVisibleItems(8)
        combo.show()
        self.addCleanup(combo.close)
        combo.showPopup()
        self.app.processEvents()
        view = combo.view()
        before = view.verticalScrollBar().value()
        wheel(view.viewport(), -120)
        self.app.processEvents()
        self.assertGreater(view.verticalScrollBar().value(), before)
        combo.hidePopup()

    def test_every_application_dropdown_uses_wheel_protection(self):
        widgets = [self.make_page(), ExperimentPage(), LivePage(), HistoryPage(), HelpDialog()]
        for widget in widgets:
            for combo in widget.findChildren(QtComboBox):
                self.assertIsInstance(combo, QComboBox)
                before = combo.currentIndex()
                wheel(combo)
                self.assertEqual(combo.currentIndex(), before)
            widget.close()

    def test_manual_alicat_and_licor_can_be_selected_without_usb_commands(self):
        page = self.make_page()
        page.show()
        self.wait(page)
        with patch('serial.Serial', side_effect=AssertionError('No manual instrument port')):
            for role in ('alicat', 'licor'):
                combo = page.adapter_selectors[role]
                combo.setCurrentIndex(combo.findData(page.MANUAL_VALUES[role]))
                self.assertEqual(page._draft[role + '_mode'], 'manual')
            self.assertEqual(page._required_roles(), ('core2',))

    def test_flow_monitoring_confirmation_is_required_in_hardware_review(self):
        from cct.ui.preflight import PreflightDialog
        dialog = PreflightDialog({'ready': True, 'mode': 'hardware', 'manual_devices': ['alicat']}, {}, allow_start=True)
        self.addCleanup(dialog.close)
        dialog.confirm.setChecked(True)
        self.assertFalse(dialog.start_btn.isEnabled())
        dialog.flow_confirm.setChecked(True)
        self.assertTrue(dialog.start_btn.isEnabled())

    def test_no_simulator_source_fault_tools_or_speed_control(self):
        page = self.make_page()
        self.assertEqual(page.mode.findData('simulation'), -1)
        self.assertFalse(hasattr(page, 'test_toggle'))
        live = LivePage()
        self.assertFalse(hasattr(live, 'speed'))
        live.close()


if __name__ == '__main__':
    unittest.main()
