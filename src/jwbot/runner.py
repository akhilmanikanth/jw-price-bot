"""Orchestrates one price check: scrape -> compare -> notify -> persist."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .formatting import build_message
from .history import History
from .lock import LockBusy, file_lock
from .models import PriceResult, RunReport
from .notifier import TelegramError, TelegramNotifier
from .scrapers import get_scrapers

log = logging.getLogger(__name__)

DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass
class CheckOutcome:
    report: RunReport
    message: str
    sent: bool
    skipped_reason: str | None = None


def run_key_for(moment: datetime, manual: bool = False) -> str:
    if manual:
        return f"{moment.date().isoformat()}-manual-{moment.strftime('%H%M%S')}"
    return moment.date().isoformat()


def is_due(
    config: Config,
    moment: datetime | None = None,
    grace_minutes: int = 150,
    early_minutes: int = 15,
) -> bool:
    """True when `moment` sits inside the scheduled window.

    The window is deliberately asymmetric: [target - 15min, target + grace].
    GitHub Actions cron can be delayed by tens of minutes under load, so a
    generous *late* tolerance keeps the weekly run reliable, while the tight
    *early* tolerance stops the UTC+10 cron slot firing an hour early during
    AEST. Firing twice inside one window is harmless - the duplicate guard in
    `run_check` catches it.
    """
    now = moment or datetime.now(config.tz)
    target_day = DAY_MAP.get(config.schedule_day_of_week.lower()[:3])
    if target_day is None:
        log.warning("Unrecognised SCHEDULE_DAY_OF_WEEK=%r; treating as due", config.schedule_day_of_week)
        return True
    if now.weekday() != target_day:
        return False
    target = now.replace(
        hour=config.schedule_hour, minute=config.schedule_minute, second=0, microsecond=0
    )
    return (target - timedelta(minutes=early_minutes)) <= now <= (target + timedelta(minutes=grace_minutes))


def scrape_all(config: Config) -> list[PriceResult]:
    scrapers = get_scrapers(config)
    if not scrapers:
        log.error("No scrapers enabled")
        return []

    log.info("Checking %d retailer(s): %s", len(scrapers), ", ".join(s.display_name for s in scrapers))
    results: list[PriceResult] = []
    with ThreadPoolExecutor(max_workers=min(4, len(scrapers))) as pool:
        futures = {pool.submit(s.fetch): s for s in scrapers}
        for future, scraper in futures.items():
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - belt and braces
                log.exception("Scraper %s crashed outright", scraper.key)
                results.append(
                    PriceResult.failure(
                        scraper.key, scraper.display_name, f"{type(exc).__name__}: {exc}", scraper.product_url
                    )
                )
            finally:
                scraper.close()

    order = {s.key: i for i, s in enumerate(scrapers)}
    results.sort(key=lambda r: order.get(r.retailer, 99))
    return results


def run_check(
    config: Config,
    *,
    manual: bool = False,
    force: bool = False,
    dry_run: bool = False,
    send: bool = True,
    chat_id_override: str | None = None,
) -> CheckOutcome:
    """Perform one full check. Never raises for scraping problems."""
    now = datetime.now(config.tz)
    key = run_key_for(now, manual=manual)
    history = History(config.history_path)

    lock_path = Path(config.state_path).with_name("jwbot.lock")
    try:
        with file_lock(lock_path, stale_after_s=1800, wait_s=0 if manual else 30):
            return _run_locked(
                config,
                history,
                now,
                key,
                manual=manual,
                force=force,
                dry_run=dry_run,
                send=send,
                chat_id_override=chat_id_override,
            )
    except LockBusy as exc:
        log.warning("Skipping run: %s", exc)
        report = RunReport(run_key=key, started_at=now.isoformat(timespec="seconds"), timezone=config.timezone_name, manual=manual)
        return CheckOutcome(report=report, message="", sent=False, skipped_reason=str(exc))


def _run_locked(
    config: Config,
    history: History,
    now: datetime,
    key: str,
    *,
    manual: bool,
    force: bool,
    dry_run: bool,
    send: bool,
    chat_id_override: str | None,
) -> CheckOutcome:
    # --- duplicate guard -------------------------------------------------
    if not manual and not force and history.already_notified(key):
        log.warning("Run %s already notified - skipping duplicate", key)
        existing = history.get_run(key) or RunReport(run_key=key, started_at=now.isoformat())
        return CheckOutcome(
            report=existing,
            message="",
            sent=False,
            skipped_reason=f"already notified for {key}",
        )

    report = RunReport(
        run_key=key,
        started_at=now.isoformat(timespec="seconds"),
        timezone=config.timezone_name,
        manual=manual,
    )

    report.results = scrape_all(config)
    report.finished_at = datetime.now(config.tz).isoformat(timespec="seconds")

    if not report.results:
        report.errors.append("No retailers were configured or enabled.")

    previous = history.previous_prices(exclude_run_key=key)
    message = build_message(report, previous_prices=previous, checked_at=now)

    log.info("Message built (%d chars)", len(message))
    for result in report.results:
        log.info(
            "%-12s price=%s available=%s strategy=%s error=%s",
            result.retailer,
            result.price,
            result.available,
            result.strategy,
            result.error,
        )

    sent = False
    if send and not dry_run:
        try:
            token, chat_id = config.require_telegram()
            notifier = TelegramNotifier(token, chat_id_override or chat_id, timeout=config.http_timeout)
            notifier.send_message(message)
            sent = True
            report.notified = True
        except (TelegramError, RuntimeError) as exc:
            log.error("Telegram delivery failed: %s", exc)
            report.errors.append(f"Telegram delivery failed: {exc}")
    elif dry_run:
        log.info("Dry run - not sending to Telegram")

    history.upsert(report)
    try:
        history.save()
    except OSError as exc:
        log.error("Could not save history: %s", exc)

    return CheckOutcome(report=report, message=message, sent=sent)
