"""F1 25 telemetry viewer — desktop GUI.

Top bar: two "Load sample" buttons each paired with a lap dropdown, and a
"Render" button on the right. Main area holds stacked chart panels with a
shared X axis: a time-delta panel (shown only when both samples are loaded)
above an inputs panel.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _load_viewer_settings():
    """Read the "viewer" section of settings.json. Missing file or section
    is fine — we just return defaults so the app works out of the box."""
    path = os.path.join(_base_dir(), "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data.get("viewer", {}) or {}


def _downsample_lap_df(lap_df, target_hz):
    """Block-average consecutive rows down to ``target_hz``.

    Estimates the source rate from the median ``lap_time`` interval, then
    bins every ``round(source_hz / target_hz)`` consecutive rows. Continuous
    columns are averaged (smoothing the trace); discrete columns (gear,
    ers_mode, lap_num) take the first value of each bin so step renders and
    integer tooltips stay sensible. Pure in-memory — the source CSV is not
    touched.
    """
    if target_hz is None or target_hz <= 0:
        return lap_df
    if len(lap_df) < 2:
        return lap_df

    dt_ms = np.diff(lap_df["lap_time"].to_numpy())
    dt_ms = dt_ms[dt_ms > 0]  # drop jitter-induced backward jumps
    if len(dt_ms) == 0:
        return lap_df
    source_hz = 1000.0 / float(np.median(dt_ms))

    bin_size = int(round(source_hz / target_hz))
    if bin_size <= 1:
        return lap_df

    bins = np.arange(len(lap_df)) // bin_size
    first_cols = ("gear", "ers_mode", "lap_num", "lap_run", "sector_idx")
    # sector1_time / sector2_time / last_lap_time are 0 then latched to a
    # game-stamped value; max() preserves the latch through bin boundaries
    # rather than smearing the 0→value transition with a mean.
    max_cols = ("sector1_time", "sector2_time", "last_lap_time")
    agg = {}
    for col in lap_df.columns:
        if col in first_cols:
            agg[col] = "first"
        elif col in max_cols:
            agg[col] = "max"
        else:
            agg[col] = "mean"
    return lap_df.groupby(bins).agg(agg)


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

    state_changed = Signal()

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
        else:
            self._df = df
            self._path = path
            self._populate_laps()
        self.state_changed.emit()

    def _populate_laps(self):
        """Build the lap dropdown. The lap time shown for each entry is the
        game-authoritative ``last_lap_time`` stamped on the first row of the
        following (lap_num, lap_run) block — that's the value the game
        writes the instant the S/F line is crossed, with no UDP jitter. For
        flashback-abandoned blocks (next block has same/lower lap_num or
        same lap_num + different lap_run) and the trailing block of the
        recording (no successor), we fall back to ``max(lap_time)`` so the
        partial lap still appears with a representative time."""
        self.lap_combo.clear()
        if self._df is None or self._df.empty:
            self.lap_combo.setEnabled(False)
            return

        df = self._df.reset_index(drop=True)
        lap_nums = df["lap_num"].to_numpy()
        lap_runs = df["lap_run"].to_numpy()
        last_lap_times = df["last_lap_time"].to_numpy()
        lap_times = df["lap_time"].to_numpy()
        n = len(df)
        if n == 0:
            self.lap_combo.setEnabled(False)
            return

        # Indices where a new (lap_num, lap_run) block starts.
        starts = [0]
        for i in range(1, n):
            if lap_nums[i] != lap_nums[i - 1] or lap_runs[i] != lap_runs[i - 1]:
                starts.append(i)

        entries = []  # (lap_num, lap_run, time_ms)
        for k, start in enumerate(starts):
            end = (starts[k + 1] - 1) if k + 1 < len(starts) else n - 1
            ln = lap_nums[start]
            lr = lap_runs[start]
            if pd.isna(ln) or pd.isna(lr):
                continue
            ln, lr = int(ln), int(lr)
            time_ms = None
            if k + 1 < len(starts):
                next_start = starts[k + 1]
                next_ln = lap_nums[next_start]
                if not pd.isna(next_ln) and int(next_ln) == ln + 1:
                    llt = last_lap_times[next_start]
                    if not pd.isna(llt) and llt > 0:
                        time_ms = float(llt)
            if time_ms is None:
                block_lt = lap_times[start : end + 1]
                valid = ~pd.isna(block_lt)
                if valid.any():
                    m = float(block_lt[valid].max())
                    if m > 0:
                        time_ms = m
            if time_ms is None:
                continue
            entries.append((ln, lr, time_ms))

        # Renumber runs per-lap from 1 so "(run 1) / (run 2)" beats the raw
        # global counter, which can skip values across other laps.
        runs_per_lap = {}
        for ln, _, _ in entries:
            runs_per_lap[ln] = runs_per_lap.get(ln, 0) + 1

        display_index = {}
        for ln, lr, time_ms in entries:
            display_index[ln] = display_index.get(ln, 0) + 1
            if runs_per_lap[ln] > 1:
                text = f"#{ln} (run {display_index[ln]}) {_format_lap_time_ms(time_ms)}"
            else:
                text = f"#{ln} {_format_lap_time_ms(time_ms)}"
            self.lap_combo.addItem(text, userData=(ln, lr, time_ms))

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

    def set_x_label(self, label):
        """Set or hide the X-axis title. Used by MainWindow to keep the
        label on whichever panel is currently bottom-most visible."""
        axis = self.plot_item.getAxis("bottom")
        if label:
            axis.setLabel(label)
            axis.showLabel(True)
        else:
            axis.showLabel(False)

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
        lines.append(f"Distance: {x_val:.0f} m")
        QToolTip.showText(QCursor.pos(), "\n".join(lines), self.plot_widget)


class StatsDialog(QDialog):
    """Modal table of per-sample lap statistics, recomputed on every open."""

    def __init__(self, samples, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stats")
        self.setModal(True)

        loaded = {n: d for n, d in samples.items() if d is not None}
        sample_nums = sorted(loaded.keys())
        col_headers = ["Statistic"] + [f"Sample {n}" for n in sample_nums]
        rows = self._compute_rows(loaded)

        table = QTableWidget(len(rows), len(col_headers))
        table.setHorizontalHeaderLabels(col_headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        for r, (stat_name, values) in enumerate(rows):
            table.setItem(r, 0, QTableWidgetItem(stat_name))
            for c, n in enumerate(sample_nums, start=1):
                table.setItem(r, c, QTableWidgetItem(values.get(n, "—")))
        table.resizeColumnsToContents()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(table)
        layout.addLayout(btn_row)

        self.resize(450, 200)

    @staticmethod
    def _compute_rows(loaded):
        """Returns [(statistic_name, {sample_num: formatted_value}), ...]."""
        return [
            (
                "Top speed",
                {
                    n: f"{int(np.max(d['speed']))} km/h"
                    for n, d in loaded.items()
                },
            ),
        ]


class ChartsSettingsDialog(QDialog):
    """Modal dialog with a single "Charts" group of checkboxes — one per
    available chart panel. The caller passes the current visibility state
    and reads back the chosen state on accept(). At least one chart must
    stay checked; up to ``MAX_SELECTED`` may be checked (the cap is here
    in anticipation of more panels — with 6 it's unreachable today)."""

    MAX_SELECTED = 6

    def __init__(self, chart_specs, current_state, parent=None):
        """``chart_specs`` is an ordered list of ``(key, label)`` tuples.
        ``current_state`` is a ``{key: bool}`` mapping; missing keys default
        to True."""
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)

        self._checkboxes = {}
        group = QGroupBox("Charts")
        gl = QVBoxLayout(group)
        for key, label in chart_specs:
            cb = QCheckBox(label)
            cb.setChecked(current_state.get(key, True))
            cb.stateChanged.connect(self._refresh_constraints)
            gl.addWidget(cb)
            self._checkboxes[key] = cb

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.accept)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(group)
        layout.addLayout(btn_row)

        self._refresh_constraints()

    def _refresh_constraints(self, *_):
        checked = [cb for cb in self._checkboxes.values() if cb.isChecked()]
        unchecked = [cb for cb in self._checkboxes.values() if not cb.isChecked()]
        for cb in self._checkboxes.values():
            cb.setEnabled(True)
        if len(checked) == 1:
            checked[0].setEnabled(False)  # can't deselect the last one
        if len(checked) >= self.MAX_SELECTED:
            for cb in unchecked:
                cb.setEnabled(False)  # cap on total selected

    def get_state(self):
        return {key: cb.isChecked() for key, cb in self._checkboxes.items()}


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

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self._on_settings_clicked)
        top.addWidget(self.settings_btn)

        self.stats_btn = QPushButton("Stats")
        self.stats_btn.setEnabled(False)
        self.stats_btn.clicked.connect(self._on_stats_clicked)
        top.addWidget(self.stats_btn)

        self.sample1.state_changed.connect(self._update_stats_btn)
        self.sample2.state_changed.connect(self._update_stats_btn)

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
        )
        self.fuel_panel = ChartPanel(
            y_label="Fuel used (kg)",
            y_range=(0, 5),  # placeholder; recomputed per-render from data
        )
        self.delta_panel.link_x_to(self.input_panel)
        self.speed_panel.link_x_to(self.input_panel)
        self.gear_panel.link_x_to(self.input_panel)
        self.steering_panel.link_x_to(self.input_panel)
        self.ers_panel.link_x_to(self.input_panel)
        self.fuel_panel.link_x_to(self.input_panel)

        chart_stack = QSplitter(Qt.Orientation.Vertical)
        chart_stack.addWidget(self.delta_panel)
        chart_stack.addWidget(self.input_panel)
        chart_stack.addWidget(self.speed_panel)
        chart_stack.addWidget(self.gear_panel)
        chart_stack.addWidget(self.steering_panel)
        chart_stack.addWidget(self.ers_panel)
        chart_stack.addWidget(self.fuel_panel)
        chart_stack.setStretchFactor(0, 1)
        chart_stack.setStretchFactor(1, 3)
        chart_stack.setStretchFactor(2, 2)
        chart_stack.setStretchFactor(3, 2)
        chart_stack.setStretchFactor(4, 2)
        chart_stack.setStretchFactor(5, 2)
        chart_stack.setStretchFactor(6, 2)

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

        # Ordered (key, label) specs drive the Settings dialog and the
        # X-label bottom-most-visible lookup. Order here also matches the
        # splitter, so keep them in sync.
        self._chart_specs = [
            ("delta", "Delta"),
            ("inputs", "Inputs"),
            ("speed", "Speed"),
            ("gear", "Gear"),
            ("steering", "Steering"),
            ("ers", "ERS"),
            ("fuel", "Fuel"),
        ]
        self._chart_panels = {
            "delta": self.delta_panel,
            "inputs": self.input_panel,
            "speed": self.speed_panel,
            "gear": self.gear_panel,
            "steering": self.steering_panel,
            "ers": self.ers_panel,
            "fuel": self.fuel_panel,
        }
        # All on by default except fuel — keeps the historical layout intact
        # for users who don't care about fuel.
        self._chart_visibility = {key: True for key, _ in self._chart_specs}
        self._chart_visibility["fuel"] = False
        self.fuel_panel.setVisible(False)
        # Whether the last _on_render actually produced delta data. The
        # delta panel is shown only when the user wants it AND this is True.
        self._delta_renderable = False

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
            self.fuel_panel,
        )
        for panel in self._left_panels:
            panel.x_hovered.connect(self._on_chart_x_hovered)
            panel.x_unhovered.connect(self._on_chart_x_unhovered)

        self._samples = {1: None, 2: None}
        self._downsample_hz = _load_viewer_settings().get("downsample_hz")
        self.render_btn.clicked.connect(self._on_render)

        self._update_x_label()

    def _on_settings_clicked(self):
        dialog = ChartsSettingsDialog(
            self._chart_specs, self._chart_visibility, parent=self
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._chart_visibility = dialog.get_state()
            self._update_panel_visibility()

    def _update_panel_visibility(self):
        for key, panel in self._chart_panels.items():
            wanted = self._chart_visibility.get(key, True)
            if key == "delta":
                # Delta has the data-side gate: it only makes sense when both
                # samples are loaded and a delta curve was actually computed.
                panel.setVisible(wanted and self._delta_renderable)
            else:
                panel.setVisible(wanted)
        self._update_x_label()

    def _update_x_label(self):
        label = "Lap distance (m)"
        bottom_visible = None
        for panel in self._left_panels:
            if panel.isVisible():
                bottom_visible = panel
        for panel in self._left_panels:
            panel.set_x_label(label if panel is bottom_visible else None)

    def _update_stats_btn(self):
        has_data = any(
            sample._df is not None
            and sample.lap_combo.currentData() is not None
            for sample in (self.sample1, self.sample2)
        )
        self.stats_btn.setEnabled(has_data)

    def _on_stats_clicked(self):
        samples = {
            1: self._extract_lap(self.sample1),
            2: self._extract_lap(self.sample2),
        }
        StatsDialog(samples, parent=self).exec()

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
    def _calibrated_elapsed_time_ms(data):
        """Piecewise-linear calibration of speed-integrated time against the
        game's sector and lap-total stamps.

        Anchors (when available): t(S1 boundary) = sector1_time,
        t(S2 boundary) = sector1_time + sector2_time, t(last row) =
        lap_total_ms. Between anchors, the relative integration is stretched
        linearly so its endpoints match — the SHAPE within each segment is
        preserved (which is what tells you where on track time was gained
        or lost) while the absolute level matches the game's authoritative
        stamps. Sector 1 with no start-of-lap anchor gets a pure shift; if
        any anchor is unavailable (incomplete lap, no boundary crossed),
        the corresponding segment falls back to the previous segment's
        anchor + raw integration.
        """
        x = data["x"]
        speed = data["speed"]
        sector_idx = data["sector_idx"].astype(int)
        s1_ms = data.get("sector1_time_ms") or 0.0
        s2_ms = data.get("sector2_time_ms") or 0.0
        total_ms = data.get("lap_total_ms")

        t_rel = MainWindow._smooth_elapsed_time_ms(x, speed, 0.0)
        t = t_rel.copy()
        n = len(t)
        if n == 0:
            return t

        in_s2 = sector_idx >= 1
        in_s3 = sector_idx >= 2
        i_s1 = int(np.argmax(in_s2)) if in_s2.any() else -1
        i_s2 = int(np.argmax(in_s3)) if in_s3.any() else -1

        # Sector 1: pure shift (no lap-start anchor; data may begin mid-sector).
        if i_s1 > 0 and s1_ms > 0:
            shift = s1_ms - t_rel[i_s1]
            t[: i_s1 + 1] = t_rel[: i_s1 + 1] + shift

        # Sector 2: piecewise linear stretch between S1 and S2 anchors.
        if i_s1 >= 0 and i_s2 > i_s1 and s1_ms > 0 and s2_ms > 0:
            rel_lo = t_rel[i_s1]
            rel_hi = t_rel[i_s2]
            if rel_hi > rel_lo:
                scale = s2_ms / (rel_hi - rel_lo)
                t[i_s1 + 1 : i_s2 + 1] = (
                    s1_ms + (t_rel[i_s1 + 1 : i_s2 + 1] - rel_lo) * scale
                )

        # Sector 3: piecewise linear stretch between S2 anchor and lap end.
        if (
            i_s2 > 0
            and total_ms is not None
            and s1_ms > 0
            and s2_ms > 0
        ):
            s3_ms = total_ms - s1_ms - s2_ms
            rel_lo = t_rel[i_s2]
            rel_hi = t_rel[-1]
            if rel_hi > rel_lo and s3_ms > 0:
                scale = s3_ms / (rel_hi - rel_lo)
                t[i_s2 + 1 :] = (
                    (s1_ms + s2_ms) + (t_rel[i_s2 + 1 :] - rel_lo) * scale
                )

        return t

    def _extract_lap(self, sample):
        df = sample._df
        selection = sample.lap_combo.currentData()
        if df is None or selection is None:
            return None
        lap, lap_run, lap_total_ms = selection
        lap_df = df[df["lap_num"] == lap]
        if lap_run is not None and "lap_run" in df.columns:
            lap_df = lap_df[lap_df["lap_run"] == lap_run]
        lap_df = lap_df[lap_df["lap_distance"] >= 0]
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
        lap_df = _downsample_lap_df(lap_df, self._downsample_hz)
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
            "sector_idx": lap_df["sector_idx"].to_numpy(),
            # sector1_time / sector2_time are 0 until the corresponding
            # boundary is crossed, then latched to the game-stamped value.
            # max() picks the latched value (or stays 0 if never crossed).
            "sector1_time_ms": float(lap_df["sector1_time"].max() or 0),
            "sector2_time_ms": float(lap_df["sector2_time"].max() or 0),
            # Game-authoritative total from the next block's last_lap_time;
            # None when the recording ended before the line crossing.
            "lap_total_ms": float(lap_total_ms) if lap_total_ms else None,
            # Cumulative fuel consumed since the first row of this lap. Uses
            # the first row's fuel_level as the baseline so the chart starts
            # at 0 even when the sample is missing rows near lap_distance=0.
            "fuel_used": float(lap_df["fuel_level"].iloc[0])
            - lap_df["fuel_level"].to_numpy(),
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
        self.fuel_panel.clear()
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
        fuel_pens = {
            1: pg.mkPen((218, 165, 32), width=3),
            2: pg.mkPen((240, 210, 130), width=3, style=Qt.PenStyle.DashLine),
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
            self.fuel_panel.add_series(
                data["x"],
                data["fuel_used"],
                pen=fuel_pens[sample_num],
                label="Fuel",
                sample_num=sample_num,
                formatter=lambda v: f"{v:.2f} kg",
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

        self._delta_renderable = False

        xs = [d["x"] for d in self._samples.values() if d is not None]
        if xs:
            x_min = min(float(a.min()) for a in xs)
            x_max = max(float(a.max()) for a in xs)
            self.input_panel.set_x_range(x_min, x_max)

            max_speed = max(
                float(np.max(d["speed"]))
                for d in self._samples.values()
                if d is not None
            )
            self.speed_panel.set_y_range(0, max_speed * 1.1)

            max_fuel = max(
                float(np.max(d["fuel_used"]))
                for d in self._samples.values()
                if d is not None
            )
            if max_fuel <= 0:
                max_fuel = 0.1  # avoid an empty Y range
            self.fuel_panel.set_y_range(0, max_fuel * 1.1)

        s1 = self._samples[1]
        s2 = self._samples[2]
        if s1 is not None and s2 is not None:
            delta_x_min = max(float(s1["x"].min()), float(s2["x"].min()))
            delta_x_max = min(float(s1["x"].max()), float(s2["x"].max()))
            if delta_x_max > delta_x_min:
                # Calibrated delta — see _calibrated_elapsed_time_ms.
                t1_full = self._calibrated_elapsed_time_ms(s1)
                t2_full = self._calibrated_elapsed_time_ms(s2)

                mask = (s1["x"] >= delta_x_min) & (s1["x"] <= delta_x_max)
                x_common = s1["x"][mask]
                if len(x_common) >= 2:
                    t1 = t1_full[mask]
                    t2 = np.interp(x_common, s2["x"], t2_full)
                    delta_s = (t1 - t2) / 1000.0

                    max_abs = float(np.max(np.abs(delta_s)))
                    if max_abs == 0:
                        max_abs = 0.001
                    y_lim = max_abs * 1.1
                    self.delta_panel.set_y_range(-y_lim, y_lim)
                    self.delta_panel.add_series(
                        x_common,
                        delta_s,
                        pen=pg.mkPen("b", width=3),
                        label="Δ",
                        formatter=lambda v: f"{v:+.3f}s",
                    )
                    self._delta_renderable = True

        self._update_panel_visibility()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
