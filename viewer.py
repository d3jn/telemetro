"""F1 25 telemetry viewer — desktop GUI.

Top bar: two "Load sample" buttons each paired with a lap dropdown, and a
"Render" button on the right. Main area holds stacked chart panels with a
shared X axis: a time-delta panel (shown only when both samples are loaded)
above an inputs panel.
"""

import sys

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


def _format_lap_time_ms(ms):
    if ms is None or pd.isna(ms) or ms <= 0:
        return "—"
    total_s = float(ms) / 1000.0
    minutes = int(total_s // 60)
    seconds = total_s - minutes * 60
    return f"{minutes}:{seconds:06.3f}"


class SampleLoader(QWidget):
    """One "Load sample N" button + the lap dropdown that follows it."""

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self._label = label
        self._df = None
        self._path = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.load_btn = QPushButton(f"Load sample {label}")
        self.load_btn.clicked.connect(self._on_load_clicked)
        layout.addWidget(self.load_btn)

        self.lap_combo = QComboBox()
        self.lap_combo.setEnabled(False)
        self.lap_combo.setMinimumWidth(180)
        layout.addWidget(self.lap_combo)

    def _on_load_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Load sample {self._label}",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return

        try:
            df = pd.read_csv(path)
        except Exception as e:
            self._df = None
            self._path = None
            self.lap_combo.clear()
            self.lap_combo.setEnabled(False)
            QMessageBox.critical(self, "Load failed", f"Could not read {path}:\n{e}")
            return

        self._df = df
        self._path = path
        self._populate_laps()

    def _populate_laps(self):
        self.lap_combo.clear()
        if self._df is None or "lap_num" not in self._df.columns:
            self.lap_combo.setEnabled(False)
            return

        # Lap time per lap = max running timer seen during that lap. The timer
        # resets to 0 on each new lap, so its peak is the lap's final reading.
        grouped = self._df.groupby("lap_num")["lap_time"].max().sort_index()
        for lap_num, lap_ms in grouped.items():
            if pd.isna(lap_num):
                continue
            text = f"#{int(lap_num)} {_format_lap_time_ms(lap_ms)}"
            self.lap_combo.addItem(text, userData=int(lap_num))

        self.lap_combo.setEnabled(self.lap_combo.count() > 0)


class ChartPanel(QWidget):
    """A pyqtgraph chart with Y-locked, X-pannable behavior and hover tooltips.

    Holds a list of series (each: x, y, tooltip label, optional sample number,
    value formatter). On hover, interpolates each series at the cursor's X
    position and shows a tooltip listing all in-range values.
    """

    def __init__(
        self,
        y_label,
        y_range,
        y_ticks=None,
        x_label=None,
        parent=None,
    ):
        super().__init__(parent)
        self._series = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget(background="w")
        self.plot_item = self.plot_widget.getPlotItem()
        if x_label:
            self.plot_item.setLabel("bottom", x_label)
        self.plot_item.setLabel("left", y_label)
        self.plot_item.showGrid(x=True, y=True, alpha=0.3)

        y_min, y_max = y_range
        self.plot_item.setYRange(y_min, y_max, padding=0)
        vb = self.plot_item.getViewBox()
        vb.setMouseEnabled(x=True, y=False)
        vb.setLimits(yMin=y_min, yMax=y_max)

        if y_ticks is not None:
            self.plot_item.getAxis("left").setTicks([y_ticks, []])

        layout.addWidget(self.plot_widget)

        self._hover_proxy = pg.SignalProxy(
            self.plot_widget.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._on_mouse_move,
        )

    def clear(self):
        self._series.clear()
        self.plot_item.clear()

    def add_series(self, x, y, pen, label, sample_num=None, formatter=None):
        if formatter is None:
            formatter = lambda v: f"{v:.0f}"
        self.plot_item.plot(x, y, pen=pen)
        self._series.append(
            {
                "x": x,
                "y": y,
                "label": label,
                "sample_num": sample_num,
                "formatter": formatter,
            }
        )

    def set_x_range(self, x_min, x_max):
        vb = self.plot_item.getViewBox()
        vb.setLimits(xMin=x_min, xMax=x_max)
        self.plot_item.setXRange(x_min, x_max, padding=0)

    def set_y_range(self, y_min, y_max):
        vb = self.plot_item.getViewBox()
        vb.setLimits(yMin=y_min, yMax=y_max)
        self.plot_item.setYRange(y_min, y_max, padding=0)

    def link_x_to(self, other):
        self.plot_item.setXLink(other.plot_item)

    def _on_mouse_move(self, event):
        if not self._series:
            QToolTip.hideText()
            return
        pos = event[0]
        vb = self.plot_item.getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            QToolTip.hideText()
            return
        x_val = vb.mapSceneToView(pos).x()

        sample_nums = {
            s["sample_num"] for s in self._series if s["sample_num"] is not None
        }
        show_label = len(sample_nums) > 1

        lines = []
        for s in self._series:
            x, y = s["x"], s["y"]
            if x_val < x[0] or x_val > x[-1]:
                continue
            y_val = float(np.interp(x_val, x, y))
            suffix = (
                f" {s['sample_num']}"
                if show_label and s["sample_num"] is not None
                else ""
            )
            lines.append(f"{s['label']}{suffix}: {s['formatter'](y_val)}")

        if not lines:
            QToolTip.hideText()
            return
        QToolTip.showText(QCursor.pos(), "\n".join(lines), self.plot_widget)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("telemetrO viewer")

        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)

        self.sample1 = SampleLoader("1")
        self.sample2 = SampleLoader("2")
        top.addWidget(self.sample1)
        top.addWidget(self.sample2)
        top.addStretch(1)

        self.render_btn = QPushButton("Render")
        top.addWidget(self.render_btn)

        root.addLayout(top)

        pg.setConfigOptions(antialias=True)

        self.delta_panel = ChartPanel(
            y_label="Δ (s)",
            y_range=(-1.0, 1.0),  # placeholder; set per-render from data
        )
        self.input_panel = ChartPanel(
            y_label="Inputs",
            y_range=(0, 110),
            y_ticks=[(v, str(v)) for v in (0, 20, 40, 60, 80, 100)],
            x_label="Lap distance (m)",
        )
        self.delta_panel.link_x_to(self.input_panel)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.delta_panel)
        splitter.addWidget(self.input_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        self.delta_panel.setVisible(False)

        self._samples = {1: None, 2: None}
        self.render_btn.clicked.connect(self._on_render)

    @staticmethod
    def _extract_lap(sample):
        df = sample._df
        lap = sample.lap_combo.currentData()
        if df is None or lap is None:
            return None
        lap_df = df[(df["lap_num"] == lap) & (df["lap_distance"] >= 0)]
        lap_df = lap_df.sort_values("lap_distance")
        if lap_df.empty:
            return None
        return {
            "x": lap_df["lap_distance"].to_numpy(),
            "throttle": lap_df["throttle"].to_numpy(),
            "brake": lap_df["brake"].to_numpy(),
            "lap_time": lap_df["lap_time"].to_numpy(),
        }

    def _on_render(self):
        self._samples = {
            1: self._extract_lap(self.sample1),
            2: self._extract_lap(self.sample2),
        }

        self.input_panel.clear()
        self.delta_panel.clear()

        pens = {
            (1, "throttle"): pg.mkPen("g", width=3),
            (1, "brake"): pg.mkPen("r", width=3),
            (2, "throttle"): pg.mkPen(
                (144, 238, 144), width=3, style=Qt.PenStyle.DashLine
            ),
            (2, "brake"): pg.mkPen(
                (240, 128, 128), width=3, style=Qt.PenStyle.DashLine
            ),
        }

        # Draw sample 2 first so sample 1's solid lines land on top.
        for sample_num in (2, 1):
            data = self._samples[sample_num]
            if data is None:
                continue
            self.input_panel.add_series(
                data["x"],
                data["throttle"],
                pen=pens[(sample_num, "throttle")],
                label="Throttle",
                sample_num=sample_num,
            )
            self.input_panel.add_series(
                data["x"],
                data["brake"],
                pen=pens[(sample_num, "brake")],
                label="Brake",
                sample_num=sample_num,
            )

        xs = [d["x"] for d in self._samples.values() if d is not None]
        if not xs:
            self.delta_panel.setVisible(False)
            return
        x_min = min(float(a.min()) for a in xs)
        x_max = max(float(a.max()) for a in xs)
        self.input_panel.set_x_range(x_min, x_max)

        s1 = self._samples[1]
        s2 = self._samples[2]
        if s1 is None or s2 is None:
            self.delta_panel.setVisible(False)
            return

        delta_x_min = max(float(s1["x"].min()), float(s2["x"].min()))
        delta_x_max = min(float(s1["x"].max()), float(s2["x"].max()))
        if delta_x_max <= delta_x_min:
            self.delta_panel.setVisible(False)
            return

        mask = (s1["x"] >= delta_x_min) & (s1["x"] <= delta_x_max)
        x_common = s1["x"][mask]
        t1 = s1["lap_time"][mask]
        t2 = np.interp(x_common, s2["x"], s2["lap_time"])
        delta_s = (t1 - t2) / 1000.0

        max_abs = float(np.max(np.abs(delta_s)))
        if max_abs == 0:
            max_abs = 0.001  # degenerate case — avoid an empty Y range
        y_lim = max_abs * 1.1
        self.delta_panel.set_y_range(-y_lim, y_lim)

        self.delta_panel.add_series(
            x_common,
            delta_s,
            pen=pg.mkPen("b", width=3),
            label="Δ",
            formatter=lambda v: f"{v:+.3f}s",
        )
        self.delta_panel.setVisible(True)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
