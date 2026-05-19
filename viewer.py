"""F1 25 telemetry viewer — desktop GUI.

Top bar: two "Load sample" buttons each paired with a lap dropdown, and a
"Render" button on the right. Main area below is reserved for charts.
"""

import sys

import pandas as pd
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
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

        self.chart_area = QFrame()
        self.chart_area.setFrameShape(QFrame.Shape.StyledPanel)
        root.addWidget(self.chart_area, 1)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
