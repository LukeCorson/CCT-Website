"""Linked, bounded live traces shared by monitoring and run history."""

import math
from html import escape

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import COLORS
from .widgets import label

pg.setConfigOptions(antialias=True, background="w", foreground="#71838e")


class RunPlots(QWidget):
    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self._row_key = None
        self._event_keys = set()
        self._markers = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        tools = QHBoxLayout()
        tools.addWidget(label("SIGNAL HISTORY", "eyebrow"))
        tools.addStretch()
        self.ratio = QCheckBox("Normalized CO₂")
        self.ratio.setChecked(False)
        self.follow = QCheckBox("Follow live")
        self.follow.setChecked(True)
        self.follow.setToolTip("Uncheck to inspect a time interval. Recording continues.")
        for control in (self.ratio, self.follow):
            tools.addWidget(control)
        self.ratio.hide()
        layout.addLayout(tools)
        self.plot_co2 = self._plot("CO₂", "ppm", height=215 if not compact else 155)
        self.plot_ratio = self._plot("Cout / Cin", "", height=115)
        self.plot_flow = self._plot("Flow", "sccm", height=115)
        self.plot_temp = self._plot("Column", "°C", height=115)
        plots = [self.plot_co2, self.plot_ratio, self.plot_flow, self.plot_temp]
        for plot in plots:
            layout.addWidget(plot)
            if plot is not self.plot_co2:
                plot.setXLink(self.plot_co2)
            plot.setLabel("bottom", "Experiment elapsed", "min")
        self.plot_ratio.setVisible(False)
        self.ratio.toggled.connect(self.plot_ratio.setVisible)
        self.curves = {
            "co2_ppm": self.plot_co2.plot(pen=pg.mkPen(COLORS["co2_ppm"], width=2), connect="finite"),
            "flow_sccm": self.plot_flow.plot(pen=pg.mkPen(COLORS["flow_sccm"], width=1.8), connect="finite"),
            "column_temp_c": self.plot_temp.plot(pen=pg.mkPen(COLORS["column_temp_c"], width=1.8), connect="finite"),
            "ratio": self.plot_ratio.plot(pen=pg.mkPen(COLORS["ratio"], width=1.8), connect="finite"),
        }
        self.baseline_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#9fadb4", style=Qt.PenStyle.DashLine), label="inlet reference")
        self.threshold_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#c8953b", style=Qt.PenStyle.DashLine), label="breakthrough threshold")
        self.plot_co2.addItem(self.baseline_line)
        self.plot_co2.addItem(self.threshold_line)
        self.ratio_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#c8953b", style=Qt.PenStyle.DashLine))
        self.plot_ratio.addItem(self.ratio_line)
        self.empty = label("Connect devices, then start a test to see recorded traces.", "subtitle", True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)
        self.caption = label("All stages are recorded. Gaps indicate missing readings. Drag to zoom; right-click a plot to reset.", "subtitle", True)
        layout.addWidget(self.caption)

    @staticmethod
    def _plot(name, units, height):
        plot = pg.PlotWidget()
        plot.setMinimumHeight(height)
        plot.setMaximumHeight(height + 60)
        plot.setLabel("left", name, units=units)
        plot.showGrid(x=True, y=True, alpha=0.12)
        plot.getAxis("left").setWidth(76)
        # ppm, sccm, degrees and minutes are already deliberate lab units.
        # Automatic SI prefixes would turn 0.43 ppm into a confusing 430 mppm.
        plot.getAxis("left").enableAutoSIPrefix(False)
        plot.getAxis("bottom").enableAutoSIPrefix(False)
        plot.setMouseEnabled(x=True, y=True)
        plot.setClipToView(True)
        plot.setDownsampling(auto=True, mode="peak")
        plot.getPlotItem().setContentsMargins(0, 2, 10, 0)
        return plot

    @staticmethod
    def _value(row, key):
        value = row.get(key, (row.get("values") or {}).get(key, (row.get("channels") or {}).get(key)))
        try:
            return float(value) if value is not None else math.nan
        except (TypeError, ValueError):
            return math.nan

    def update_data(self, rows, baseline=None, threshold=None, events=None, run_key=None):
        rows = rows or []
        key = (run_key, len(rows), (rows[-1].get("elapsed_s") if rows else None))
        if key != self._row_key:
            self._row_key = key
            x = [float(row.get("elapsed_s", 0) or 0) / 60 for row in rows]
            for channel, curve in self.curves.items():
                if channel == "ratio":
                    y = []
                    for row in rows:
                        recorded = row.get("breakthrough_ratio")
                        row_baseline = row.get("baseline_ppm")
                        if recorded is not None:
                            y.append(self._value(row, "breakthrough_ratio"))
                        elif row_baseline and row_baseline > 0:
                            y.append(self._value(row, "co2_ppm") / row_baseline)
                        else:
                            y.append(math.nan)
                else:
                    y = [self._value(row, channel) for row in rows]
                curve.setData(x, y)
            self.empty.setVisible(not rows)
            if x and self.follow.isChecked():
                latest = x[-1]
                self.plot_co2.setXRange(max(0, latest - 30), max(1, latest), padding=0.02)
        for line, value in ((self.baseline_line, baseline), (self.threshold_line, baseline * threshold if baseline and threshold is not None else None), (self.ratio_line, threshold)):
            line.setVisible(value is not None)
            if value is not None:
                line.setValue(float(value))
        # A bounded set of transition markers keeps long recordings responsive.
        transition_events = [e for e in (events or []) if e.get("event", e.get("type", e.get("kind"))) in ("stage", "transition", "stage_changed", "fault", "stop", "operator_note")][-24:]
        event_key = repr([(e.get("elapsed_s"), e.get("message"), e.get("type")) for e in transition_events])
        if event_key != self._event_keys:
            self._event_keys = event_key
            for line in self._markers:
                self.plot_co2.removeItem(line)
            self._markers = []
            for event in transition_events:
                if event.get("elapsed_s") is None:
                    continue
                note = event.get("event") == "operator_note"
                marker = pg.InfiniteLine(pos=float(event["elapsed_s"]) / 60, angle=90,
                                         pen=pg.mkPen("#b97812" if note else "#cedce1", style=Qt.PenStyle.DashLine if note else Qt.PenStyle.DotLine))
                marker.setToolTip("<qt>" + escape(str(event.get("message", event.get("stage", "Stage transition")))) + "</qt>")
                self.plot_co2.addItem(marker)
                self._markers.append(marker)


class CycleOverlay(QWidget):
    """Compare adsorption curves without mixing each cycle's inlet reference."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("Breakthrough cycles · aligned to column switch", "sectionTitle"))
        layout.addWidget(label("Each cycle uses its saved inlet reference. Only breakthrough-stage measurements are included.", "subtitle", True))
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(300)
        self.plot.setLabel("left", "Cout / Cin")
        self.plot.setLabel("bottom", "Time since column switch", "min")
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.showGrid(x=True, y=True, alpha=0.12)
        self.plot.addLegend(offset=(12, 12))
        layout.addWidget(self.plot)
        self.empty = label("No breakthrough measurements were recorded in this experiment.", "subtitle", True)
        layout.addWidget(self.empty)

    def set_rows(self, rows):
        self.plot.clear()
        grouped = {}
        for row in rows or []:
            if row.get("stage") == "breakthrough":
                grouped.setdefault(row.get("cycle", 0), []).append(row)
        palette = ("#087f83", "#b97812", "#7859a3", "#be644b", "#4a7ea2", "#547d45", "#bf658e")
        for idx, (cycle, samples) in enumerate(sorted(grouped.items(), key=lambda item: str(item[0]))):
            start = samples[0].get("elapsed_s", 0) or 0
            x, y = [], []
            for row in samples:
                elapsed = row.get("stage_elapsed_s")
                x.append(float(elapsed if elapsed is not None else (row.get("elapsed_s", 0) or 0) - start) / 60)
                value = row.get("breakthrough_ratio")
                y.append(float(value) if value is not None else math.nan)
            self.plot.plot(x, y, name=f"Cycle {cycle}", pen=pg.mkPen(palette[idx % len(palette)], width=2), connect="finite")
        self.empty.setVisible(not grouped)
        self.plot.enableAutoRange()
