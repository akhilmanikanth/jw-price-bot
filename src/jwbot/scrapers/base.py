"""Scraper base class + registry.

Adding a new retailer is intentionally small: subclass `BaseScraper`, set the
class attributes, implement one or more `strategy_*` methods, and decorate the
class with `@register`. Nothing else in the project needs to change.
"""

from __future__ import annotations

import logging
import re as _re
import time
from abc import ABC
from datetime import datetime
from typing import Callable, Iterable, Sequence

import requests

from ..config import Config
from ..extract import extract_price_from_html, looks_out_of_stock
from ..http import build_session, get_text
from ..models import PriceResult
from ..products import ProductSpec

log = logging.getLogger(__name__)

_REGISTRY: dict[str, type["BaseScraper"]] = {}


def register(cls: type["BaseScraper"]) -> type["BaseScraper"]:
    key = cls.key.lower()
    if key in _REGISTRY:
        raise ValueError(f"Duplicate scraper key: {key}")
    _REGISTRY[key] = cls
    return cls


def registry() -> dict[str, type["BaseScraper"]]:
    return dict(_REGISTRY)


def unregister(key: str) -> None:
    """Remove a scraper registered at runtime (e.g. via /removebottle)."""
    _REGISTRY.pop(key.lower(), None)


def _token_matches_key(token: str, key: str) -> bool:
    """ENABLED_RETAILERS accepts a full key ("bws:jw-black-700"), a retailer
    ("bws" = every BWS product) or a product key ("jw-black-700" = every
    retailer for that product)."""
    retailer, _, product = key.partition(":")
    return token == key or token == retailer or (bool(product) and token == product)


def get_scrapers(config: Config) -> list["BaseScraper"]:
    """Instantiate the enabled scrapers, in registration order."""
    wanted = config.enabled_retailers
    scrapers: list[BaseScraper] = []
    for key, cls in _REGISTRY.items():
        if wanted and not any(_token_matches_key(t, key) for t in wanted):
            continue
        scrapers.append(cls(config))
    if wanted:
        unknown = [
            t for t in wanted if not any(_token_matches_key(t, key) for key in _REGISTRY)
        ]
        if unknown:
            log.warning("Unknown token(s) in ENABLED_RETAILERS: %s", ", ".join(sorted(unknown)))
    return scrapers


class ScrapeFailure(Exception):
    """Raised by a strategy when it cannot produce a price."""


class BaseScraper(ABC):
    # --- required per retailer ---
    key: str = ""  # unique registry key, e.g. "bws:jw-black-700"
    display_name: str = ""
    product_url: str = ""  # may be empty: search-based strategies resolve it

    # --- product being tracked (None for single-product custom scrapers) ---
    product: ProductSpec | None = None

    # --- optional per retailer ---
    price_selectors: Sequence[str] = ()
    prefer_json_keys: Sequence[str] = ()
    wait_selector: str | None = None
    needs_javascript: bool = True
    expected_name_tokens: Sequence[str] = ()

    def __init__(self, config: Config) -> None:
        self.config = config
        self.log = logging.getLogger(f"jwbot.scraper.{self.key}")
        self._session: requests.Session | None = None

    # ------------------------------------------------------------------ #
    # Session
    # ------------------------------------------------------------------ #
    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = build_session(
                self.config.user_agent,
                total_retries=self.config.max_retries,
                backoff=self.config.retry_backoff,
            )
            self.prime_session(self._session)
        return self._session

    def prime_session(self, session: requests.Session) -> None:
        """Hook: set cookies / hit a homepage before the real request."""

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    # ------------------------------------------------------------------ #
    # Strategies
    # ------------------------------------------------------------------ #
    def strategies(self) -> list[tuple[str, Callable[[], PriceResult]]]:
        """Ordered list of (name, callable). Cheapest / most reliable first."""
        out: list[tuple[str, Callable[[], PriceResult]]] = []
        if hasattr(self, "strategy_api"):
            out.append(("api", getattr(self, "strategy_api")))
        if self.product_url:  # URL-based strategies need a URL to fetch
            out.append(("static-html", self.strategy_static_html))
            if self.config.use_playwright:
                out.append(("browser", self.strategy_browser))
        return out

    def strategy_static_html(self) -> PriceResult:
        html = get_text(self.session, self.product_url, timeout=self.config.http_timeout)
        self._dump(html, "static")
        return self._result_from_html(html, "static-html")

    def strategy_browser(self) -> PriceResult:
        from ..browser import rendered_page  # local import keeps Playwright optional

        with rendered_page(
            self.product_url,
            user_agent=self.config.user_agent,
            headless=self.config.headless,
            timeout_ms=self.config.page_timeout_ms,
            wait_selector=self.wait_selector,
        ) as html:
            self._dump(html, "browser")
            return self._result_from_html(html, "browser")

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #
    def _result_from_html(self, html: str, strategy_label: str) -> PriceResult:
        if not html or len(html) < 200:
            raise ScrapeFailure("empty response body")

        data = extract_price_from_html(
            html,
            selectors=self.price_selectors,
            prefer_json_keys=self.prefer_json_keys,
        )
        price = data.get("price")
        available = data.get("available")

        if price is None:
            if looks_out_of_stock(html[:200000]):
                return self.make_result(
                    price=None,
                    available=False,
                    product_name=data.get("product_name"),
                    strategy=f"{strategy_label}:out-of-stock",
                )
            raise ScrapeFailure(f"no price found via {strategy_label}")

        return self.make_result(
            price=price,
            available=True if available is None else bool(available),
            product_name=data.get("product_name"),
            strategy=data.get("strategy") or strategy_label,
        )

    def make_result(
        self,
        price: float | None,
        available: bool,
        product_name: str | None = None,
        strategy: str | None = None,
        note: str | None = None,
        on_special: bool | None = None,
    ) -> PriceResult:
        return PriceResult(
            retailer=self.key,
            display_name=self.display_name,
            product_key=self.product.key if self.product else None,
            product_label=self.product.label if self.product else None,
            product_name=(product_name or "").strip() or None,
            price=round(price, 2) if price is not None else None,
            url=self.product_url,
            available=available,
            on_special=on_special,
            note=note,
            strategy=strategy,
            scraped_at=datetime.now(self.config.tz).isoformat(timespec="seconds"),
        )

    def _dump(self, html: str, tag: str, ext: str = "html") -> None:
        directory = self.config.debug_dump_dir
        if not directory:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            # Keys contain ":" which is illegal on NTFS and rejected by
            # actions/upload-artifact - keep dump filenames filesystem-safe.
            safe_key = _re.sub(r"[^A-Za-z0-9._-]+", "-", self.key)
            path = directory / f"{safe_key}-{tag}-{stamp}.{ext}"
            path.write_text(html, encoding="utf-8", errors="replace")
            self.log.debug("Dumped %s to %s", ext, path)
        except OSError as exc:
            self.log.debug("Could not dump %s: %s", ext, exc)

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def fetch(self) -> PriceResult:
        """Run every strategy until one yields a result. Never raises."""
        started = time.monotonic()
        errors: list[str] = []

        for name, func in self.strategies():
            self.log.info("Trying strategy '%s'", name)
            try:
                result = func()
            except ScrapeFailure as exc:
                self.log.warning("Strategy '%s' found nothing: %s", name, exc)
                errors.append(f"{name}: {exc}")
                continue
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "?"
                self.log.warning("Strategy '%s' HTTP %s", name, status)
                errors.append(f"{name}: HTTP {status}")
                continue
            except requests.RequestException as exc:
                self.log.warning("Strategy '%s' network error: %s", name, exc)
                errors.append(f"{name}: network error ({type(exc).__name__})")
                continue
            except Exception as exc:  # noqa: BLE001 - a scraper must never crash the run
                self.log.exception("Strategy '%s' raised", name)
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue

            if result is None:
                errors.append(f"{name}: returned nothing")
                continue

            result.duration_s = round(time.monotonic() - started, 2)
            if result.price is not None:
                self.log.info(
                    "OK via %s: $%.2f (%s)", name, result.price, result.strategy
                )
            else:
                self.log.info("Product reported unavailable via %s", name)
            return result

        message = "; ".join(errors) or "all strategies failed"
        self.log.error("Could not get a price: %s", message)
        failure = PriceResult.failure(
            self.key,
            self.display_name,
            message,
            self.product_url or None,
            product_key=self.product.key if self.product else None,
            product_label=self.product.label if self.product else None,
        )
        failure.duration_s = round(time.monotonic() - started, 2)
        return failure


def summarise_errors(results: Iterable[PriceResult]) -> list[str]:
    return [f"{r.display_name}: {r.error}" for r in results if r.error]
