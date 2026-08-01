@echo off
REM Run the bot continuously (Telegram /check + weekly APScheduler job).
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo Virtualenv not found. Run: powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
    exit /b 1
)
".venv\Scripts\python.exe" main.py bot
endlocal
