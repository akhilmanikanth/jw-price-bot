"""Liquorland (Coles Group) scraper.

Liquorland renders prices client-side, so the browser strategy is the reliable
one. The cheaper HTML strategies run first in case the page is server-rendered
on a given day (it sometimes is, via JSON-LD / __NEXT_DATA__).
"""

from __future__ import annotations

import re
from typing import Callable

from ..extract import extract_price_from_html
from ..http import get_text
from ..models import PriceResult
from .base import BaseScraper, ScrapeFailure, register

PRODUCT_LINK_RE = re.compile(r'href="(/spirits/[^"]*johnnie-walker-black-label[^"]*700ml[^"]*)"', re.I)


@register
class LiquorlandScraper(BaseScraper):
    key = "liquorland"
    display_name = "Liquorland"
    product_url = (
        "https://www.liquorland.com.au/spirits/"
        "johnnie-walker-black-label-12yo-scotch-whisky-700ml_30663"
    )
    search_url = "https://www.liquorland.com.au/search?q=johnnie%20walker%20black%20label%20700ml"

    prefer_json_keys = (
        "currentprice",
        "nowprice",
        "sellprice",
        "onlineprice",
        "shelfprice",
        "price",
    )
    price_selectors = (
        '[data-testid="product-price"]',
        '[data-testid="price"]',
        ".product-price__value",
        ".product-price",
        ".price__value",
        ".price-value",
        'span[class*="Price"]',
        'div[class*="price"] span',
    )
    wait_selector = '[data-testid="product-price"], [class*="price"], h1'
    expected_name_tokens = ("johnnie", "walker", "black")

    def prime_session(self, session) -> None:  # type: ignore[no-untyped-def]
        try:
            session.get("https://www.liquorland.com.au/", timeout=self.config.http_timeout)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Could not prime Liquorland session: %s", exc)

    def strategies(self) -> list[tuple[str, Callable[[], PriceResult]]]:
        out: list[tuple[str, Callable[[], PriceResult]]] = [
            ("static-html", self.strategy_static_html),
        ]
        if self.config.use_playwright:
            out.append(("browser", self.strategy_browser))
            out.append(("browser-search", self.strategy_browser_search))
        return out

    def strategy_browser_search(self) -> PriceResult:
        """Fallback: the product URL changed - find it again from the search page."""
        from ..browser import rendered_page

        with rendered_page(
            self.search_url,
            user_agent=self.config.user_agent,
            headless=self.config.headless,
            timeout_ms=self.config.page_timeout_ms,
            wait_selector='a[href*="johnnie-walker"], [class*="price"]',
        ) as html:
            self._dump(html, "search")
            match = PRODUCT_LINK_RE.search(html)
            if not match:
                raise ScrapeFailure("product not found on Liquorland search page")
            resolved = "https://www.liquorland.com.au" + match.group(1)
            self.log.info("Resolved Liquorland product URL to %s", resolved)
            self.product_url = resolved

        with rendered_page(
            resolved,
            user_agent=self.config.user_agent,
            headless=self.config.headless,
            timeout_ms=self.config.page_timeout_ms,
            wait_selector=self.wait_selector,
        ) as html:
            self._dump(html, "search-product")
            return self._result_from_html(html, "browser-search")
