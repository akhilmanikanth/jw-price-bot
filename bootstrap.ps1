<#
.SYNOPSIS
    One-shot setup: puts the weekly price check live on GitHub Actions, forever.

.DESCRIPTION
    Does everything that a GitHub token is not allowed to do on your behalf:
      1. Installs the GitHub CLI if it is missing
      2. Logs you in with the `workflow` scope
      3. Writes the two Actions workflow files
      4. Stores TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as repository secrets
      5. Commits and pushes the workflows
      6. Fires a first run so you get a message immediately

    After this the check runs every Friday at 3:00 PM Australia/Sydney in
    GitHub's cloud. Nothing on this machine needs to stay running.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File bootstrap.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File bootstrap.ps1 -BotToken "123:ABC" -ChatId "6984421703"
#>

param(
    [string]$BotToken,
    [string]$ChatId,
    [switch]$SkipFirstRun
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say($msg, $colour = "Cyan") { Write-Host "`n==> $msg" -ForegroundColor $colour }
function Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# --------------------------------------------------------------------------- #
# 0. Sanity
# --------------------------------------------------------------------------- #
if (-not (Test-Path ".git")) {
    throw "This must be run from inside the cloned repo. Try: git clone https://github.com/akhilmanikanth/jw-price-bot.git ."
}

# --------------------------------------------------------------------------- #
# 1. GitHub CLI
# --------------------------------------------------------------------------- #
Say "Checking for the GitHub CLI"
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Warn "Not found - installing via winget (this takes a minute)"
    winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "gh still not on PATH. Close this window, open a new PowerShell, and re-run."
    }
}
Ok "gh is available"

# --------------------------------------------------------------------------- #
# 2. Auth (needs the 'workflow' scope to push .github/workflows/)
# --------------------------------------------------------------------------- #
Say "Checking GitHub login"
$authed = $false
try {
    $status = gh auth status 2>&1 | Out-String
    $authed = ($LASTEXITCODE -eq 0) -and ($status -match "workflow")
} catch { $authed = $false }

if (-not $authed) {
    Warn "Logging you in - a browser window will open. Approve it, then come back here."
    gh auth login --hostname github.com --git-protocol https --web --scopes "repo,workflow"
    if ($LASTEXITCODE -ne 0) { throw "GitHub login failed." }
}
gh auth setup-git | Out-Null
Ok "Authenticated with the workflow scope"

# --------------------------------------------------------------------------- #
# 3. Credentials
# --------------------------------------------------------------------------- #
Say "Telegram credentials"
if (-not $BotToken) {
    $BotToken = Read-Host "  Bot token (from @BotFather)"
}
if (-not $ChatId) {
    $ChatId = Read-Host "  Your numeric chat id (from @userinfobot)"
}
if (-not $BotToken -or -not $ChatId) { throw "Both the token and the chat id are required." }
Ok "Captured"

# --------------------------------------------------------------------------- #
# 4. Workflow files
# --------------------------------------------------------------------------- #
Say "Writing the Actions workflows"
New-Item -ItemType Directory -Force -Path ".github\workflows" | Out-Null

# Single-quoted here-strings: PowerShell must NOT touch the ${{ }} expressions.
$weekly = @'
name: Weekly price check

on:
  schedule:
    # Friday 3:00 PM Australia/Sydney.
    # Sydney is UTC+11 during AEDT (Oct-Apr) and UTC+10 during AEST (Apr-Oct),
    # so both UTC slots are scheduled. The `--only-if-due` guard runs the check
    # only in the correct one, and the duplicate-notification guard is the
    # backstop if GitHub delays a run into the other window.
    - cron: "0 4 * * 5"   # 15:00 AEDT
    - cron: "0 5 * * 5"   # 15:00 AEST
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Scrape and print only - do not send to Telegram"
        type: boolean
        default: false
      force:
        description: "Ignore the duplicate-notification guard"
        type: boolean
        default: true
      log_level:
        description: "Log level"
        type: choice
        options: [INFO, DEBUG]
        default: INFO

concurrency:
  group: weekly-price-check
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    env:
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      TIMEZONE: ${{ vars.TIMEZONE || 'Australia/Sydney' }}
      SCHEDULE_DAY_OF_WEEK: ${{ vars.SCHEDULE_DAY_OF_WEEK || 'fri' }}
      SCHEDULE_HOUR: ${{ vars.SCHEDULE_HOUR || '15' }}
      SCHEDULE_MINUTE: ${{ vars.SCHEDULE_MINUTE || '0' }}
      ENABLED_RETAILERS: ${{ vars.ENABLED_RETAILERS }}
      USE_PLAYWRIGHT: "true"
      LOG_LEVEL: ${{ github.event.inputs.log_level || 'INFO' }}
      HISTORY_PATH: data/history.json
      DEBUG_DUMP_DIR: debug

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: requirements-core.txt

      - name: Install Python dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements-core.txt

      - name: Cache Playwright browsers
        id: playwright-cache
        uses: actions/cache@v4
        with:
          path: ~/.cache/ms-playwright
          key: playwright-${{ runner.os }}-${{ hashFiles('requirements-core.txt') }}

      - name: Install Chromium for Playwright
        run: python -m playwright install --with-deps chromium

      - name: Show current Sydney time
        run: TZ=$TIMEZONE date

      - name: Run price check
        id: run
        run: |
          ARGS="check --tolerate-failure"
          if [ "${{ github.event_name }}" = "schedule" ]; then
            ARGS="$ARGS --only-if-due"
          else
            [ "${{ github.event.inputs.force }}" = "true" ] && ARGS="$ARGS --force"
            [ "${{ github.event.inputs.dry_run }}" = "true" ] && ARGS="$ARGS --dry-run --manual"
          fi
          echo "Running: python main.py $ARGS"
          python main.py $ARGS

      - name: Upload logs and debug HTML
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: jwbot-logs-${{ github.run_number }}
          path: |
            logs/
            debug/
          retention-days: 14
          if-no-files-found: ignore

      - name: Commit updated price history
        if: github.event_name == 'schedule' || github.event.inputs.dry_run != 'true'
        run: |
          if [ -z "$(git status --porcelain data/history.json)" ]; then
            echo "No history changes to commit."
            exit 0
          fi
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/history.json
          git commit -m "chore: record price check $(TZ=$TIMEZONE date +%Y-%m-%d) [skip ci]"
          git pull --rebase --autostash origin ${{ github.ref_name }} || true
          git push
'@

$tests = @'
name: Tests

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: |
          python -m pip install --upgrade pip
          pip install -r requirements-core.txt pytest
      - run: python -m pytest -q
'@

# UTF-8 without BOM - a BOM makes GitHub reject the workflow.
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $PWD ".github\workflows\weekly-price-check.yml"), $weekly, $enc)
[System.IO.File]::WriteAllText((Join-Path $PWD ".github\workflows\tests.yml"), $tests, $enc)
Ok "Wrote .github\workflows\weekly-price-check.yml and tests.yml"

# --------------------------------------------------------------------------- #
# 5. Secrets
# --------------------------------------------------------------------------- #
Say "Storing repository secrets"
$BotToken | gh secret set TELEGRAM_BOT_TOKEN
if ($LASTEXITCODE -ne 0) { throw "Could not set TELEGRAM_BOT_TOKEN." }
$ChatId | gh secret set TELEGRAM_CHAT_ID
if ($LASTEXITCODE -ne 0) { throw "Could not set TELEGRAM_CHAT_ID." }
Ok "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID stored"

# --------------------------------------------------------------------------- #
# 6. Push
# --------------------------------------------------------------------------- #
Say "Pushing the workflows"
git add .github/workflows
$pending = git status --porcelain .github/workflows
if ($pending) {
    git -c user.name="jwbot setup" -c user.email="setup@localhost" commit -m "ci: add weekly price check and test workflows" | Out-Null
    git push
    if ($LASTEXITCODE -ne 0) { throw "Push failed. Check 'git remote -v' and your login." }
    Ok "Pushed"
} else {
    Ok "Already up to date"
}

# --------------------------------------------------------------------------- #
# 7. First run
# --------------------------------------------------------------------------- #
if (-not $SkipFirstRun) {
    Say "Triggering a first run now"
    Warn "If you have not messaged your bot in Telegram yet, do it now - Telegram"
    Warn "blocks bots from starting a conversation, so the send will fail otherwise."
    Read-Host "  Press Enter once you've sent your bot a message"

    gh workflow run "weekly-price-check.yml" -f dry_run=false -f force=true
    if ($LASTEXITCODE -ne 0) {
        Warn "Could not trigger automatically - GitHub sometimes needs a minute to"
        Warn "register a brand new workflow. Wait 60s then run:"
        Warn "  gh workflow run weekly-price-check.yml -f force=true"
    } else {
        Ok "Triggered - watching it now"
        Start-Sleep -Seconds 8
        gh run watch --exit-status 2>&1 | Out-Host
    }
}

# --------------------------------------------------------------------------- #
Say "Done" "Green"
Write-Host @"

  The check now runs every Friday at 3:00 PM Australia/Sydney,
  in GitHub's cloud. This machine does not need to stay on.

  Useful commands from this folder:
    gh run list --workflow weekly-price-check.yml    # recent runs
    gh run view --log                                # last run's log
    gh workflow run weekly-price-check.yml -f force=true   # run it right now

  To also get the Telegram /check command, run the bot continuously here:
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Mode Service

"@ -ForegroundColor Gray
