#!/usr/bin/env python3
"""CLI entry point for the Johnnie Walker Black Label price bot.

Commands
--------
  python main.py check         Run one price check and send it to Telegram.
  python main.py check --dry-run   Scrape and print, don't send.
  python main.py bot           Long-running: /check command + weekly scheduler.
  python main.py test-telegram Verify the token / chat id.
  python main.py history       Print recorded price history.
  python main.py retailers     List registered retailers.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from jwbot.config import load_config  # noqa: E402
from jwbot.logging_setup import setup_logging  # noqa: E402


def cmd_check(args: argparse.Namespace) -> int:
    from jwbot.runner import is_due, run_check

    config = load_config()
    log = setup_logging(config.log_level, config.log_dir)

    if args.only_if_due and not is_due(config, grace_minutes=args.grace):
        now = datetime.now(config.tz)
        log.info(
            "Not due right now (%s, scheduled %s %s) - exiting cleanly",
            now.strftime("%A %H:%M %Z"),
            config.schedule_label,
            config.timezone_name,
        )
        return 0

    outcome = run_check(
        config,
        manual=args.manual,
        force=args.force,
        dry_run=args.dry_run,
        send=not args.no_send,
    )

    if outcome.skipped_reason:
        log.info("Run skipped: %s", outcome.skipped_reason)
        return 0

    print("\n" + "=" * 62)
    print(outcome.message)
    print("=" * 62 + "\n")

    if args.json:
        print(json.dumps(outcome.report.to_dict(), indent=2))

    if not outcome.report.successful:
        log.error("No retailer returned a usable price")
        return 0 if args.tolerate_failure else 2
    return 0


def cmd_bot(args: argparse.Namespace) -> int:
    import os
    from dataclasses import replace

    from jwbot.bot import run_bot
    from jwbot.config import PROJECT_ROOT

    config = load_config()
    # A machine running bot mode keeps its own history file unless told
    # otherwise: data/history.json is written (and committed) by the weekly
    # cloud run, and sharing it would make `git pull` conflict.
    if not os.getenv("HISTORY_PATH"):
        config = replace(config, history_path=PROJECT_ROOT / "data" / "history-local.json")
    setup_logging(config.log_level, config.log_dir)
    run_bot(config)
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    from jwbot.userdata import bot_version, git_short_sha

    sha = git_short_sha()
    print(f"jwbot v{bot_version()}" + (f" ({sha})" if sha else ""))
    return 0


def cmd_test_telegram(args: argparse.Namespace) -> int:
    from jwbot.notifier import TelegramNotifier

    config = load_config()
    log = setup_logging(config.log_level, config.log_dir)
    token, chat_id = config.require_telegram()
    notifier = TelegramNotifier(token, chat_id, timeout=config.http_timeout)
    me = notifier.get_me()
    log.info("Authenticated as @%s (%s)", me.get("username"), me.get("id"))
    notifier.send_message(
        "✅ <b>Johnnie Walker price bot</b> is connected.\n"
        f"Price checks: {config.schedule_label} {config.timezone_name}"
    )
    print(f"Test message sent to chat {chat_id}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from jwbot.history import History
    from jwbot.scrapers import get_scrapers

    config = load_config()
    setup_logging(config.log_level, config.log_dir)
    history = History(config.history_path)

    if args.json:
        print(json.dumps([r.to_dict() for r in history.runs], indent=2))
        return 0

    for scraper in get_scrapers(config):
        series = history.price_series(scraper.key, limit=args.limit)
        print(f"\n{scraper.display_name}")
        if not series:
            print("  (no data)")
        for run_key, price in series:
            print(f"  {run_key}  ${price:,.2f}")
    print()
    return 0


def cmd_retailers(args: argparse.Namespace) -> int:
    from jwbot.scrapers import registry

    for key, cls in registry().items():
        print(f"{key:<14} {cls.display_name:<14} {cls.product_url}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jwbot", description="Weekly Johnnie Walker Black Label 700mL price checker"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="run one price check")
    p_check.add_argument("--dry-run", action="store_true", help="scrape and print, don't send to Telegram")
    p_check.add_argument("--no-send", action="store_true", help="alias for --dry-run without the label")
    p_check.add_argument("--force", action="store_true", help="ignore the duplicate-notification guard")
    p_check.add_argument("--manual", action="store_true", help="record as a manual (off-schedule) check")
    p_check.add_argument("--json", action="store_true", help="also print the raw report as JSON")
    p_check.add_argument(
        "--only-if-due",
        action="store_true",
        help="exit 0 without doing anything unless the local time matches the schedule",
    )
    p_check.add_argument(
        "--grace",
        type=int,
        default=150,
        help="how many minutes after the scheduled time --only-if-due still counts as due",
    )
    p_check.add_argument(
        "--tolerate-failure",
        action="store_true",
        help="exit 0 even when no retailer returned a price (useful in CI)",
    )
    p_check.set_defaults(func=cmd_check)

    p_bot = sub.add_parser("bot", help="run the Telegram bot + weekly scheduler")
    p_bot.set_defaults(func=cmd_bot)

    p_tg = sub.add_parser("test-telegram", help="send a connectivity test message")
    p_tg.set_defaults(func=cmd_test_telegram)

    p_hist = sub.add_parser("history", help="print recorded price history")
    p_hist.add_argument("--limit", type=int, default=20)
    p_hist.add_argument("--json", action="store_true")
    p_hist.set_defaults(func=cmd_history)

    p_ret = sub.add_parser("retailers", help="list registered retailers")
    p_ret.set_defaults(func=cmd_retailers)

    p_ver = sub.add_parser("version", help="print the bot version")
    p_ver.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
