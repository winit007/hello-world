@echo off
setlocal
title Space Planner
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    if exist "space-planner\.venv\Scripts\python.exe" cd /d "%~dp0space-planner"
)
if not exist ".venv\Scripts\python.exe" (
    echo Space Planner is not installed here. Run install.bat first.
    pause
    exit /b 1
)
set "PORT=8000"
if not "%~1"=="" set "PORT=%~1"
echo Starting Space Planner on http://127.0.0.1:%PORT%/  (close this window to stop)
start "" "http://127.0.0.1:%PORT%/"
".venv\Scripts\python.exe" -m spaceplanner serve --host 127.0.0.1 --port %PORT%
endlocal
