@echo off
setlocal
title Space Planner - install
echo ==========================================
echo  Space Planner - Windows installer
echo ==========================================
echo.

REM ---- 1. Python check -------------------------------------------------
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    ) else (
        echo Python 3 was not found.
        echo Install it from https://www.python.org/downloads/windows/
        echo and tick "Add python.exe to PATH" during setup, then run this file again.
        pause
        exit /b 1
    )
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not %errorlevel%==0 (
    echo Python 3.10 or newer is required. Please update Python and run this file again.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.version.split()[0])"') do echo Using Python %%v

REM ---- 2. Get the code (git clone/pull, or use the folder we are in) --------
set "REPO=https://github.com/winit007/hello-world.git"
set "BRANCH=ccr-f0f2b0c3-epz5ni"
if exist "%~dp0spaceplanner\__main__.py" (
    set "APP=%~dp0"
    echo Using the code in %~dp0
) else (
    set "APP=%~dp0space-planner\"
    where git >nul 2>nul
    if not %errorlevel%==0 (
        echo Git was not found. Either install Git from https://git-scm.com/download/win
        echo or download the repository ZIP from GitHub, unzip it, and run install.bat from inside that folder.
        pause
        exit /b 1
    )
    if exist "%APP%.git" (
        echo Updating existing checkout...
        git -C "%APP%" fetch origin %BRANCH% && git -C "%APP%" checkout %BRANCH% && git -C "%APP%" pull origin %BRANCH%
    ) else (
        echo Cloning %REPO% (branch %BRANCH%)...
        git clone --branch %BRANCH% %REPO% "%APP%"
    )
    if not exist "%APP%spaceplanner\__main__.py" (
        echo Download failed.
        pause
        exit /b 1
    )
)
cd /d "%APP%"

REM ---- 3. Virtual environment + dependencies ---------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if not %errorlevel%==0 (
        echo Could not create the virtual environment.
        pause
        exit /b 1
    )
)
echo Installing dependencies (shapely, ezdxf, PyYAML, pytest)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not %errorlevel%==0 (
    echo Dependency install failed. Check your internet connection and run install.bat again.
    pause
    exit /b 1
)

REM ---- 4. Smoke test ----------------------------------------------------------
echo Running a quick check...
".venv\Scripts\python.exe" -c "import shapely, ezdxf, yaml; import spaceplanner; print('Space Planner', spaceplanner.__version__, 'ready')"
if not %errorlevel%==0 (
    echo The install check failed.
    pause
    exit /b 1
)

REM ---- 5. Desktop shortcut to run.bat (optional) ----------------------------------
copy /y "%~dp0run.bat" "%APP%run.bat" >nul 2>nul
echo.
echo Install complete.
echo   Start the app:      double-click run.bat   (opens http://127.0.0.1:8000/)
echo   Command line:       .venv\Scripts\python -m spaceplanner generate examples\plot_40x60_east.json -o out\house
echo.
set /p START=Start the web app now? [Y/n] 
if /i not "%START%"=="n" call "%APP%run.bat"
endlocal
