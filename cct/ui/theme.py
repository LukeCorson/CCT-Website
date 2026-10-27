"""Shared visual tokens. Edit this file to restyle the application."""

NAVY = "#122938"
TEAL = "#087f83"
AMBER = "#b97812"
INK = "#193342"
MUTED = "#6b7e89"
COLORS = {"co2_ppm": "#087f83", "flow_sccm": "#b27a2a", "column_temp_c": "#be644b", "ratio": "#7859a3"}

STYLE = """
QWidget { font-size: 13px; color: #193342; }
QMainWindow, QWidget#mainSurface, QWidget#pageSurface { background: #f3f6f7; }
QWidget#topbar { background: white; border-bottom: 1px solid #dfe7ea; }
QWidget#sidebar { background: #122938; }
QWidget#sidebar QLabel { color: #d5e1e7; background: transparent; }
QLabel#brand { color: #ffffff; font-size: 23px; font-weight: 700; letter-spacing: 2px; }
QLabel#sidebarCaption { color: #88a4b5; font-size: 9px; letter-spacing: 1px; }
QLabel#sideFoot { color: #88a4b5; font-size: 11px; }
QPushButton { border: 1px solid #cbd8dd; border-radius: 6px; padding: 9px 14px; background: white; font-weight: 600; }
QPushButton:hover { background: #e9f1f2; border-color: #94b9ba; }
QPushButton:pressed { background: #dcebec; }
QPushButton:disabled { color: #99a7ae; background: #edf1f3; border-color: #dce4e8; }
QPushButton#primary { background: #087f83; color: white; border-color: #087f83; }
QPushButton#primary:hover { background: #066b70; }
QPushButton#primary:disabled { background: #accacd; border-color: #accacd; }
QPushButton#danger { color: #a23e35; border-color: #dfb8b3; background: #fff6f4; }
QPushButton#danger:hover { background: #ffe8e3; }
QPushButton#danger:disabled { color: #aeb9be; border-color: #dce4e8; background: #f4f6f7; }
QPushButton#nav { text-align: left; background: transparent; color: #b2c6d1; border: none; padding: 12px 15px; border-radius: 5px; font-weight: 500; }
QPushButton#nav:hover { background: #1b394c; color: white; }
QPushButton#nav:checked { background: #224657; color: white; border-left: 3px solid #5bc9bd; }
QPushButton#sidePrimary { background: #40b8ad; color: #102b36; border: none; text-align: left; }
QPushButton#sidePrimary:hover { background: #60cabe; }
QFrame#card { background: white; border: 1px solid #dfe7ea; border-radius: 9px; }
QFrame#banner { background: #fff6e6; border: 1px solid #ead5ac; border-radius: 6px; }
QFrame#infoBanner { background: #e8f3f3; border: 1px solid #cbe1e2; border-radius: 6px; }
QLabel#eyebrow { color: #6b7e89; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
QLabel#title { color: #193342; font-size: 26px; font-weight: 700; }
QLabel#subtitle { color: #6b7e89; font-size: 12px; }
QLabel#sectionTitle { color: #193342; font-size: 16px; font-weight: 600; }
QLabel#metric { color: #193342; font-size: 28px; font-weight: 600; }
QLabel#unit { color: #6b7e89; font-size: 12px; }
QLabel#pill { background: #e8f3f3; color: #087f83; border-radius: 10px; padding: 4px 10px; font-size: 10px; font-weight: 700; }
QLabel#error { color: #a23e35; }
QLabel#muted { color: #6b7e89; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit { background: white; border: 1px solid #cddae0; border-radius: 5px; padding: 7px; min-height: 18px; selection-background-color: #087f83; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus { border-color: #087f83; }
QLineEdit[invalid='true'], QDoubleSpinBox[invalid='true'] { border-color: #b45448; background: #fff6f4; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: none; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 15px; height: 15px; }
QGroupBox { border: 1px solid #dfe7ea; border-radius: 7px; margin-top: 10px; padding: 15px; background: white; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 5px; }
QTableWidget { background: white; border: 1px solid #dfe7ea; border-radius: 5px; gridline-color: #edf1f3; selection-background-color: #e1eeee; selection-color: #193342; }
QHeaderView::section { background: #f5f8f9; color: #6b7e89; border: none; border-bottom: 1px solid #dfe7ea; padding: 9px; text-align: left; font-size: 11px; font-weight: 600; }
QTableWidget::item { padding: 7px; }
QProgressBar { border: none; background: #e7edef; border-radius: 3px; height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #087f83; border-radius: 3px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 8px; background: transparent; margin: 0; }
QScrollBar::handle:vertical { background: #c6d5dc; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSplitter::handle { background: #dfe7ea; }
QToolTip { background: #193342; color: white; padding: 7px; border: none; }
"""
