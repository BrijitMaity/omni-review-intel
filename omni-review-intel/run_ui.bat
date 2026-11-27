@echo off
setlocal
title Omni Review Intel - Web UI

set "PY_DIR=%LOCALAPPDATA%\Programs\Python\Python312"
if exist "%PY_DIR%\python.exe" (
    set "PATH=%PY_DIR%;%PY_DIR%\Scripts;%PATH%"
)

cd /d "%~dp0"

echo ========================================================
echo  Starting Omni Review Intel - Amazon-Style UI
echo  Local URL: http://localhost:8501
echo ========================================================
echo.

python -m streamlit run app.py

pause
