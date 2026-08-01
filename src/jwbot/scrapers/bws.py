"""BWS (Endeavour Group) scraper.

Strategy order:
  1. BWS public UI API (fast, clean JSON)   -> api.bws.com.au/apis/ui/Product/<stockcode>
  2. BWS search API (in case the stockcode changes)
  3. Static product HTML (JSON-LD / embedded state)
  4. Playwright-rendered product HTML
"""

from __future__ import annotations

from typing import Any, Callable

from ..extract import find_availability_in_json, find_price_in_json
from ..http import get_json
from ..models import PriceResult
from .base import BaseScraper, ScrapeFailure, register

API_ROOT = "https://api.bws.com.au/apis/ui"


@register
class BWSScraper(BaseScraper):
    key = "bws"
    display_name = "BWS"
    stockcode = "9067"
    product_url = "https://bws.com.au/product/9067/johnnie-walker-black-label-scotch-whisky-700ml"
    search_term = "Johnnie Walker Black Label 700ml"

    prefer_json_keys = ("singleprice", "sellprice", "onlineprice", "price")
    price_selectors = (
        '[data-testid="product-price"]',
        ".product-price",
        ".price__value",
        ".product__price",
        'span[class*="price"]',
    )
    wait_selector = 'h1, [data-testid="product-price"]'
    expected_name_tokens = ("johnnie", "walker", "black")

    # ------------------------------------------------------------------ #
    def _api_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://bws.com.au",
            "Referer": self.product_url,
        }

    def prime_session(self, session) -> None:  # type: ignore[no-untyped-def]
        """Pick up cookies from the homepage so the API accepts us."""
        try:
            session.get("https://bws.com.au/", timeout=self.config.http_timeout)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Could not prime BWS session: %s", exc)

    # ------------------------------------------------------------------ #
    def strategies(self) -> list[tuple[str, Callable[[], PriceResult]]]:
        out: list[tuple[str, Callable[[], PriceResult]]] = [
            ("api-product", self.strategy_api_product),
            ("api-search", self.strategy_api_search),
            ("static-html", self.strategy_static_html),
        ]
        if self.config.use_playwright:
            out.append(("browser", self.strategy_browser))
        return out

    # ------------------------------------------------------------------ #
    def strategy_api_product(self) -> PriceResult:
        url = f"{API_ROOT}/Product/{self.stockcode}"
        payload = get_json(
            self.session, url, timeout=self.config.http_timeout, headers=self._api_headers()
        )
        return self._from_api_payload(payload, "api-product")

    def strategy_api_search(self) -> PriceResult:
        url = f"{API_ROOT}/Search/products"
        params = {
            "searchTerm": self.search_term,
            "pageNumber": 1,
            "pageSize": 12,
            "sortType": "Relevance",
        }
        payload = get_json(
            self.session,
            url,
            timeout=self.config.http_timeout,
            headers=self._api_headers(),
            params=params,
        )
        node = self._best_search_match(payload)
        if node is None:
            raise ScrapeFailure("no matching product in BWS search results")
        return self._from_api_payload(node, "api-search")

    # ------------------------------------------------------------------ #
    def _best_search_match(self, payload: Any) -> Any:
        """Pick the plain 700mL Black Label, not gift packs or Double Black."""
        from ..extract import iter_json_objects

        best = None
        for obj in iter_json_objects(payload):
            name = obj.get("Name") or obj.get("name")
            stockcode = obj.get("Stockcode") or obj.get("stockcode")
            if not isinstance(name, str):
                continue
            lowered = name.lower()
            if str(stockcode) == self.stockcode:
                return obj
            if not all(token in lowered for token in self.expected_name_tokens):
                continue
            if "700" not in lowered:
                continue
            if any(bad in lowered for bad in ("gift", "glass", "double", "sherry", "ruby", "icons", "origin", "1l", "1 litre")):
                continue
            if best is None:
                best = obj
        return best

    def _from_api_payload(self, payload: Any, label: str) -> PriceResult:
        price, hint = find_price_in_json(payload, prefer_keys=self.prefer_json_keys)
        if price is None:
            available = find_availability_in_json(payload)
            if available is False:
                return self.make_result(
                    None, False, self._name_from_payload(payload), f"{label}:out-of-stock"
                )
            raise ScrapeFailure(f"no price in {label} response")

        available = find_availability_in_json(payload)
        return self.make_result(
            price=price,
            available=True if available is None else bool(available),
            product_name=self._name_from_payload(payload),
            strategy=f"{label}:{hint}",
        )

    @staticmethod
    def _name_from_payload(payload: Any) -> str | None:
        from ..extract import iter_json_objects

        for obj in iter_json_objects(payload):
            name = obj.get("Name") or obj.get("name")
            if isinstance(name, str) and "johnnie" in name.lower():
                return name
        return None
