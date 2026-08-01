"""Long-running mode: Telegram command handling + APScheduler weekly job.

Use this on the Windows server / Docker. GitHub Actions uses `main.py check`
instead and does not need this module.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import Config
from .formatting import build_history_message
from .history import History
from .runner import run_check
from .scrapers import get_scrapers

log = logging.getLogger(__name__)


def _authorised(config: Config, update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    allowed = config.notify_chat_ids
    if not allowed:
        return False
    return str(chat.id) in allowed


async def _deny(update: Update) -> None:
    chat = update.effective_chat
    log.warning("Ignoring command from unauthorised chat %s", chat.id if chat else "?")
    if update.message:
        await update.message.reply_text("Sorry, this bot is private.")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)
    await cmd_help(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)
    retailers = ", ".join(s.display_name for s in get_scrapers(config))
    text = (
        "\U0001F943 <b>Johnnie Walker Black Label 700mL price bot</b>\n\n"
        f"Checking: {retailers or 'none configured'}\n"
        f"Schedule: every {config.schedule_day_of_week.title()} at "
        f"{config.schedule_hour:02d}:{config.schedule_minute:02d} ({config.timezone_name})\n\n"
        "<b>Commands</b>\n"
        "/check - run a price check right now\n"
        "/last - show the most recent recorded result\n"
        "/history - recent price history per retailer\n"
        "/status - scheduler and configuration info\n"
        "/help - this message"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)

    notice = await update.message.reply_text("\U0001F50E Checking prices, one moment...")
    chat_id = str(update.effective_chat.id)

    # Scraping is blocking (requests / Playwright sync API) - keep the loop free.
    outcome = await asyncio.to_thread(
        run_check, config, manual=True, force=True, chat_id_override=chat_id
    )

    try:
        await notice.delete()
    except Exception:  # noqa: BLE001
        pass

    if outcome.skipped_reason and not outcome.message:
        await update.message.reply_text(f"⏭ Skipped: {outcome.skipped_reason}")
        return
    if not outcome.sent:
        # run_check already tried to send; fall back to replying in-thread.
        await update.message.reply_text(
            outcome.message, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)

    history = History(config.history_path)
    latest = history.latest()
    if latest is None:
        await update.message.reply_text("No checks recorded yet. Try /check.")
        return

    from .formatting import build_message

    previous = history.previous_prices(exclude_run_key=latest.run_key)
    text = build_message(
        latest,
        previous_prices=previous,
        checked_at=datetime.fromisoformat(latest.started_at),
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)

    history = History(config.history_path)
    retailers = [(s.key, s.display_name) for s in get_scrapers(config)]
    await update.message.reply_text(
        build_history_message(history, retailers), parse_mode=ParseMode.HTML
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)

    scheduler: AsyncIOScheduler | None = context.application.bot_data.get("scheduler")
    next_run = "n/a"
    if scheduler:
        jobs = scheduler.get_jobs()
        if jobs and jobs[0].next_run_time:
            next_run = jobs[0].next_run_time.strftime("%A, %d %b %Y %I:%M %p %Z")

    history = History(config.history_path)
    latest = history.latest()
    text = (
        "⚙️ <b>Status</b>\n\n"
        f"Now: {datetime.now(config.tz).strftime('%A, %d %b %Y %I:%M %p %Z')}\n"
        f"Next scheduled run: {next_run}\n"
        f"Retailers: {', '.join(s.display_name for s in get_scrapers(config)) or 'none'}\n"
        f"Playwright: {'enabled' if config.use_playwright else 'disabled'}\n"
        f"History file: <code>{config.history_path}</code>\n"
        f"Runs recorded: {len(history.runs)}\n"
        f"Last run: {latest.run_key if latest else 'none'}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)
    await update.message.reply_text("Unknown command. Try /help.")


# --------------------------------------------------------------------------- #
# Scheduled job
# --------------------------------------------------------------------------- #
def _scheduled_job(config: Config) -> None:
    log.info("Scheduled weekly price check firing")
    try:
        outcome = run_check(config, manual=False, force=False)
        if outcome.skipped_reason:
            log.info("Scheduled run skipped: %s", outcome.skipped_reason)
        else:
            log.info("Scheduled run complete; notified=%s", outcome.sent)
    except Exception:  # noqa: BLE001
        log.exception("Scheduled run raised an unexpected error")


async def _run_scheduled(config: Config) -> None:
    await asyncio.to_thread(_scheduled_job, config)


def build_scheduler(config: Config) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.tz)
    trigger = CronTrigger(
        day_of_week=config.schedule_day_of_week,
        hour=config.schedule_hour,
        minute=config.schedule_minute,
        timezone=config.tz,
    )
    scheduler.add_job(
        _run_scheduled,
        trigger=trigger,
        args=[config],
        id="weekly_price_check",
        name="Weekly Johnnie Walker Black Label price check",
        max_instances=1,      # never overlap
        coalesce=True,        # a missed run fires once, not N times
        misfire_grace_time=3600,
        replace_existing=True,
    )
    return scheduler


# --------------------------------------------------------------------------- #
def run_bot(config: Config) -> None:
    """Blocking: start polling + the weekly scheduler."""
    token, _chat = config.require_telegram()

    application = Application.builder().token(token).build()
    application.bot_data["config"] = config

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("check", cmd_check))
    application.add_handler(CommandHandler("last", cmd_last))
    application.add_handler(CommandHandler("history", cmd_history))
    application.add_handler(CommandHandler("status", cmd_status))

    async def _post_init(app: Application) -> None:
        scheduler = build_scheduler(config)
        scheduler.start()
        app.bot_data["scheduler"] = scheduler
        jobs = scheduler.get_jobs()
        if jobs and jobs[0].next_run_time:
            log.info("Next scheduled check: %s", jobs[0].next_run_time)
        me = await app.bot.get_me()
        log.info("Bot @%s is online and polling", me.username)

    async def _post_shutdown(app: Application) -> None:
        scheduler = app.bot_data.get("scheduler")
        if scheduler:
            scheduler.shutdown(wait=False)

    application.post_init = _post_init
    application.post_shutdown = _post_shutdown

    log.info(
        "Starting bot; weekly check every %s at %02d:%02d %s",
        config.schedule_day_of_week,
        config.schedule_hour,
        config.schedule_minute,
        config.timezone_name,
    )
    application.run_polling(drop_pending_updates=True)
