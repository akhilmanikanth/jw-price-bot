"""Centralised logging configuration: console + rotating file."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False

FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S%z"


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    """Configure the root logger once. Safe to call repeatedly."""
    global _CONFIGURED
    root = logging.getLogger()

    if _CONFIGURED:
        root.setLevel(level)
        return logging.getLogger("jwbot")

    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(FORMAT, datefmt=DATEFMT)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / "jwbot.log",
                maxBytes=2_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)

            error_handler = RotatingFileHandler(
                log_dir / "errors.log",
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.WARNING)
            error_handler.setFormatter(formatter)
            root.addHandler(error_handler)
        except OSError as exc:  # pragma: no cover - e.g. read-only FS
            root.warning("Could not set up file logging in %s: %s", log_dir, exc)

    # Quieten noisy third parties.
    for noisy in ("httpx", "httpcore", "urllib3", "telegram.ext", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    return logging.getLogger("jwbot")
