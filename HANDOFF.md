# Handoff brief

Context for anyone (human or assistant) picking this project up.

## State: LIVE

- The weekly GitHub Actions run is deployed and verified end-to-end
  (workflows in `.github/workflows/`, `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
  repository secrets set, first live run scraped both retailers and delivered
  the Telegram message).
- Multi-product support is in: the bot tracks the bottle catalog in
  `src/jwbot/products.py` (Black Label 700mL & 1L as full blocks; Blue Label
  and three Ballantine's expressions as compact watch-list lines).
- Schedule: Friday 3:00 PM Australia/Sydney via two UTC cron slots +
  `--only-if-due` (DST-proof), duplicate-notification guard as backstop.

## How product references resolve

Products in the catalog may omit their Liquorland URL / BWS stockcode. The
scrapers then use their search strategies (BWS search API, Liquorland search
page via Playwright), match candidates against the spec's
`name_tokens` / `size_tokens` / `exclude_tokens`, and log:

    RESOLVED <key> url=... / stockcode=... (bake into products.py)

After a first successful run, copy those references into the `RetailerRef`
entries so later runs take the fast deterministic path. Nothing breaks if you
don't - the search path just keeps doing the work each week.

## Operational notes

- Run logs + raw HTML dumps are uploaded as a workflow artifact on every run.
- Price history is committed back to `data/history.json`; week-on-week diffs
  come from there. Pre-multi-product rows used bare retailer keys
  ("liquorland", "bws"); `apply_legacy_aliases` maps them onto
  `*:jw-black-700` so the original bottle's baseline survived the upgrade.
- Retailer bot protection may block datacentre IPs (shows as HTTP 403).
  Fallback: run on a residential-IP Windows machine via
  `scripts/install_task.ps1 -Mode Service` (local copy lives at C:\JWPC_bot;
  `.env` goes there, never in git).
- Telegram only delivers after the user has messaged the bot once.
- The offline test suite (`python -m pytest -q`) needs no network and runs in
  CI on every push and PR.
