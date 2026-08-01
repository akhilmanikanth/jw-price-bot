# Handoff brief

Context for anyone (human or assistant) picking this project up mid-setup.

## Goal

A Telegram bot that checks **Johnnie Walker Black Label 700mL** at **Liquorland** and
**BWS** every **Friday 3:00 PM Australia/Sydney** and messages one private Telegram
chat. Priority is set-and-forget: it must keep running unattended, and when it does
break it must say so loudly rather than fail silently.

## State: code complete, go-live incomplete

All application code is written, pushed and verified (35 files; git blob SHAs
compared against the source tree, 33/33 byte-identical; 49 offline unit tests
passing). Do not rewrite it — finish the last mile.

Two things are **not** done, because the session that built this had a GitHub token
that GitHub forbids from writing workflow files or repository secrets:

1. `.github/workflows/weekly-price-check.yml` and `.github/workflows/tests.yml`
   are not in the repo
2. The `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` repository secrets are not set

`bootstrap.ps1` does both, plus pushes and triggers a first run:

```powershell
cd C:\JWPC_bot
git clone https://github.com/akhilmanikanth/jw-price-bot.git .
powershell -ExecutionPolicy Bypass -File bootstrap.ps1
```

## Design decisions worth preserving

- **Layered scraping per retailer.** BWS: JSON API, then search API, then static
  HTML, then Playwright. Liquorland: static HTML, then Playwright, then
  Playwright-on-search-page (re-resolves the product URL if it moves). A failing
  layer falls through; all layers failing produces an error *result*, never an
  exception.
- **Per-retailer isolation.** One site down still reports the other, with the error
  text inline in the message and in `logs/errors.log`.
- **DST handling.** Sydney is UTC+11 (AEDT) and UTC+10 (AEST), so both UTC cron
  slots are scheduled (`0 4 * * 5`, `0 5 * * 5`) and `--only-if-due` runs the check
  only in whichever is genuinely 3 PM local. The window is asymmetric — 15 minutes
  early, 150 minutes late — because GitHub cron runs late under load.
- **Duplicate guard.** Run key is the scheduled local date, plus a file lock. If
  both cron slots fire, exactly one message is sent.
- **Extensibility.** A new retailer is one `@register`ed `BaseScraper` subclass plus
  one import line. See "Adding another liquor store" in README.md.

## Known risks

- **Liquorland has never run against the live site.** The build sandbox could not
  reach it, so its CSS selectors and Playwright waits are educated guesses. If it
  returns no price: set `DEBUG_DUMP_DIR=debug`, re-run, read the saved HTML, fix
  `price_selectors` in `src/jwbot/scrapers/liquorland.py`. On GitHub Actions that
  HTML is already uploaded as a run artifact.
- **BWS read $55** for this product when its page was fetched during research — a
  useful sanity check on the first live run.
- **Retailer bot protection may block datacentre IPs**, appearing as HTTP 403
  rather than a missing price. Fallback is running on a machine with a residential
  IP via `scripts/install_task.ps1 -Mode Service`.
- **Telegram will not deliver until the user has messaged the bot once.** Failures
  read "chat not found".
- **Never commit the bot token.** `.env` is gitignored; credentials belong in
  GitHub repository secrets.

## Verifying it actually works

Do not stop at "the script finished":

1. `python main.py check --dry-run --force` — check the real scraped prices
2. Confirm the Actions run succeeded and the Telegram message arrived
3. `gh run list --workflow weekly-price-check.yml`

## Optional

`/check` on demand in Telegram needs a long-running process:
`scripts/setup_windows.ps1` then `scripts/install_task.ps1 -Mode Service`. The
weekly cloud run does not need it. If both run, give them separate `HISTORY_PATH`
values or two messages arrive each week.
