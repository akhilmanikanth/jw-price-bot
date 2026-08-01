# Installs the Telegram bot (/check commands) as an auto-starting background
# task on this Windows machine. Run from an **elevated** PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Mode Service
#
# Modes:
#   Service  register + start the "JWPriceBot" task (auto-starts with Windows,
#            restarts itself if it crashes)
#   Remove   stop + unregister the task
#   Run      run the bot in this window (foreground, Ctrl+C to stop)
#
# The bot answers /check /last /history /bottles /addbottle /target /status.
# The Friday summary still comes from the GitHub Actions run; this task does
# not double-send it (RUN_WEEKLY_JOB defaults to off).

param(
    [ValidateSet("Service", "Remove", "Run")]
    [string]$Mode = "Service"
)

$ErrorActionPreference = "Stop"
$TaskName = "JWPriceBot"
$Root = Split-Path -Parent $PSScriptRoot

# Prefer the project venv; fall back to python on PATH.
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "python not found - run bootstrap.ps1 first." }
    $Python = $cmd.Source
}

if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Warning ".env not found in $Root - the bot needs TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID."
    Write-Warning "If you have env.pending: Rename-Item `"$Root\env.pending`" .env"
}

switch ($Mode) {
    "Run" {
        Write-Host "Starting bot in the foreground (Ctrl+C to stop)..."
        Set-Location $Root
        & $Python main.py bot
        break
    }
    "Remove" {
        Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
        if ($?) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Removed scheduled task '$TaskName'."
        } else {
            Write-Host "Task '$TaskName' is not installed."
        }
        break
    }
    "Service" {
        $action = New-ScheduledTaskAction -Execute $Python -Argument "main.py bot" -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $settings = New-ScheduledTaskSettingsSet `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 999 `
            -RestartInterval (New-TimeSpan -Minutes 2) `
            -StartWhenAvailable `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries
        # S4U = runs as your user whether or not you're logged in, no stored password.
        $principal = New-ScheduledTaskPrincipal `
            -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType S4U `
            -RunLevel Limited

        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 3
        $state = (Get-ScheduledTask -TaskName $TaskName).State
        Write-Host "Task '$TaskName' installed (state: $state)."
        Write-Host "Logs: $Root\logs\  -  try /status in Telegram in ~10 seconds."
        break
    }
}
