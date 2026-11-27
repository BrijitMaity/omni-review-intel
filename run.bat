@echo off
setlocal
title Omni Review Intel - Master Command Center

:: Ensure Python 3.12 and its Scripts folder are on PATH
set "PY_DIR=%LOCALAPPDATA%\Programs\Python\Python312"
if exist "%PY_DIR%\python.exe" (
    set "PATH=%PY_DIR%;%PY_DIR%\Scripts;%PATH%"
)

cd /d "%~dp0"

if "%~1"=="" (
    python main.py menu
) else (
    python main.py %*
)
