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

Recorder (listens for F1 25 UDP telemetry in the 2025 or 2026 format, writes CSV):

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
- `udp_format` — `"2025"`, `"2026"` (2026 Season Pack) or `"auto"`
  (default), which locks on to whichever format the first packet uses.
  Must match the game's "UDP Format" telemetry option; the recorder warns
  if it receives the other format.
- `viewer.downsample_hz` — optional integer. When set, the viewer
  block-averages incoming telemetry rows down to this rate before
  rendering, smoothing the traces and reducing point count. `null` or
  missing means render every source row unchanged. Source CSVs are never
  modified.

## Recordings

One CSV per driver per session:
`YYYY_MM_DD_<track>_<driver>_<session_type>.csv`. The columns are the same
whichever UDP format was recorded, and cover both regulation sets:

- `regs` — `2025` or `2026`, the rules that car runs under. Not the same
  as the UDP format: the 2026 format also carries pre-2026 cars.
- `low_drag` — `1` while the wing is in its low-drag state: DRS open on
  2025 cars, Active Aero straight mode on 2026 cars.
- `overtake_active` — 2026 Overtake Mode (`0`/`1`); empty on 2025 cars.
- `ers_store` — ERS store energy in Joules. The viewer converts it to a
  percentage using the store capacity for the car's `regs`.
- `ers_mode` — raw deploy mode: 0 none, 1 medium, 2 hotlap, 3 boost (the
  2025 spec calls mode 3 "overtake").

Recordings from older recorder builds (before 2026 support) cannot be
opened in the viewer. If the recorder finds an old-format file with the
same name, it renames it to `*.legacy_<timestamp>.csv` and starts a new file.

## Tests

```bash
python3 -m unittest discover -t . -s tests -v
```

The parser and recorder tests use only the standard library.

## Building Windows executables

With the venv activated and `pyinstaller` installed:

```bat
scripts\build-windows.bat
```

Produces `dist\recorder.exe` (console app) and `dist\viewer.exe` (windowed
GUI). Ship a `settings.json` alongside the executables; both apps resolve
it from the directory the `.exe` lives in.
