"""A tiny cross-platform file lock so two runs never overlap."""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)


class LockBusy(RuntimeError):
    pass


@contextmanager
def file_lock(path: Path, stale_after_s: int = 900, wait_s: float = 0.0) -> Iterator[None]:
    """Exclusive lock via atomic O_EXCL create. Works on Windows and Linux."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s

    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time():.0f}\n".encode())
            os.close(fd)
            break
        except FileExistsError:
            age = _lock_age(path)
            if age is not None and age > stale_after_s:
                log.warning("Removing stale lock %s (age %.0fs)", path, age)
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise LockBusy(f"Another run holds {path} (age {age}s)")
            time.sleep(1.0)

    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _lock_age(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None
