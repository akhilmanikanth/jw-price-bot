"""Liquorland (Coles Group) scrapers - one registered class per catalog product.

Liquorland renders prices client-side, so the browser strategy is the reliable
one. The cheaper HTML strategy runs first in case the page is server-rendered
on a given day (it sometimes is, via JSON-LD / __NEXT_DATA__). Products whose
canonical URL is not yet known skip straight to the search-page strategy,
which resolves the URL by name and logs it so it can be baked into
`products.py` later.
"""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import quote_plus

from ..models import PriceResult
from ..products import PRODUCTS, ProductSpec
from .base import BaseScraper, ScrapeFailure, register

# Any product-ish link on a search results page: Liquorland product paths end
# in an _<sku> suffix (e.g. .../johnnie-walker-...-700ml_30663), relative or
# absolute. Matching against the catalog spec (tokens/size/excludes) happens
# in Python, not in the regex.
SEARCH_LINK_RE = re.compile(
    r'href="(?:https?://(?:www\.)?liquorland\.com\.au)?(/[^"?#]*_\d{3,})"', re.I
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


class LiquorlandScraper(BaseScraper):
    """Base for every Liquorland product scraper (not registered itself)."""

    display_name = "Liquorland"

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

    @property
    def search_url(self) -> str:
        assert self.product is not None
        return "https://www.liquorland.com.au/search?q=" + quote_plus(self.product.search_term)

    def prime_session(self, session) -> None:  # type: ignore[no-untyped-def]
        try:
            session.get("https://www.liquorland.com.au/", timeout=self.config.http_timeout)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Could not prime Liquorland session: %s", exc)

    def strategies(self) -> list[tuple[str, Callable[[], PriceResult]]]:
        out: list[tuple[str, Callable[[], PriceResult]]] = []
        if self.product_url:
            out.append(("static-html", self.strategy_static_html))
            if self.config.use_playwright:
                out.append(("browser", self.strategy_browser))
        if self.config.use_playwright:
            out.append(("browser-search", self.strategy_browser_search))
        return out

    # ------------------------------------------------------------------ #
    def _pick_product_link(self, html: str) -> str | None:
        """First search-result link whose slug matches the catalog spec."""
        assert self.product is not None
        seen: set[str] = set()
        for match in SEARCH_LINK_RE.finditer(html):
            path = match.group(1)
            if path in seen:
                continue
            seen.add(path)
            slug = path.rsplit("/", 1)[-1]
            if self.product.matches_name(slug):
                return path
        return None

    @staticmethod
    def _seen_slugs(html: str) -> list[str]:
        slugs: list[str] = []
        for match in SEARCH_LINK_RE.finditer(html):
            slug = match.group(1).rsplit("/", 1)[-1]
            if slug not in slugs:
                slugs.append(slug)
        return slugs

    def strategy_browser_search(self) -> PriceResult:
        """Resolve the product URL from the search page, then scrape it."""
        from ..browser import rendered_page

        with rendered_page(
            self.search_url,
            user_agent=self.config.user_agent,
            headless=self.config.headless,
            timeout_ms=self.config.page_timeout_ms,
            # Nav links also match 'a[href*="/spirits/"]', so the selector can be
            # satisfied before results render - give the results grid extra time.
            wait_selector='a[href*="/spirits/"], [class*="price"]',
            wait_after_load_ms=4000,
        ) as html:
            self._dump(html, "search")
            path = self._pick_product_link(html)
            if not path:
                slugs = self._seen_slugs(html)
                if slugs:
                    detail = f"{len(slugs)} product links seen: " + "; ".join(slugs[:5])[:220]
                else:
                    title_match = TITLE_RE.search(html)
                    title = (title_match.group(1).strip()[:80] if title_match else "?")
                    detail = (
                        f"0 product links seen; page title={title!r}, "
                        f"{html.count('href=')} hrefs, {len(html)} bytes"
                    )
                raise ScrapeFailure(f"product not found on Liquorland search page ({detail})")
            resolved = "https://www.liquorland.com.au" + path
            self.log.info("RESOLVED %s url=%s (bake into products.py)", self.key, resolved)
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


def _make_class(spec: ProductSpec) -> type[LiquorlandScraper]:
    return type(
        f"Liquorland_{spec.key.replace('-', '_')}",
        (LiquorlandScraper,),
        {
            "key": f"liquorland:{spec.key}",
            "product": spec,
            "product_url": spec.liquorland.url or "",
            "expected_name_tokens": spec.name_tokens,
        },
    )


for _spec in PRODUCTS:
    register(_make_class(_spec))
