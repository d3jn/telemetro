# telemetrO

## Requirements

- Python 3.10+
- `python3-venv` (Debian/Ubuntu: `sudo apt install python3-venv`)
- Python packages: `PySide6`, `pandas`, `pyqtgraph` (add `pyinstaller` if you
  intend to build standalone executables)

## Setup

Linux / macOS:

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install PySide6 pandas pyqtgraph
```

Windows (PowerShell or `cmd`):

```bat
python -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install PySide6 pandas pyqtgraph
```

## Run

Recorder (listens for F1 25 UDP telemetry, writes CSV):

```bash
.venv/bin/python main.py            # Linux / macOS
.venv\Scripts\python.exe main.py    # Windows
```

Viewer (desktop GUI for comparing laps):

```bash
.venv/bin/python viewer.py          # Linux / macOS
.venv\Scripts\python.exe viewer.py  # Windows
```

## Settings

`settings.json` lives next to `main.py` / `viewer.py` when running from
source, and next to `recorder.exe` / `viewer.exe` when running a frozen
build. Keys:

- `udp_port` / `output_dir` — recorder settings.
- `viewer.downsample_hz` — optional integer. When set, the viewer
  block-averages incoming telemetry rows down to this rate before
  rendering, smoothing the traces and reducing point count. `null` or
  missing means render every source row unchanged. Source CSVs are never
  modified.

## Building Windows executables

With the venv activated and `pyinstaller` installed:

```bat
scripts\build-windows.bat
```

Produces `dist\recorder.exe` (console app) and `dist\viewer.exe` (windowed
GUI). Ship a `settings.json` alongside the executables; both apps resolve
it from the directory the `.exe` lives in.
