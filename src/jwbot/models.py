"""Data models shared across the project."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class MultiBuy:
    """A bulk offer, e.g. "2 for $110" - the per-bottle price is what matters."""

    quantity: int
    total_price: float
    description: str | None = None

    @property
    def unit_price(self) -> float:
        return round(self.total_price / self.quantity, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "total_price": self.total_price,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MultiBuy":
        return cls(
            quantity=int(data["quantity"]),
            total_price=float(data["total_price"]),
            description=data.get("description"),
        )


@dataclass
class PriceResult:
    """The outcome of asking one retailer for one product's price."""

    retailer: str  # machine key, e.g. "liquorland:jw-black-700"
    display_name: str  # human label, e.g. "Liquorland"
    product_key: str | None = None  # catalog key, e.g. "jw-black-700"
    product_label: str | None = None  # human label, e.g. "Johnnie Walker Black Label 700mL"
    product_name: str | None = None
    price: float | None = None  # price for ONE bottle
    currency: str = "AUD"
    url: str | None = None
    available: bool = False
    on_special: bool | None = None  # best-effort promo flag (None = unknown)
    multibuy: list[MultiBuy] = field(default_factory=list)
    error: str | None = None
    blocked: bool = False  # True when bot protection stopped us (not a real fault)
    note: str | None = None  # e.g. "member price", "on special"
    strategy: str | None = None  # which extraction path succeeded
    scraped_at: str | None = None
    duration_s: float | None = None

    @property
    def ok(self) -> bool:
        return self.price is not None and self.available and self.error is None

    @property
    def best_multibuy(self) -> MultiBuy | None:
        """The bulk offer with the lowest per-bottle price, if any beats single."""
        deals = [d for d in self.multibuy if d.quantity > 1 and d.total_price > 0]
        if not deals:
            return None
        best = min(deals, key=lambda d: d.unit_price)
        if self.price is not None and best.unit_price >= self.price - 0.005:
            return None  # not actually cheaper per bottle
        return best

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["multibuy"] = [d.to_dict() for d in self.multibuy]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PriceResult":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in allowed}
        deals = []
        for raw in clean.get("multibuy") or ():
            if isinstance(raw, MultiBuy):
                deals.append(raw)
                continue
            try:
                deals.append(MultiBuy.from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
        clean["multibuy"] = deals
        return cls(**clean)

    @classmethod
    def failure(
        cls,
        retailer: str,
        display_name: str,
        error: str,
        url: str | None = None,
        product_key: str | None = None,
        product_label: str | None = None,
        blocked: bool = False,
    ) -> "PriceResult":
        return cls(
            retailer=retailer,
            display_name=display_name,
            product_key=product_key,
            product_label=product_label,
            url=url,
            available=False,
            error=error,
            blocked=blocked,
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
