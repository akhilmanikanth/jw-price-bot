"""JSON-file price history + duplicate-notification guard."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import PriceResult, RunReport

log = logging.getLogger(__name__)

MAX_RUNS_KEPT = 260  # ~5 years of weekly runs


class History:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {"version": 1, "runs": []}
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        if not self.path.exists():
            log.info("No history file at %s yet - starting fresh", self.path)
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.error("History file %s is unreadable (%s); starting fresh", self.path, exc)
            self._backup_corrupt()
            return
        if isinstance(raw, dict) and isinstance(raw.get("runs"), list):
            self._data = raw
        else:
            log.error("History file %s has an unexpected shape; starting fresh", self.path)
            self._backup_corrupt()

    def _backup_corrupt(self) -> None:
        try:
            self.path.rename(self.path.with_suffix(self.path.suffix + ".corrupt"))
        except OSError:
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        runs = self._data.get("runs", [])
        if len(runs) > MAX_RUNS_KEPT:
            self._data["runs"] = runs[-MAX_RUNS_KEPT:]
        # Atomic write so a crash can't corrupt the file.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, self.path)
            log.info("History saved to %s (%d runs)", self.path, len(self._data["runs"]))
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------ #
    @property
    def runs(self) -> list[RunReport]:
        return [RunReport.from_dict(r) for r in self._data.get("runs", [])]

    def get_run(self, run_key: str) -> RunReport | None:
        for raw in reversed(self._data.get("runs", [])):
            if raw.get("run_key") == run_key:
                return RunReport.from_dict(raw)
        return None

    def already_notified(self, run_key: str) -> bool:
        run = self.get_run(run_key)
        return bool(run and run.notified)

    def latest(self, exclude_run_key: str | None = None) -> RunReport | None:
        for raw in reversed(self._data.get("runs", [])):
            if exclude_run_key and raw.get("run_key") == exclude_run_key:
                continue
            return RunReport.from_dict(raw)
        return None

    def previous_prices(
        self, exclude_run_key: str | None = None, include_manual: bool = False
    ) -> dict[str, float]:
        """Most recent known price per retailer, ignoring the current run.

        Walks backwards so a retailer that failed last week still compares
        against the last time it *did* work. Manual /check runs are excluded by
        default so the weekly message really is a week-on-week comparison.
        """
        prices: dict[str, float] = {}
        for raw in reversed(self._data.get("runs", [])):
            if exclude_run_key and raw.get("run_key") == exclude_run_key:
                continue
            if not include_manual and raw.get("manual"):
                continue
            for result in raw.get("results", []):
                key = result.get("retailer")
                price = result.get("price")
                if key and price is not None and key not in prices:
                    prices[key] = float(price)
        return prices

    def price_series(self, retailer: str, limit: int = 12) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        for raw in self._data.get("runs", []):
            for result in raw.get("results", []):
                if result.get("retailer") == retailer and result.get("price") is not None:
                    out.append((raw.get("run_key", "?"), float(result["price"])))
        return out[-limit:]

    # ------------------------------------------------------------------ #
    def upsert(self, report: RunReport) -> None:
        """Insert or replace the run with this run_key."""
        runs = self._data.setdefault("runs", [])
        payload = report.to_dict()
        for index, raw in enumerate(runs):
            if raw.get("run_key") == report.run_key:
                runs[index] = payload
                break
        else:
            runs.append(payload)

    def mark_notified(self, run_key: str) -> None:
        for raw in self._data.get("runs", []):
            if raw.get("run_key") == run_key:
                raw["notified"] = True


def diff(current: PriceResult, previous: float | None) -> tuple[str, float | None]:
    """Return (direction, delta) where direction is up/down/same/new."""
    if current.price is None:
        return "unknown", None
    if previous is None:
        return "new", None
    delta = round(current.price - previous, 2)
    if abs(delta) < 0.005:
        return "same", 0.0
    return ("up" if delta > 0 else "down"), delta
