@echo off
rem Start facekit by double-clicking this file.
rem Looks for a conda environment named facekit, then a local .venv,
rem then falls back to whatever python is on the PATH.

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_interface.py
    goto :done
)

where conda >nul 2>nul
if %errorlevel%==0 (
    call conda activate facekit 2>nul
    if %errorlevel%==0 (
        python run_interface.py
        goto :done
    )
)

python run_interface.py

:done
if %errorlevel% neq 0 (
    echo.
    echo facekit exited with an error. Run this to find out what is missing:
    echo     python -m facekit env
    echo.
    pause
)
