@echo off
REM Prefer UTF-8 console so Chinese messages show correctly on modern Windows.
chcp 65001 >nul 2>&1
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [X] Python not found. Install Python 3.10+ and tick "Add to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo [*] First run: creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [X] Failed to create .venv
        pause
        exit /b 1
    )
    echo [*] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [X] Failed to install requirements
        pause
        exit /b 1
    )
)

echo [*] Starting DiskWatch...
start "" ".venv\Scripts\pythonw.exe" "run.pyw"
exit /b 0
