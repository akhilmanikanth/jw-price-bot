<#
.SYNOPSIS
    One-time setup on a Windows server: virtualenv, dependencies, Chromium.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Project root: $root" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is not on PATH. Install Python 3.11+ from python.org and re-run."
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}

$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt

Write-Host "Installing Chromium for Playwright..." -ForegroundColor Cyan
& $py -m playwright install chromium

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Created .env - edit it now and add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID." -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force -Path "data", "logs" | Out-Null

Write-Host ""
Write-Host "Done. Next steps:" -ForegroundColor Green
Write-Host "  1. Edit .env with your bot token and chat id"
Write-Host "  2. .venv\Scripts\python.exe main.py test-telegram"
Write-Host "  3. .venv\Scripts\python.exe main.py check --dry-run"
Write-Host "  4. Register the scheduled task:  powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1"
