# telemetrO

## Requirements

- Python 3.10+
- `python3-venv` (Debian/Ubuntu: `sudo apt install python3.12-venv`)
- Python packages: `PySide6`, `pandas`, `pyqtgraph`

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install PySide6 pandas pyqtgraph
```

## Run

Recorder (listens for F1 25 UDP telemetry, writes CSV):

```bash
.venv/bin/python main.py
```

Viewer (desktop GUI for comparing laps):

```bash
.venv/bin/python viewer.py
```
