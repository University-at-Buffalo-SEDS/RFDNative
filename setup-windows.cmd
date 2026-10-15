@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python setup.py
) else (
    py -3 setup.py
)
if errorlevel 1 (
    echo Setup failed. Install Python 3.10 or newer with Tcl/Tk and the Python launcher.
    pause
    exit /b 1
)
pause
