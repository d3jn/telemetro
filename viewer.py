"""F1 25 telemetry viewer — desktop GUI.

Top bar: two "Load sample" buttons each paired with a lap dropdown, and a
"Render" button on the right. Main area below is reserved for charts.
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
        self.plot_widget = pg.PlotWidget(background="w")
        plot_item = self.plot_widget.getPlotItem()
        plot_item.setLabel("bottom", "Lap distance (m)")
        plot_item.setLabel("left", "Inputs")
        plot_item.showGrid(x=True, y=True, alpha=0.3)
        plot_item.setYRange(0, 110, padding=0)
        vb = plot_item.getViewBox()
        vb.setMouseEnabled(x=True, y=False)
        vb.setLimits(yMin=0, yMax=110)
        y_axis = plot_item.getAxis("left")
        y_axis.setTicks([[(v, str(v)) for v in (0, 20, 40, 60, 80, 100)], []])
        root.addWidget(self.plot_widget, 1)

        self._samples = {1: None, 2: None}

        self._hover_proxy = pg.SignalProxy(
            self.plot_widget.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._on_mouse_move,
        )

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
        return (
            lap_df["lap_distance"].to_numpy(),
            lap_df["throttle"].to_numpy(),
            lap_df["brake"].to_numpy(),
        )

    def _on_render(self):
        self._samples = {
            1: self._extract_lap(self.sample1),
            2: self._extract_lap(self.sample2),
        }

        plot_item = self.plot_widget.getPlotItem()
        plot_item.clear()

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
            x, throttle, brake = data
            plot_item.plot(x, throttle, pen=pens[(sample_num, "throttle")])
            plot_item.plot(x, brake, pen=pens[(sample_num, "brake")])

        xs = [d[0] for d in self._samples.values() if d is not None]
        if not xs:
            return
        x_min = min(float(a.min()) for a in xs)
        x_max = max(float(a.max()) for a in xs)

        plot_item.getViewBox().setLimits(xMin=x_min, xMax=x_max)
        plot_item.setXRange(x_min, x_max, padding=0)
        plot_item.setYRange(0, 110, padding=0)

    def _on_mouse_move(self, event):
        loaded = {n: d for n, d in self._samples.items() if d is not None}
        if not loaded:
            QToolTip.hideText()
            return
        pos = event[0]
        vb = self.plot_widget.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            QToolTip.hideText()
            return
        x_val = vb.mapSceneToView(pos).x()

        show_label = len(loaded) > 1
        lines = []
        for sample_num, (x, throttle, brake) in loaded.items():
            if x_val < x[0] or x_val > x[-1]:
                continue
            t_val = float(np.interp(x_val, x, throttle))
            b_val = float(np.interp(x_val, x, brake))
            suffix = f" {sample_num}" if show_label else ""
            lines.append(f"Throttle{suffix}: {t_val:.0f}")
            lines.append(f"Brake{suffix}: {b_val:.0f}")

        if not lines:
            QToolTip.hideText()
            return
        QToolTip.showText(QCursor.pos(), "\n".join(lines), self.plot_widget)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
