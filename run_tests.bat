@echo off
setlocal
title Omni Review Intel - Test Runner

set "PY_DIR=%LOCALAPPDATA%\Programs\Python\Python312"
if exist "%PY_DIR%\python.exe" (
    set "PATH=%PY_DIR%;%PY_DIR%\Scripts;%PATH%"
)

cd /d "%~dp0omni-review-intel"

echo ========================================================
echo  Running Omni Review Intel Test Suite (70 Tests)
echo ========================================================
echo.

python -m pytest -v

pause
