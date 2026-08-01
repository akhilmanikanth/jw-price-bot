# Whisky Price Bot

Checks whisky prices at **Liquorland** and **BWS** every **Friday at 3:00 PM
Australia/Sydney**, and sends one message to one private Telegram chat.

Tracked bottles (see `src/jwbot/products.py`):

| Product | Treatment |
| --- | --- |
| Johnnie Walker Black Label 700mL | full block |
| Johnnie Walker Black Label 1 Litre | full block |
| Johnnie Walker Blue Label 700mL | watch-list line |
| Ballantine's Finest 700mL | watch-list line |
| Ballantine's Finest 1 Litre | watch-list line |
| Ballantine's 12 Year Old 700mL | watch-list line |

```
Whisky Weekly Price Update

Johnnie Walker Black Label 700mL
Liquorland: $65.00   +$2.00 (was $63.00)
BWS: $55.00   no change
Cheapest today: BWS - $55.00 (save $10.00)

Johnnie Walker Black Label 1 Litre
Liquorland: $88.00   first reading
BWS: $84.00   first reading
Cheapest today: BWS - $84.00 (save $4.00)

Also watching
Blue Label 700mL: Liquorland $255.00 (+$5.00) . BWS $250.00
No change: Ballantine's Finest 700mL $50.00, Ballantine's 12YO 700mL $65.00

Checked: Friday, 07 Aug 2026 - 3:00 PM (AEST)
```

Main bottles always get the full retailer-by-retailer block. Watch-list
bottles get one compact line when something moved (or errored - never
silently), and collapse into a single "No change" line otherwise.

(The real message uses emoji - they are stripped here so the README renders cleanly everywhere.)

## Features

- **Two run modes** - GitHub Actions cron (nothing to host), or a long-running
  process on your Windows server / Docker for the `/check` command.
- **Layered scraping** - retailer JSON APIs first, then static HTML (JSON-LD,
  `__NEXT_DATA__`, meta tags), then a real Chromium browser via Playwright.
  If one layer breaks, the next one usually still works.
- **Never fails silently** - if one site is unreachable the other still reports,
  and the error text is included in the Telegram message and written to
  `logs/errors.log`.
- **Week-on-week comparison** - up / down / unchanged, with the previous price.
- **Duplicate protection** - a run key per scheduled date plus a file lock, so a
  double-fired scheduler can't send the same message twice.
- **Easy to extend** - a new retailer is one small file (see below).

## Commands

| Command | What it does |
| --- | --- |
| `python main.py check` | Run one check and send it to Telegram |
| `python main.py check --dry-run` | Scrape and print, don't send |
| `python main.py check --only-if-due` | Exit quietly unless it's the scheduled window |
| `python main.py bot` | Long-running: `/check` command + weekly APScheduler job |
| `python main.py test-telegram` | Verify the token and chat id |
| `python main.py history` | Print recorded prices |
| `python main.py retailers` | List registered retailers |

Telegram commands (bot mode): `/check`, `/last`, `/history`, `/status`, `/help`.
Every command is rejected unless it comes from a chat id in `TELEGRAM_CHAT_ID` /
`TELEGRAM_ALLOWED_CHAT_IDS`, so the bot is private even though anyone can find it.

---

## 1. Get your Telegram credentials

1. In Telegram, message **@BotFather** then `/newbot` and copy the **token**.
2. Message **@userinfobot** and copy your numeric **chat id**.
3. Send your new bot any message once (Telegram won't let a bot start a
   conversation with you until you do).

---

## 2. Run it on GitHub Actions (no server needed)

The scheduled workflow lives in `.github/workflows/weekly-price-check.yml`.

**Add the secrets:** repo, then *Settings*, *Secrets and variables*, *Actions*, *New repository secret*

| Secret | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | the BotFather token |
| `TELEGRAM_CHAT_ID` | your numeric chat id |

Optional repository **variables** (same page, *Variables* tab): `TIMEZONE`,
`SCHEDULE_DAY_OF_WEEK`, `SCHEDULE_HOUR`, `SCHEDULE_MINUTE`, `ENABLED_RETAILERS`.

**Test it:** *Actions*, *Weekly price check*, *Run workflow*. Tick **dry_run**
first to check the scrapers without sending anything; the log prints the exact
message and the raw HTML is uploaded as an artifact if something fails.

### About the schedule

Sydney is UTC+11 in summer (AEDT) and UTC+10 in winter (AEST), so the workflow
registers **both** UTC slots - `0 4 * * 5` and `0 5 * * 5` - and `--only-if-due`
runs the check only in the slot that is actually 3:00 PM locally. GitHub cron can
be delayed by tens of minutes under load, so the "due" window extends 2.5 hours
*past* 3:00 PM but only 15 minutes before it. If both slots somehow run, the
duplicate-notification guard means you still get exactly one message.

Price history is committed back to `data/history.json` after each run, which is
what makes the week-on-week comparison work.

---

## 3. Run it on your Windows server

```powershell
git clone https://github.com/akhilmanikanth/jw-price-bot.git
cd jw-price-bot

powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
notepad .env                                   # add your token and chat id

.venv\Scripts\python.exe main.py test-telegram
.venv\Scripts\python.exe main.py check --dry-run
```

Then register it with Task Scheduler:

```powershell
# Option A - fire once a week, no process resident. /check will NOT work.
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Mode Weekly

# Option B - run continuously at startup: APScheduler + Telegram /check.
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Mode Service
```

Handy: `scripts\run_check.bat` and `scripts\run_bot.bat`.

> Running both GitHub Actions **and** a Windows instance? Point them at separate
> `HISTORY_PATH` files, or set `ENABLED_RETAILERS` differently - otherwise you'll
> get two messages, since they don't share the duplicate-guard state.

### Docker (Linux or Windows with WSL2)

```bash
cp .env.example .env    # fill it in
docker compose up -d --build
docker compose logs -f
```

---

## 4. Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env

python main.py check --dry-run
python -m pytest -q
```

---

## Configuration

Everything comes from environment variables (or a `.env` file). See
[`.env.example`](.env.example) for the full annotated list.

| Variable | Default | Notes |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | - | **required** |
| `TELEGRAM_CHAT_ID` | - | **required**, the only chat that receives messages |
| `TELEGRAM_ALLOWED_CHAT_IDS` | - | extra chat ids allowed to use `/check` |
| `TIMEZONE` | `Australia/Sydney` | any IANA zone |
| `SCHEDULE_DAY_OF_WEEK` | `fri` | `mon` to `sun` |
| `SCHEDULE_HOUR` / `SCHEDULE_MINUTE` | `15` / `0` | 24-hour clock, local time |
| `ENABLED_RETAILERS` | *(all)* | retailer (`bws`), product (`jw-black-700`) or exact listing (`bws:jw-black-700`) tokens, comma separated |
| `USE_PLAYWRIGHT` | `true` | set `false` to skip the browser layer |
| `HTTP_TIMEOUT` | `25` | seconds |
| `PAGE_TIMEOUT_MS` | `45000` | Playwright navigation timeout |
| `MAX_RETRIES` / `RETRY_BACKOFF` | `3` / `2` | HTTP retry policy |
| `HISTORY_PATH` | `data/history.json` | price history |
| `LOG_DIR` / `LOG_LEVEL` | `logs` / `INFO` | `jwbot.log` + `errors.log`, rotated |
| `DEBUG_DUMP_DIR` | - | set it to save the raw HTML of every fetch |

---

## Adding another bottle

Add one `ProductSpec` to `PRODUCTS` in `src/jwbot/products.py`:

```python
ProductSpec(
    key="jw-red-700",
    label="Johnnie Walker Red Label 700mL",
    short_label="Red Label 700mL",
    search_term="johnnie walker red label 700ml",
    name_tokens=("johnnie", "walker", "red"),
    size_tokens=("700",),
    brief=True,  # compact watch-list line instead of a full block
)
```

That's it - both retailers pick it up automatically. Leave the retailer URL /
stockcode out: the search strategies resolve the product by name on the next
run and log a `RESOLVED ... (bake into products.py)` line; paste the resolved
reference into a `RetailerRef` afterwards so later runs take the fast path.

## Adding another liquor store

Create `src/jwbot/scrapers/danmurphys.py`:

```python
from .base import BaseScraper, register

@register
class DanMurphysScraper(BaseScraper):
    key = "danmurphys"
    display_name = "Dan Murphy's"
    product_url = "https://www.danmurphys.com.au/product/DM_XXXXX/..."

    # Optional - the generic extractor already tries JSON-LD, meta tags and
    # embedded JSON. Add selectors only if those miss.
    price_selectors = ('[data-testid="product-price"]', ".price__value")
    prefer_json_keys = ("singleprice", "price")
    wait_selector = '[data-testid="product-price"]'
```

Then add `from . import danmurphys` to `src/jwbot/scrapers/__init__.py`. That's it -
it's picked up by the scheduler, `/check`, the history comparison and the message.

Need a custom path (a JSON API, a login, a store picker)? Override `strategies()`
and add your own `strategy_*` methods; raise `ScrapeFailure` when a strategy finds
nothing and the next one takes over.

---

## Troubleshooting

**No price, `no price found via browser`.** The site changed its markup. Set
`DEBUG_DUMP_DIR=debug`, re-run, and open the saved HTML - then add the right CSS
selector to `price_selectors`. On GitHub Actions the dump is already uploaded as a
run artifact.

**Blocked / CAPTCHA on the runner.** Retailer bot protection sometimes blocks
datacentre IPs. Running on your Windows server (a residential IP) usually works
where a cloud runner doesn't - that's the main argument for the hybrid setup.

**`Chromium not found`.** Run `python -m playwright install chromium`.

**No Telegram message.** Run `python main.py test-telegram`. The most common
cause is never having sent your bot a message first.

**Wrong time in the message.** Check `TIMEZONE`, and on Windows make sure
`tzdata` is installed (it's in `requirements.txt`).

---

## Layout

```
main.py                      CLI entry point
src/jwbot/
  products.py                the bottle catalog (add products here)
  config.py                  env -> Config
  logging_setup.py           console + rotating file logs
  models.py                  PriceResult, RunReport
  extract.py                 generic price/availability extraction
  http.py                    requests session with retries
  browser.py                 Playwright rendering (optional)
  history.py                 JSON price history + week-on-week diff
  lock.py                    cross-platform file lock
  formatting.py              the Telegram message
  notifier.py                Telegram Bot API client
  runner.py                  orchestration + duplicate guard
  bot.py                     polling bot + APScheduler
  scrapers/
    base.py                  BaseScraper + registry
    liquorland.py
    bws.py
tests/                       offline unit tests
scripts/                     Windows setup + Task Scheduler
.github/workflows/           weekly cron + tests
```

## Notes

Prices are scraped from publicly visible product pages for personal use, with a
handful of requests per site per week. Both retailers show region-dependent pricing; the
figures here are the default national online prices, which may differ from your
local store.
