"""Small reusable interface elements."""

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QComboBox as QtComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget


class QComboBox(QtComboBox):
    """Require an intentional click or key press to change a closed dropdown."""

    def wheelEvent(self, event):
        # Ignore rather than consume: the containing page can still scroll.
        # An open popup has its own item view, which retains normal scrolling.
        event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        # Keep the dropdown affordance visible on every supported Qt theme.
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        area = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                           QStyle.SubControl.SC_ComboBoxArrow, self)
        center = area.center()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#6b7e89' if self.isEnabled() else '#aab7be'), 1.5))
        painter.drawPolyline(QPolygonF([QPointF(center.x()-4, center.y()-2),
                                      QPointF(center.x(), center.y()+2), QPointF(center.x()+4, center.y()-2)]))


def label(text="", kind=None, wrap=False):
    value = QLabel(text)
    # Notes and imported metadata are data, never HTML or local-file embeds.
    value.setTextFormat(Qt.TextFormat.PlainText)
    if kind:
        value.setObjectName(kind)
    value.setWordWrap(wrap)
    return value


def button(text, callback=None, kind=None):
    value = QPushButton(text)
    if kind:
        value.setObjectName(kind)
    if callback:
        value.clicked.connect(callback)
    return value


def card():
    value = QFrame()
    value.setObjectName("card")
    layout = QVBoxLayout(value)
    layout.setContentsMargins(20, 17, 20, 17)
    layout.setSpacing(10)
    return value, layout


def heading(title, subtitle):
    value = QWidget()
    layout = QVBoxLayout(value)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(5)
    layout.addWidget(label(title, "title"))
    layout.addWidget(label(subtitle, "subtitle", True))
    return value


def banner(text, warning=False):
    value = QFrame()
    value.setObjectName("banner" if warning else "infoBanner")
    layout = QHBoxLayout(value)
    layout.setContentsMargins(13, 9, 13, 9)
    layout.addWidget(label(text, None, True))
    return value


def duration(seconds):
    seconds = max(0, int(seconds or 0))
    h, remain = divmod(seconds, 3600)
    m, s = divmod(remain, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def number(value, places=1):
    try:
        return f"{float(value):,.{places}f}" if math.isfinite(float(value)) else "—"
    except (TypeError, ValueError):
        return "—"


class MetricCard(QFrame):
    def __init__(self, title, unit, accent, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.accent = accent
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(3)
        self.caption = label(title.upper(), "eyebrow")
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        row = QHBoxLayout()
        self.value = label("—", "metric")
        self.value.setMinimumWidth(92)
        self.value.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        self.value.setStyleSheet(f"color: {accent};")
        row.addWidget(self.value)
        row.addWidget(label(unit, "unit"), 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        layout.addLayout(row)
        self.detail = label("No readings yet", "subtitle")
        layout.addWidget(self.detail)

    def update_value(self, value, age, connected, simulated=False):
        try:
            valid = value is not None and math.isfinite(float(value))
            fresh = connected and valid and age is not None and math.isfinite(float(age)) and float(age) <= 5
        except (ValueError, TypeError):
            valid = fresh = False
        self.value.setText(number(value) if connected and valid else "—")
        self.value.setStyleSheet(f"color: {self.accent if fresh else '#a36c16' if connected and valid else '#8c9ca5'};")
        if not connected or value is None:
            self.detail.setText("Waiting for connection" if not connected else "Waiting for reading")
        elif not fresh:
            self.detail.setText(f"Stale · last received {number(age, 0)} s ago")
        else:
            self.detail.setText("Simulated reading" if simulated else "Live reading")
        self.setToolTip(f"Age of last reading: {number(age)} s" if age is not None else "No device data")
