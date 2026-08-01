@echo off
REM Run a single price check using the project virtualenv.
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo Virtualenv not found. Run: powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
    exit /b 1
)
".venv\Scripts\python.exe" main.py check %*
endlocal
