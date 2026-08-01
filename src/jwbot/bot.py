"""Long-running mode: Telegram command handling + APScheduler weekly job.

Use this on the Windows server / Docker. GitHub Actions uses `main.py check`
instead and does not need this module.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from . import products as catalog
from .config import Config
from .formatting import build_history_message, esc, money
from .history import History
from .runner import run_check
from .scrapers import get_scrapers, register_product, unregister_product
from .userdata import (
    PROJECT_ROOT,
    bot_version,
    git_short_sha,
    git_sync,
    load_targets,
    save_targets,
)

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
    scrapers = get_scrapers(config)
    retailers = ", ".join(dict.fromkeys(s.display_name for s in scrapers))
    bottles = ", ".join(
        dict.fromkeys(s.product.short_label for s in scrapers if s.product)
    )
    text = (
        "\U0001F943 <b>Whisky price bot</b>\n\n"
        f"Retailers: {retailers or 'none configured'}\n"
        f"Bottles: {bottles or 'none configured'}\n"
        f"Schedule: every {config.schedule_day_of_week.title()} at "
        f"{config.schedule_hour:02d}:{config.schedule_minute:02d} ({config.timezone_name})\n\n"
        "<b>Commands</b>\n"
        "/check - run a price check right now\n"
        "/last - show the most recent recorded result\n"
        "/history - recent price history per retailer\n"
        "/bottles - list every bottle being tracked\n"
        "/addbottle Chivas Regal 12 700ml - track a new bottle\n"
        "/removebottle chivas - stop tracking a bottle you added\n"
        "/target black 1l 80 - alert when a price hits your target\n"
        "/status - scheduler and configuration info\n"
        "/version - code version running here\n"
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


def _history_with_fallback(config: Config) -> History:
    """This machine's history; falls back to the repo's committed history
    (written by the weekly cloud run) when nothing local exists yet."""
    history = History(config.history_path)
    if not history.runs:
        cloud_path = PROJECT_ROOT / "data" / "history.json"
        if cloud_path != Path(config.history_path) and cloud_path.exists():
            return History(cloud_path)
    return history


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)

    history = _history_with_fallback(config)
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

    history = _history_with_fallback(config)
    listings = [
        (
            s.key,
            f"{s.display_name} - {s.product.short_label}" if s.product else s.display_name,
        )
        for s in get_scrapers(config)
    ]
    await update.message.reply_text(
        build_history_message(history, listings), parse_mode=ParseMode.HTML
    )


# --------------------------------------------------------------------------- #
# Catalog + target commands
# --------------------------------------------------------------------------- #
async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)
    sha = git_short_sha()
    text = f"\U0001F4E6 Running <b>v{esc(bot_version())}</b>"
    if sha:
        text += f" (<code>{esc(sha)}</code>)"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_bottles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)
    custom_keys = {s.key for s in catalog.custom_specs()}
    lines = ["\U0001F943 <b>Tracked bottles</b>", ""]
    for spec in catalog.PRODUCTS_BY_KEY.values():
        kind = " <i>(yours - /removebottle)</i>" if spec.key in custom_keys else ""
        style = "watch list" if spec.brief else "full alerts"
        lines.append(f"• {esc(spec.label)} - {style}{kind}")
    lines += ["", "Add one any time: /addbottle Chivas Regal 12 700ml"]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


def _sync_note(pushed: bool, detail: str, what: str) -> str:
    if pushed:
        return f"☁️ Synced to GitHub - Friday's cloud check includes {what}."
    return (
        f"⚠️ Cloud sync didn't go through ({detail}). {what.capitalize()} works on this "
        "server; it will reach the cloud with the next code push."
    )


async def cmd_addbottle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)

    text = " ".join(context.args or ())
    try:
        spec = catalog.spec_from_text(text)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    if spec.key in catalog.PRODUCTS_BY_KEY:
        await update.message.reply_text(
            f"Already tracking {catalog.PRODUCTS_BY_KEY[spec.key].label}. /bottles to see the list."
        )
        return

    catalog.save_custom_specs(catalog.custom_specs() + (spec,))
    catalog.add_spec_runtime(spec)
    register_product(spec)
    log.info("Added custom bottle %s via /addbottle", spec.key)

    pushed, detail = await asyncio.to_thread(
        git_sync,
        [catalog.CUSTOM_PRODUCTS_PATH],
        f"feat: track {spec.label} (added via /addbottle)",
    )
    await update.message.reply_text(
        f"✅ Added <b>{esc(spec.label)}</b>\n"
        "I'll find it at Liquorland and BWS by name on the next check - /check to try now.\n"
        + _sync_note(pushed, detail, "the new bottle"),
        parse_mode=ParseMode.HTML,
    )


async def cmd_removebottle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)

    text = " ".join(context.args or ())
    customs = catalog.custom_specs()
    if not customs:
        await update.message.reply_text("No custom bottles to remove - only ones you /addbottle can be removed.")
        return
    match = None
    for spec in customs:
        if spec.key == text.strip().lower():
            match = spec
            break
    if match is None:
        resolved, candidates = catalog.resolve_product(text)
        custom_candidates = [c for c in candidates if c.key in {s.key for s in customs}]
        if resolved is not None and resolved.key in {s.key for s in customs}:
            match = resolved
        elif len(custom_candidates) == 1:
            match = custom_candidates[0]
    if match is None:
        names = "\n".join(f"• {esc(s.label)} (<code>{s.key}</code>)" for s in customs)
        await update.message.reply_text(
            f"Which one? Your custom bottles:\n{names}", parse_mode=ParseMode.HTML
        )
        return

    catalog.save_custom_specs(tuple(s for s in customs if s.key != match.key))
    catalog.remove_spec_runtime(match.key)
    unregister_product(match.key)

    targets = load_targets(config.targets_path)
    if match.key in targets:
        targets.pop(match.key)
        save_targets(config.targets_path, targets)

    pushed, detail = await asyncio.to_thread(
        git_sync,
        [catalog.CUSTOM_PRODUCTS_PATH, Path(config.targets_path)],
        f"feat: stop tracking {match.label} (via /removebottle)",
    )
    await update.message.reply_text(
        f"\U0001F5D1 Removed <b>{esc(match.label)}</b>.\n" + _sync_note(pushed, detail, "the change"),
        parse_mode=ParseMode.HTML,
    )


def _best_known_price(config: Config, product_key: str) -> tuple[float, str] | None:
    history = _history_with_fallback(config)
    prices = history.previous_prices(include_manual=True)
    best: tuple[float, str] | None = None
    for listing, price in prices.items():
        _, _, suffix = listing.partition(":")
        if (suffix or catalog.DEFAULT_LEGACY_PRODUCT) != product_key:
            continue
        if best is None or price < best[0]:
            best = (price, listing.split(":", 1)[0])
    return best


async def cmd_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)

    args = list(context.args or ())
    targets = load_targets(config.targets_path)

    # /target -> list
    if not args:
        if not targets:
            await update.message.reply_text(
                "No targets set. Example: /target black label 1l 80"
            )
            return
        lines = ["\U0001F3AF <b>Price targets</b>", ""]
        for key, value in targets.items():
            spec = catalog.PRODUCTS_BY_KEY.get(key)
            label = spec.short_label if spec else key
            lines.append(f"• {esc(label)} - {money(value)}")
        lines += ["", "Set: /target black 1l 80 · Clear: /target clear black 1l"]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    # /target clear <bottle>
    if args[0].lower() in {"clear", "remove", "off"}:
        spec, candidates = catalog.resolve_product(" ".join(args[1:]))
        if spec is None:
            hint = ", ".join(c.short_label for c in candidates[:4]) or "no match"
            await update.message.reply_text(f"Which bottle? ({hint})")
            return
        if targets.pop(spec.key, None) is None:
            await update.message.reply_text(f"No target was set for {spec.short_label}.")
            return
        save_targets(config.targets_path, targets)
        pushed, detail = await asyncio.to_thread(
            git_sync, [Path(config.targets_path)], f"chore: clear target for {spec.key}"
        )
        await update.message.reply_text(
            f"Cleared the target for <b>{esc(spec.short_label)}</b>.\n"
            + _sync_note(pushed, detail, "the change"),
            parse_mode=ParseMode.HTML,
        )
        return

    # /target <bottle words...> <price>
    try:
        price = float(args[-1].lstrip("$"))
    except ValueError:
        await update.message.reply_text(
            "End with the price, e.g. /target black label 1l 80"
        )
        return
    spec, candidates = catalog.resolve_product(" ".join(args[:-1]))
    if spec is None:
        if len(candidates) > 1:
            hint = "\n".join(f"• {esc(c.short_label)}" for c in candidates[:6])
            await update.message.reply_text(
                f"That matches a few bottles - be more specific:\n{hint}", parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                "I don't know that bottle. /bottles lists them, /addbottle adds one."
            )
        return

    targets[spec.key] = price
    save_targets(config.targets_path, targets)
    known = _best_known_price(config, spec.key)
    retailer_names = {"bws": "BWS", "liquorland": "Liquorland"}
    context_line = (
        f"Best known price: {money(known[0])} at {retailer_names.get(known[1], known[1])}."
        if known
        else "No price on record yet - /check will take a reading."
    )
    if known and known[0] <= price:
        context_line += " That's already at your target! \U0001F389"
    pushed, detail = await asyncio.to_thread(
        git_sync, [Path(config.targets_path)], f"chore: target {spec.key} at ${price:g}"
    )
    await update.message.reply_text(
        f"\U0001F3AF Target set: <b>{esc(spec.short_label)}</b> at <b>{money(price)}</b>.\n"
        f"{esc(context_line)}\n" + _sync_note(pushed, detail, "the target"),
        parse_mode=ParseMode.HTML,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    if not _authorised(config, update):
        return await _deny(update)

    scheduler: AsyncIOScheduler | None = context.application.bot_data.get("scheduler")
    next_run = "GitHub Actions (cloud) sends the Friday summary"
    if scheduler:
        jobs = scheduler.get_jobs()
        if jobs and jobs[0].next_run_time:
            next_run = jobs[0].next_run_time.strftime("%A, %d %b %Y %I:%M %p %Z")

    history = _history_with_fallback(config)
    latest = history.latest()
    text = (
        "⚙️ <b>Status</b>\n\n"
        f"Version: v{esc(bot_version())}\n"
        f"Now: {datetime.now(config.tz).strftime('%A, %d %b %Y %I:%M %p %Z')}\n"
        f"Next scheduled run: {next_run}\n"
        f"Listings: {len(get_scrapers(config))} "
        f"({', '.join(dict.fromkeys(s.display_name for s in get_scrapers(config))) or 'none'})\n"
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
        name="Weekly whisky price check",
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
    application.add_handler(CommandHandler("bottles", cmd_bottles))
    application.add_handler(CommandHandler("addbottle", cmd_addbottle))
    application.add_handler(CommandHandler("removebottle", cmd_removebottle))
    application.add_handler(CommandHandler("target", cmd_target))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("version", cmd_version))

    async def _post_init(app: Application) -> None:
        if config.run_weekly_job:
            scheduler = build_scheduler(config)
            scheduler.start()
            app.bot_data["scheduler"] = scheduler
            jobs = scheduler.get_jobs()
            if jobs and jobs[0].next_run_time:
                log.info("Next scheduled check: %s", jobs[0].next_run_time)
        else:
            log.info(
                "Local weekly job disabled (RUN_WEEKLY_JOB=false) - the GitHub "
                "Actions run sends the Friday summary; this bot answers commands."
            )
        me = await app.bot.get_me()
        log.info("Bot @%s is online and polling (v%s)", me.username, bot_version())

    async def _post_shutdown(app: Application) -> None:
        scheduler = app.bot_data.get("scheduler")
        if scheduler:
            scheduler.shutdown(wait=False)

    application.post_init = _post_init
    application.post_shutdown = _post_shutdown

    if config.run_weekly_job:
        log.info(
            "Starting bot; weekly check every %s at %02d:%02d %s",
            config.schedule_day_of_week,
            config.schedule_hour,
            config.schedule_minute,
            config.timezone_name,
        )
    else:
        log.info("Starting bot in commands-only mode (cloud handles the weekly check)")
    application.run_polling(drop_pending_updates=True)
