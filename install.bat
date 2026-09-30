@echo off
setlocal EnableExtensions
title Space Planner - install
echo ==========================================
echo  Space Planner - Windows installer
echo ==========================================
echo.

set "REPO=https://github.com/winit007/hello-world.git"
set "BRANCH=ccr-f0f2b0c3-epz5ni"
set "HERE=%~dp0"

REM ---------------------------------------------------------------- 1. Python
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto :have_python
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto :have_python
echo Python 3 was not found on this computer.
echo Install it from https://www.python.org/downloads/windows/
echo and tick "Add python.exe to PATH" during setup, then run this file again.
goto :fail

:have_python
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo Python 3.10 or newer is required. Please update Python and run this file again.
    goto :fail
)
for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.version.split()[0])"') do echo Using Python %%v

REM ---------------------------------------------------------------- 2. Code
if exist "%HERE%spaceplanner\__main__.py" (
    set "APP=%HERE%"
    goto :have_code
)
set "APP=%HERE%space-planner\"
where git >nul 2>nul
if errorlevel 1 (
    echo Git was not found, so the code cannot be downloaded automatically.
    echo Either install Git from https://git-scm.com/download/win and run this again,
    echo or download the ZIP from
    echo   https://github.com/winit007/hello-world/archive/refs/heads/%BRANCH%.zip
    echo unzip it, and run install.bat from inside that folder.
    goto :fail
)
if exist "%APP%.git" goto :update_code
echo Cloning %REPO% branch %BRANCH% ...
git clone --branch %BRANCH% %REPO% "%APP%"
goto :check_code

:update_code
echo Updating existing checkout in %APP% ...
git -C "%APP%" fetch origin %BRANCH%
git -C "%APP%" checkout %BRANCH%
git -C "%APP%" pull origin %BRANCH%

:check_code
if not exist "%APP%spaceplanner\__main__.py" (
    echo Download failed. Check your internet connection and try again.
    goto :fail
)

:have_code
cd /d "%APP%"
echo Using the code in %APP%

REM ---------------------------------------------------------------- 3. Environment
if exist ".venv\Scripts\python.exe" goto :have_venv
echo Creating virtual environment ...
%PY% -m venv .venv
if errorlevel 1 (
    echo Could not create the virtual environment.
    goto :fail
)

:have_venv
echo Installing dependencies: shapely, ezdxf, PyYAML, pytest ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency install failed. Check your internet connection and run install.bat again.
    goto :fail
)

REM ---------------------------------------------------------------- 4. Check
echo Running a quick check ...
".venv\Scripts\python.exe" -c "import shapely, ezdxf, yaml, spaceplanner; print('Space Planner', spaceplanner.__version__, 'ready')"
if errorlevel 1 (
    echo The install check failed. Please send the text above for help.
    goto :fail
)
if not exist "%APP%run.bat" copy /y "%HERE%run.bat" "%APP%run.bat" >nul 2>nul

echo.
echo Install complete.
echo   Start the app:   double-click run.bat  -  it opens http://127.0.0.1:8000/
echo   Command line:    .venv\Scripts\python -m spaceplanner generate examples\plot_40x60_east.json -o out\house
echo.
set "START=y"
set /p "START=Start the web app now? [Y/n] "
if /i "%START%"=="n" goto :done
call "%APP%run.bat"
goto :done

:fail
echo.
echo Installation did not finish. Read the messages above.
pause
exit /b 1

:done
pause
endlocal
exit /b 0
