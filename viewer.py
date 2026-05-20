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
from PySide6.QtCore import Qt, Signal
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


def _format_gear(v):
    g = int(v)
    if g == -1:
        return "R"
    if g == 0:
        return "N"
    return str(g)


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

    Also emits ``x_hovered(float)`` / ``x_unhovered()`` so external listeners
    (e.g. the trajectory map) can react to the cursor's data-space X.
    """

    x_hovered = Signal(float)
    x_unhovered = Signal()

    # Pixel width forced on the left axis of every panel so stacked panels'
    # plot areas line up exactly regardless of tick-label or title length.
    Y_AXIS_WIDTH = 70

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
        self._x_linked = []  # other panels sharing this panel's X axis

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

        left_axis = self.plot_item.getAxis("left")
        left_axis.setWidth(self.Y_AXIS_WIDTH)
        if y_ticks is not None:
            left_axis.setTicks([y_ticks, []])

        layout.addWidget(self.plot_widget)

        self._hover_proxy = pg.SignalProxy(
            self.plot_widget.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._on_mouse_move,
        )

        # Crosshair line; driven externally so all linked panels move in sync.
        # ignoreBounds keeps it out of auto-range calculations.
        self._cursor_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen((100, 100, 100), width=1, style=Qt.PenStyle.DashLine),
        )
        self._cursor_line.setVisible(False)
        self.plot_item.addItem(self._cursor_line, ignoreBounds=True)

    def clear(self):
        self._series.clear()
        self.plot_item.clear()
        # plot_item.clear() detaches everything we added, including the
        # crosshair. Reattach it (hidden) so hover can show it again.
        self._cursor_line.setVisible(False)
        self.plot_item.addItem(self._cursor_line, ignoreBounds=True)

    def add_series(
        self, x, y, pen, label, sample_num=None, formatter=None, step=False
    ):
        if formatter is None:
            formatter = lambda v: f"{v:.0f}"
        plot_kwargs = {"pen": pen}
        if step:
            # Hold each sample's y until the next x, so discrete-valued
            # signals (gear, ERS mode) render as a staircase instead of a
            # diagonal connect-the-dots line.
            plot_kwargs["stepMode"] = "right"
        self.plot_item.plot(x, y, **plot_kwargs)
        self._series.append(
            {
                "x": x,
                "y": y,
                "label": label,
                "sample_num": sample_num,
                "formatter": formatter,
                "step": step,
            }
        )

    def set_x_range(self, x_min, x_max):
        # Setting X limits on a viewbox only constrains *that* viewbox — even
        # when X is linked via setXLink, panning/zooming the other panel
        # interacts with its own viewbox first. So propagate to every panel
        # sharing this X axis.
        for panel in [self, *self._x_linked]:
            panel._apply_x_range(x_min, x_max)

    def _apply_x_range(self, x_min, x_max):
        vb = self.plot_item.getViewBox()
        vb.setLimits(xMin=x_min, xMax=x_max)
        self.plot_item.setXRange(x_min, x_max, padding=0)

    def set_y_range(self, y_min, y_max):
        vb = self.plot_item.getViewBox()
        vb.setLimits(yMin=y_min, yMax=y_max)
        self.plot_item.setYRange(y_min, y_max, padding=0)

    def show_cursor_x(self, x):
        self._cursor_line.setPos(x)
        self._cursor_line.setVisible(True)

    def hide_cursor(self):
        self._cursor_line.setVisible(False)

    def link_x_to(self, other):
        self.plot_item.setXLink(other.plot_item)
        # Merge into a flat group: every member lists the others, so any
        # future set_x_range call reaches all of them.
        group = []
        for panel in [self, other, *self._x_linked, *other._x_linked]:
            if panel not in group:
                group.append(panel)
        for panel in group:
            panel._x_linked = [p for p in group if p is not panel]

    def leaveEvent(self, event):
        # sigMouseMoved only fires while the mouse is inside the scene, so
        # we need leaveEvent to catch the cursor leaving the widget entirely.
        QToolTip.hideText()
        self.x_unhovered.emit()
        super().leaveEvent(event)

    def _on_mouse_move(self, event):
        if not self._series:
            QToolTip.hideText()
            self.x_unhovered.emit()
            return
        pos = event[0]
        vb = self.plot_item.getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            QToolTip.hideText()
            self.x_unhovered.emit()
            return
        x_val = vb.mapSceneToView(pos).x()
        self.x_hovered.emit(float(x_val))

        sample_nums = {
            s["sample_num"] for s in self._series if s["sample_num"] is not None
        }
        show_label = len(sample_nums) > 1

        # Series are added in (2, 1) order so sample 1 lands on top
        # visually; sort here so the tooltip lists sample 1 first regardless.
        sorted_series = sorted(
            self._series, key=lambda s: s["sample_num"] or 0
        )
        lines = []
        for s in sorted_series:
            x, y = s["x"], s["y"]
            if x_val < x[0] or x_val > x[-1]:
                continue
            if s.get("step"):
                # Read the sample that's *at or before* x_val so the tooltip
                # reports the same value the staircase shows visually,
                # without linear-interp blending two adjacent gears.
                idx = max(0, np.searchsorted(x, x_val, side="right") - 1)
                y_val = float(y[idx])
            else:
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
        )
        self.speed_panel = ChartPanel(
            y_label="Speed (km/h)",
            y_range=(0, 350),  # placeholder; set per-render from data
        )
        self.gear_panel = ChartPanel(
            y_label="Gear",
            y_range=(-1.5, 8.5),
            y_ticks=(
                [(-1, "R"), (0, "N")]
                + [(g, str(g)) for g in range(1, 9)]
            ),
        )
        # Steering is bounded ±100 by the game; the 10% allowance gives a
        # visible gap at full lock. Range is fixed regardless of data so
        # the centre line and full-lock positions don't drift between laps.
        self.steering_panel = ChartPanel(
            y_label="Steer",
            y_range=(-110, 110),
            y_ticks=[(v, str(v)) for v in (-100, -50, 0, 50, 100)],
        )
        self.ers_panel = ChartPanel(
            y_label="ERS",
            y_range=(0, 110),
            y_ticks=[(v, str(v)) for v in (0, 20, 40, 60, 80, 100)],
            x_label="Lap distance (m)",
        )
        self.delta_panel.link_x_to(self.input_panel)
        self.speed_panel.link_x_to(self.input_panel)
        self.gear_panel.link_x_to(self.input_panel)
        self.steering_panel.link_x_to(self.input_panel)
        self.ers_panel.link_x_to(self.input_panel)

        chart_stack = QSplitter(Qt.Orientation.Vertical)
        chart_stack.addWidget(self.delta_panel)
        chart_stack.addWidget(self.input_panel)
        chart_stack.addWidget(self.speed_panel)
        chart_stack.addWidget(self.gear_panel)
        chart_stack.addWidget(self.steering_panel)
        chart_stack.addWidget(self.ers_panel)
        chart_stack.setStretchFactor(0, 1)
        chart_stack.setStretchFactor(1, 3)
        chart_stack.setStretchFactor(2, 2)
        chart_stack.setStretchFactor(3, 2)
        chart_stack.setStretchFactor(4, 2)
        chart_stack.setStretchFactor(5, 2)

        # Trajectory: free pan/zoom, square aspect ratio so the track isn't
        # distorted. Deliberately not part of the linked X group on the left.
        self.trajectory_plot = pg.PlotWidget(background="w")
        self.trajectory_item = self.trajectory_plot.getPlotItem()
        self.trajectory_item.showGrid(x=True, y=True, alpha=0.2)
        traj_vb = self.trajectory_item.getViewBox()
        traj_vb.setAspectLocked(True)
        # F1 25 uses a left-handed Y-up world, so a top-down (X, Z) plot
        # renders mirrored on a standard 2D chart. Inverting X restores the
        # real-world orientation (left/right corners and rotation direction).
        traj_vb.invertX(True)

        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(chart_stack)
        main_split.addWidget(self.trajectory_plot)
        main_split.setStretchFactor(0, 2)
        main_split.setStretchFactor(1, 1)
        root.addWidget(main_split, 1)

        self.delta_panel.setVisible(False)

        # Trajectory markers — solid filled circles, 2× the trajectory line
        # width (3px) → 6px diameter. pxMode=True keeps them screen-pixel
        # sized regardless of zoom. Hidden until a left-panel hover lands.
        self.trajectory_markers = {
            1: pg.ScatterPlotItem(
                size=6, brush=pg.mkBrush("b"), pen=pg.mkPen(None), pxMode=True
            ),
            2: pg.ScatterPlotItem(
                size=6,
                brush=pg.mkBrush((100, 150, 240)),
                pen=pg.mkPen(None),
                pxMode=True,
            ),
        }
        # Add sample 2 first so sample 1's marker sits on top wherever they
        # overlap, matching the line z-order convention.
        for sample_num in (2, 1):
            marker = self.trajectory_markers[sample_num]
            marker.setVisible(False)
            self.trajectory_item.addItem(marker)

        self._left_panels = (
            self.delta_panel,
            self.input_panel,
            self.speed_panel,
            self.gear_panel,
            self.steering_panel,
            self.ers_panel,
        )
        for panel in self._left_panels:
            panel.x_hovered.connect(self._on_chart_x_hovered)
            panel.x_unhovered.connect(self._on_chart_x_unhovered)

        self._samples = {1: None, 2: None}
        self.render_btn.clicked.connect(self._on_render)

    def _on_chart_x_hovered(self, x_val):
        for panel in self._left_panels:
            panel.show_cursor_x(x_val)
        for sample_num, data in self._samples.items():
            marker = self.trajectory_markers[sample_num]
            if data is None:
                marker.setVisible(False)
                continue
            xs = data["x"]
            if x_val < xs[0] or x_val > xs[-1]:
                marker.setVisible(False)
                continue
            wx = data["world_x"]
            wz = data["world_z"]
            valid = ~np.isnan(wx) & ~np.isnan(wz)
            if not valid.any():
                marker.setVisible(False)
                continue
            world_x = float(np.interp(x_val, xs[valid], wx[valid]))
            world_z = float(np.interp(x_val, xs[valid], wz[valid]))
            marker.setData(x=[world_x], y=[world_z])
            marker.setVisible(True)

    def _on_chart_x_unhovered(self):
        for panel in self._left_panels:
            panel.hide_cursor()
        for marker in self.trajectory_markers.values():
            marker.setVisible(False)

    @staticmethod
    def _smooth_elapsed_time_ms(x, speed_kmh, anchor_ms):
        """Reconstruct a smooth elapsed-time-at-distance curve by integrating
        speed.

        Raw ``lap_time`` ships in the LAP packet, separate from CarTelemetry,
        so UDP can deliver the two streams out of order — adjacent rows show
        jitter of ±50-200 ms and occasional backward jumps. Integrating
        ``lap_distance`` against ``speed`` (both from the same CarTelemetry
        packet) produces a self-consistent monotonic curve. The anchor is
        the first sample's reported ``lap_time``, preserving absolute time
        since the lap-start line (modulo one tiny error at row 0).
        """
        if len(x) < 2:
            return np.array([anchor_ms], dtype=float)
        speed_ms = np.maximum(speed_kmh.astype(float) / 3.6, 0.5)
        avg_speed = (speed_ms[:-1] + speed_ms[1:]) / 2
        dt_ms = np.diff(x) / avg_speed * 1000.0
        return anchor_ms + np.concatenate([[0.0], np.cumsum(dt_ms)])

    @staticmethod
    def _extract_lap(sample):
        df = sample._df
        lap = sample.lap_combo.currentData()
        if df is None or lap is None:
            return None
        lap_df = df[(df["lap_num"] == lap) & (df["lap_distance"] >= 0)]
        # Drop pre-session capture artifacts: rows where the car is parked at
        # the start line (lap_distance == 0 AND speed == 0) while lap_time
        # ticks up. F1 25 emits these when the recorder runs during the
        # waiting period before a session/lap actually starts. A real
        # line-crossing row has racing speed; a genuine on-track stop has
        # lap_distance > 0 — so this predicate only matches the artifact.
        lap_df = lap_df[
            ~((lap_df["lap_distance"] == 0) & (lap_df["speed"] == 0))
        ]
        lap_df = lap_df.sort_values("lap_distance")
        if lap_df.empty:
            return None
        return {
            "x": lap_df["lap_distance"].to_numpy(),
            "throttle": lap_df["throttle"].to_numpy(),
            "brake": lap_df["brake"].to_numpy(),
            "lap_time": lap_df["lap_time"].to_numpy(),
            "ers_pct": lap_df["ers_pct"].to_numpy(),
            "ers_mode": lap_df["ers_mode"].to_numpy(),
            "speed": lap_df["speed"].to_numpy(),
            "gear": lap_df["gear"].to_numpy(),
            # F1 25 reports steer as positive-right / negative-left. Flip
            # the sign so the panel reads left=+ and right=- as requested.
            "steer": -lap_df["steer"].to_numpy(),
            "world_x": lap_df["world_x"].to_numpy(),
            "world_z": lap_df["world_z"].to_numpy(),
        }

    def _add_ers_series(self, sample_num, data, ers_pens):
        """Plot ERS storage as connected segments coloured by ERS mode.

        Each run of consecutive identical-mode samples becomes one series.
        Adjacent runs share their boundary point so the line stays visually
        unbroken across mode transitions.
        """
        x = data["x"]
        ers_pct = data["ers_pct"]
        ers_mode = data["ers_mode"]

        mask = ~np.isnan(ers_pct) & ~np.isnan(ers_mode)
        if not mask.any():
            return
        x = x[mask]
        ers_pct = ers_pct[mask]
        ers_mode = ers_mode[mask].astype(int)

        default_pen = ers_pens[(sample_num, 0)]

        def _emit(start, stop):
            mode = int(ers_mode[start])
            pen = ers_pens.get((sample_num, mode), default_pen)
            self.ers_panel.add_series(
                x[start:stop],
                ers_pct[start:stop],
                pen=pen,
                label="ERS",
                sample_num=sample_num,
            )

        start = 0
        n = len(x)
        for i in range(1, n):
            if ers_mode[i] != ers_mode[i - 1]:
                _emit(start, i + 1)  # include transition point in old segment
                start = i
        _emit(start, n)

    def _on_render(self):
        self._samples = {
            1: self._extract_lap(self.sample1),
            2: self._extract_lap(self.sample2),
        }

        self.input_panel.clear()
        self.delta_panel.clear()
        self.speed_panel.clear()
        self.gear_panel.clear()
        self.steering_panel.clear()
        self.ers_panel.clear()
        self.trajectory_item.clear()

        input_pens = {
            (1, "throttle"): pg.mkPen("g", width=3),
            (1, "brake"): pg.mkPen("r", width=3),
            (2, "throttle"): pg.mkPen(
                (144, 238, 144), width=3, style=Qt.PenStyle.DashLine
            ),
            (2, "brake"): pg.mkPen(
                (240, 128, 128), width=3, style=Qt.PenStyle.DashLine
            ),
        }
        speed_pens = {
            1: pg.mkPen("b", width=3),
            2: pg.mkPen((100, 150, 240), width=3, style=Qt.PenStyle.DashLine),
        }
        trajectory_pens = {
            1: pg.mkPen("b", width=3),
            2: pg.mkPen((100, 150, 240), width=3, style=Qt.PenStyle.DashLine),
        }
        gear_pens = {
            1: pg.mkPen((255, 140, 0), width=3),
            2: pg.mkPen((255, 200, 140), width=3, style=Qt.PenStyle.DashLine),
        }
        steering_pens = {
            1: pg.mkPen((160, 32, 240), width=3),
            2: pg.mkPen((210, 160, 240), width=3, style=Qt.PenStyle.DashLine),
        }
        # (sample_num, ers_mode) → pen. modes: 0=none, 1=medium, 2=hotlap, 3=overtake.
        ers_pens = {
            (1, 0): pg.mkPen((128, 128, 128), width=3),
            (1, 1): pg.mkPen((0, 180, 0), width=3),
            (1, 2): pg.mkPen((255, 140, 0), width=3),
            (1, 3): pg.mkPen((220, 20, 20), width=3),
            (2, 0): pg.mkPen((180, 180, 180), width=3, style=Qt.PenStyle.DashLine),
            (2, 1): pg.mkPen((144, 238, 144), width=3, style=Qt.PenStyle.DashLine),
            (2, 2): pg.mkPen((255, 200, 140), width=3, style=Qt.PenStyle.DashLine),
            (2, 3): pg.mkPen((240, 128, 128), width=3, style=Qt.PenStyle.DashLine),
        }

        # Draw sample 2 first so sample 1's solid lines land on top.
        for sample_num in (2, 1):
            data = self._samples[sample_num]
            if data is None:
                continue
            self.input_panel.add_series(
                data["x"],
                data["throttle"],
                pen=input_pens[(sample_num, "throttle")],
                label="Throttle",
                sample_num=sample_num,
            )
            self.input_panel.add_series(
                data["x"],
                data["brake"],
                pen=input_pens[(sample_num, "brake")],
                label="Brake",
                sample_num=sample_num,
            )
            self.speed_panel.add_series(
                data["x"],
                data["speed"],
                pen=speed_pens[sample_num],
                label="Speed",
                sample_num=sample_num,
            )
            self.gear_panel.add_series(
                data["x"],
                data["gear"],
                pen=gear_pens[sample_num],
                label="Gear",
                sample_num=sample_num,
                formatter=_format_gear,
                step=True,
            )
            self.steering_panel.add_series(
                data["x"],
                data["steer"],
                pen=steering_pens[sample_num],
                label="Steer",
                sample_num=sample_num,
            )
            self._add_ers_series(sample_num, data, ers_pens)

            wx = data["world_x"]
            wz = data["world_z"]
            valid = ~np.isnan(wx) & ~np.isnan(wz)
            if valid.any():
                self.trajectory_item.plot(
                    wx[valid], wz[valid], pen=trajectory_pens[sample_num]
                )

        # Trajectory fits all data with 10% padding. autoRange + padding
        # respects the aspect lock automatically.
        self.trajectory_item.getViewBox().autoRange(padding=0.1)

        # Re-add hover markers — clear() removed them. Added last so they
        # render on top of the trajectory lines; sample 2 first so sample 1
        # sits on top wherever they overlap.
        for sample_num in (2, 1):
            marker = self.trajectory_markers[sample_num]
            marker.setVisible(False)
            self.trajectory_item.addItem(marker)

        xs = [d["x"] for d in self._samples.values() if d is not None]
        if not xs:
            self.delta_panel.setVisible(False)
            return
        x_min = min(float(a.min()) for a in xs)
        x_max = max(float(a.max()) for a in xs)
        self.input_panel.set_x_range(x_min, x_max)

        max_speed = max(
            float(np.max(d["speed"]))
            for d in self._samples.values()
            if d is not None
        )
        self.speed_panel.set_y_range(0, max_speed * 1.1)

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

        # Speed integration gives us a smooth time(distance) curve up to a
        # constant. Anchor at 0 here — the absolute level is set by the
        # end-anchor below.
        t1_full = self._smooth_elapsed_time_ms(s1["x"], s1["speed"], 0.0)
        t2_full = self._smooth_elapsed_time_ms(s2["x"], s2["speed"], 0.0)

        mask = (s1["x"] >= delta_x_min) & (s1["x"] <= delta_x_max)
        x_common = s1["x"][mask]
        t1 = t1_full[mask]
        t2 = np.interp(x_common, s2["x"], t2_full)
        raw_delta_ms = t1 - t2

        if len(x_common) < 2:
            self.delta_panel.setVisible(False)
            return

        # Linear correction: zero the start AND land the end exactly on the
        # lap-time difference (= subtracting the dropdown values). Both
        # constraints can't be hit with a pure shift, so we subtract
        # raw_delta[0] to anchor the start and add a constant-slope-in-X
        # term to bring the end into place. Local curve shape — i.e. WHERE
        # in the lap each driver gains/loses time — is preserved relative
        # to that linear baseline.
        lap_diff_ms = (
            float(np.max(s1["lap_time"])) - float(np.max(s2["lap_time"]))
        )
        raw_start = raw_delta_ms[0]
        raw_end = raw_delta_ms[-1]
        slope = (lap_diff_ms - (raw_end - raw_start)) / (
            x_common[-1] - x_common[0]
        )
        delta_s = (
            raw_delta_ms - raw_start + slope * (x_common - x_common[0])
        ) / 1000.0

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
