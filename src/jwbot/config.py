"""Configuration loaded from environment variables (with .env support)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

try:  # optional convenience for local dev
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - python-dotenv is optional
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # --- Telegram ---
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    # Extra chat ids allowed to talk to the bot in polling mode (comma separated).
    allowed_chat_ids: tuple[str, ...] = ()

    # --- Schedule ---
    timezone_name: str = "Australia/Sydney"
    schedule_day_of_week: str = "fri"
    schedule_hour: int = 15
    schedule_minute: int = 0

    # --- Scraping ---
    enabled_retailers: tuple[str, ...] = ()  # empty => all registered
    use_playwright: bool = True
    headless: bool = True
    http_timeout: float = 25.0
    page_timeout_ms: int = 45000
    max_retries: int = 3
    retry_backoff: float = 2.0
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    # --- Local bot mode ---
    # The weekly Friday message is sent by the GitHub Actions run; a machine
    # running `main.py bot` only answers commands unless this is switched on.
    run_weekly_job: bool = False

    # --- Storage / logging ---
    history_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "history.json")
    targets_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "targets.json")
    state_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "state.json")
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")
    log_level: str = "INFO"
    debug_dump_dir: Path | None = None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def notify_chat_ids(self) -> tuple[str, ...]:
        """Every chat id allowed to interact with / receive from the bot."""
        ids = []
        if self.telegram_chat_id:
            ids.append(str(self.telegram_chat_id))
        for extra in self.allowed_chat_ids:
            if extra not in ids:
                ids.append(extra)
        return tuple(ids)

    def require_telegram(self) -> tuple[str, str]:
        if not self.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        if not self.telegram_chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID is not set")
        return self.telegram_bot_token, str(self.telegram_chat_id)


def load_config() -> Config:
    """Build a Config from the environment."""
    retailers_raw = _env("ENABLED_RETAILERS", "")
    retailers = tuple(
        part.strip().lower() for part in (retailers_raw or "").split(",") if part.strip()
    )

    allowed_raw = _env("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed = tuple(part.strip() for part in (allowed_raw or "").split(",") if part.strip())

    dump_dir = _env("DEBUG_DUMP_DIR")

    return Config(
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        allowed_chat_ids=allowed,
        timezone_name=_env("TIMEZONE", "Australia/Sydney"),
        schedule_day_of_week=_env("SCHEDULE_DAY_OF_WEEK", "fri"),
        schedule_hour=_env_int("SCHEDULE_HOUR", 15),
        schedule_minute=_env_int("SCHEDULE_MINUTE", 0),
        enabled_retailers=retailers,
        use_playwright=_env_bool("USE_PLAYWRIGHT", True),
        headless=_env_bool("PLAYWRIGHT_HEADLESS", True),
        http_timeout=_env_float("HTTP_TIMEOUT", 25.0),
        page_timeout_ms=_env_int("PAGE_TIMEOUT_MS", 45000),
        max_retries=_env_int("MAX_RETRIES", 3),
        retry_backoff=_env_float("RETRY_BACKOFF", 2.0),
        user_agent=_env("USER_AGENT") or Config.user_agent,
        run_weekly_job=_env_bool("RUN_WEEKLY_JOB", False),
        history_path=Path(_env("HISTORY_PATH") or (PROJECT_ROOT / "data" / "history.json")),
        targets_path=Path(_env("TARGETS_PATH") or (PROJECT_ROOT / "data" / "targets.json")),
        state_path=Path(_env("STATE_PATH") or (PROJECT_ROOT / "data" / "state.json")),
        log_dir=Path(_env("LOG_DIR") or (PROJECT_ROOT / "logs")),
        log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
        debug_dump_dir=Path(dump_dir) if dump_dir else None,
    )
