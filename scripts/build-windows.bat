@echo off
REM Build Windows executables with PyInstaller.
REM
REM Run from anywhere; the script cd's to the project root itself.
REM Assumes the project venv is activated, or that `python` and PyInstaller
REM are otherwise on PATH.
REM
REM Outputs into dist\:
REM   recorder.exe  - console app (UDP listener / CSV writer)
REM   viewer.exe    - windowed GUI for comparing laps
REM
REM Remember to ship settings.json next to the .exe files; both apps read it
REM from the executable's directory when frozen.

setlocal
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."

REM Wipe previous artifacts so a stale .spec or build\ tree can't poison the
REM next run (PyInstaller will silently reuse an existing .spec).
if exist build rd /s /q build
if exist dist rd /s /q dist
if exist recorder.spec del /q recorder.spec
if exist viewer.spec del /q viewer.spec

echo === Building recorder.exe ===
python -m PyInstaller --onefile --console --name recorder main.py
if errorlevel 1 goto :fail

echo === Building viewer.exe ===
REM --collect-submodules pyqtgraph: pyqtgraph imports many submodules lazily
REM at runtime, which PyInstaller's static analysis misses.
python -m PyInstaller --onefile --noconsole --name viewer ^
    --collect-submodules pyqtgraph ^
    viewer.py
if errorlevel 1 goto :fail

echo.
echo === Done. Artifacts in dist\ ===
popd
endlocal
exit /b 0

:fail
echo.
echo === Build failed ===
popd
endlocal
exit /b 1
