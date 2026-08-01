"""Data models shared across the project."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass
class PriceResult:
    """The outcome of asking one retailer for one product's price."""

    retailer: str  # machine key, e.g. "liquorland"
    display_name: str  # human label, e.g. "Liquorland"
    product_name: str | None = None
    price: float | None = None
    currency: str = "AUD"
    url: str | None = None
    available: bool = False
    error: str | None = None
    note: str | None = None  # e.g. "member price", "on special"
    strategy: str | None = None  # which extraction path succeeded
    scraped_at: str | None = None
    duration_s: float | None = None

    @property
    def ok(self) -> bool:
        return self.price is not None and self.available and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PriceResult":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})

    @classmethod
    def failure(
        cls,
        retailer: str,
        display_name: str,
        error: str,
        url: str | None = None,
    ) -> "PriceResult":
        return cls(
            retailer=retailer,
            display_name=display_name,
            url=url,
            available=False,
            error=error,
            scraped_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )


@dataclass
class RunReport:
    """Everything one weekly check produced."""

    run_key: str  # e.g. "2026-08-07" - the scheduled local date
    started_at: str
    finished_at: str | None = None
    timezone: str = "Australia/Sydney"
    results: list[PriceResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notified: bool = False
    manual: bool = False

    @property
    def successful(self) -> list[PriceResult]:
        return [r for r in self.results if r.ok]

    @property
    def cheapest(self) -> PriceResult | None:
        good = self.successful
        if not good:
            return None
        return min(good, key=lambda r: r.price)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_key": self.run_key,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "timezone": self.timezone,
            "notified": self.notified,
            "manual": self.manual,
            "errors": list(self.errors),
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunReport":
        return cls(
            run_key=data.get("run_key", ""),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at"),
            timezone=data.get("timezone", "Australia/Sydney"),
            results=[PriceResult.from_dict(r) for r in data.get("results", [])],
            errors=list(data.get("errors", [])),
            notified=bool(data.get("notified", False)),
            manual=bool(data.get("manual", False)),
        )
