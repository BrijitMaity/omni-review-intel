@echo off
setlocal
title Omni Review Intel - CLI

set "PY_DIR=%LOCALAPPDATA%\Programs\Python\Python312"
if exist "%PY_DIR%\python.exe" (
    set "PATH=%PY_DIR%;%PY_DIR%\Scripts;%PATH%"
)

cd /d "%~dp0"

if "%~1"=="" (
    echo Usage: run_cli.bat "https://www.youtube.com/watch?v=..."
    echo.
    python main.py --help
) else (
    python main.py --url "%~1" %2 %3 %4 %5
)

pause
