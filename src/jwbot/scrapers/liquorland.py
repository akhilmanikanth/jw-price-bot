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
import threading
from typing import Callable
from urllib.parse import quote_plus

from ..http import get_text
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
# Product slugs anywhere in the page - search results render without hrefs but
# embed product data (incl. slugs like johnnie-walker-...-700ml_30663) in JSON
# state. Bounded by quotes/slashes in JSON, so \b anchoring is enough.
SLUG_ANYWHERE_RE = re.compile(r"\b([a-z0-9][a-z0-9-]{8,}_\d{4,})\b", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# --------------------------------------------------------------------------- #
# Sitemap-based product URL resolution.
#
# Liquorland's search results render without product <a href>s and rate-limit
# bursts behind a captcha, but its XML sitemaps are static, bot-friendly and
# list every product URL (which all end in the _<sku> suffix). One fetch per
# run resolves every unknown product; the product pages themselves scrape
# reliably.
# --------------------------------------------------------------------------- #
SITEMAP_INDEX_URL = "https://www.liquorland.com.au/sitemap.xml"
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+?)\s*</loc>", re.I)
_PRODUCT_PATH_RE = re.compile(r"https?://(?:www\.)?liquorland\.com\.au(/[^\s<>\"']*_\d{3,})", re.I)
_MAX_SUB_SITEMAPS = 15

_sitemap_lock = threading.Lock()
_sitemap_cache: dict[str, object] = {"paths": None, "error": None}


def _load_sitemap_paths(session, timeout: float, log) -> list[str]:
    """Fetch (once per process) every product path in Liquorland's sitemaps."""
    with _sitemap_lock:
        if _sitemap_cache["paths"] is not None:
            return _sitemap_cache["paths"]  # type: ignore[return-value]
        if _sitemap_cache["error"] is not None:
            raise ScrapeFailure(f"sitemap unavailable: {_sitemap_cache['error']}")
        try:
            index = get_text(session, SITEMAP_INDEX_URL, timeout=timeout)
            paths: list[str] = _PRODUCT_PATH_RE.findall(index)
            sub_maps = [
                loc for loc in _LOC_RE.findall(index) if loc.lower().endswith(".xml")
            ][:_MAX_SUB_SITEMAPS]
            for loc in sub_maps:
                try:
                    body = get_text(session, loc, timeout=timeout)
                except Exception as exc:  # noqa: BLE001 - one bad sub-sitemap shouldn't kill the run
                    log.debug("Could not fetch sub-sitemap %s: %s", loc, exc)
                    continue
                paths.extend(_PRODUCT_PATH_RE.findall(body))
            deduped = list(dict.fromkeys(paths))
            log.info("Liquorland sitemap: %d product URLs collected", len(deduped))
            _sitemap_cache["paths"] = deduped
            return deduped
        except ScrapeFailure:
            raise
        except Exception as exc:  # noqa: BLE001
            _sitemap_cache["error"] = f"{type(exc).__name__}: {exc}"
            raise ScrapeFailure(f"sitemap unavailable: {_sitemap_cache['error']}")


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
        else:
            out.append(("sitemap", self.strategy_sitemap))
        if self.config.use_playwright:
            out.append(("browser-search", self.strategy_browser_search))
        return out

    # ------------------------------------------------------------------ #
    def strategy_sitemap(self) -> PriceResult:
        """Resolve the product URL from Liquorland's sitemaps, then scrape it."""
        assert self.product is not None
        paths = _load_sitemap_paths(self.session, self.config.http_timeout, self.log)
        match = None
        for path in paths:
            slug = path.rsplit("/", 1)[-1]
            if self.product.matches_name(slug):
                match = path
                break
        if match is None:
            raise ScrapeFailure(
                f"no matching product among {len(paths)} sitemap URLs"
            )
        resolved = "https://www.liquorland.com.au" + match
        self.log.info("RESOLVED %s url=%s (bake into products.py)", self.key, resolved)
        self.product_url = resolved

        try:
            html = get_text(self.session, resolved, timeout=self.config.http_timeout)
            self._dump(html, "sitemap-static")
            return self._result_from_html(html, "sitemap-static")
        except ScrapeFailure:
            if not self.config.use_playwright:
                raise
        from ..browser import rendered_page

        with rendered_page(
            resolved,
            user_agent=self.config.user_agent,
            headless=self.config.headless,
            timeout_ms=self.config.page_timeout_ms,
            wait_selector=self.wait_selector,
        ) as html:
            self._dump(html, "sitemap-browser")
            return self._result_from_html(html, "sitemap-browser")

    # ------------------------------------------------------------------ #
    def _pick_product_link(self, html: str) -> str | None:
        """First search-result product whose slug matches the catalog spec.

        Tries real hrefs first; falls back to product slugs embedded anywhere
        in the page (Liquorland's results grid renders without <a href>s but
        carries the slugs in its JSON state).
        """
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
        for match in SLUG_ANYWHERE_RE.finditer(html):
            slug = match.group(1).lower()
            if slug in seen:
                continue
            seen.add(slug)
            if self.product.matches_name(slug):
                return "/spirits/" + slug
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
                embedded = [m.group(1).lower() for m in SLUG_ANYWHERE_RE.finditer(html)]
                embedded = list(dict.fromkeys(embedded))
                if slugs or embedded:
                    detail = (
                        f"{len(slugs)} href links, {len(embedded)} embedded slugs seen: "
                        + "; ".join((slugs + embedded)[:6])[:240]
                    )
                else:
                    title_match = TITLE_RE.search(html)
                    title = (title_match.group(1).strip()[:80] if title_match else "?")
                    detail = (
                        f"0 product slugs seen; page title={title!r}, "
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
