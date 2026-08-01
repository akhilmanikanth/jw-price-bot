<#
.SYNOPSIS
    Register the price check with Windows Task Scheduler.

.DESCRIPTION
    Two modes:
      -Mode Weekly   (default) run `main.py check` every Friday at 3:00 PM local time.
                     Simple; no process sits in memory. /check in Telegram will NOT work.
      -Mode Service  run `main.py bot` continuously at startup (APScheduler + /check).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Mode Weekly
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Mode Service
#>

param(
    [ValidateSet("Weekly", "Service")]
    [string]$Mode = "Weekly",
    [string]$TaskName = "JohnnieWalkerPriceBot",
    [string]$Time = "15:00"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    throw "Virtualenv not found at $py. Run scripts\setup_windows.ps1 first."
}

if ($Mode -eq "Weekly") {
    $taskName = $TaskName
    $action = New-ScheduledTaskAction -Execute $py -Argument "main.py check" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At $Time
    $description = "Weekly Johnnie Walker Black Label 700mL price check (Friday $Time)"
} else {
    $taskName = "$TaskName-Service"
    $action = New-ScheduledTaskAction -Execute $py -Argument "main.py bot" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $description = "Johnnie Walker price bot - continuous (Telegram /check + weekly schedule)"
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description $description `
    -RunLevel Highest | Out-Null

Write-Host "Registered scheduled task '$taskName' ($Mode mode)." -ForegroundColor Green
Write-Host "Verify with:  Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo"
Write-Host "Run now with: Start-ScheduledTask -TaskName $taskName"
