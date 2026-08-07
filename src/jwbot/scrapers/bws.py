"""BWS (Endeavour Group) scrapers - one registered class per catalog product.

Strategy order per product:
  1. BWS public UI API by stockcode (fast, clean JSON) - when the code is known
  2. BWS search API - finds the product by name; also self-heals a stale
     stockcode and logs the discovered one so it can be baked into products.py
  3. Static product HTML (JSON-LD / embedded state) - when the URL is known
  4. Playwright-rendered product HTML - when the URL is known
"""

from __future__ import annotations

from typing import Any, Callable

from ..extract import find_availability_in_json, find_multibuys, find_price_in_json
from ..http import get_json
from ..models import MultiBuy, PriceResult
from ..products import PRODUCTS, ProductSpec
from .base import BaseScraper, ScrapeFailure, register

API_ROOT = "https://api.bws.com.au/apis/ui"


class BWSScraper(BaseScraper):
    """Base for every BWS product scraper (not registered itself)."""

    display_name = "BWS"
    stockcode: str | None = None

    prefer_json_keys = ("singleprice", "sellprice", "onlineprice", "price")
    price_selectors = (
        '[data-testid="product-price"]',
        ".product-price",
        ".price__value",
        ".product__price",
        'span[class*="price"]',
    )
    wait_selector = 'h1, [data-testid="product-price"]'

    @property
    def search_term(self) -> str:
        assert self.product is not None
        return self.product.search_term

    # ------------------------------------------------------------------ #
    def _api_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://bws.com.au",
            "Referer": self.product_url or "https://bws.com.au/",
        }

    def prime_session(self, session) -> None:  # type: ignore[no-untyped-def]
        """Pick up cookies from the homepage so the API accepts us."""
        try:
            session.get("https://bws.com.au/", timeout=self.config.http_timeout)
        except Exception as exc:  # noqa: BLE001
            self.log.debug("Could not prime BWS session: %s", exc)

    # ------------------------------------------------------------------ #
    def strategies(self) -> list[tuple[str, Callable[[], PriceResult]]]:
        out: list[tuple[str, Callable[[], PriceResult]]] = []
        if self.stockcode:
            out.append(("api-product", self.strategy_api_product))
        out.append(("api-search", self.strategy_api_search))
        if self.product_url:
            out.append(("static-html", self.strategy_static_html))
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
        import json as _json

        self._dump(_json.dumps(payload, indent=2, default=str)[:500000], "api-search", ext="json")
        node = self._best_search_match(payload)
        if node is None:
            candidates = self._candidate_names(payload)
            if candidates:
                # The API answered with real products, just not this one:
                # BWS doesn't list it. That's a quiet "not listed", not an error.
                sample = "; ".join(candidates[:5])[:220]
                self.log.warning(
                    "No %s in BWS search results - treating as not listed (saw: %s)",
                    self.product.label if self.product else "match",
                    sample,
                )
                return self.make_result(
                    None, False, None, "api-search:not-listed", note=None
                )
            raise ScrapeFailure("no product names in BWS search response")
        stockcode = node.get("Stockcode") or node.get("stockcode")
        if stockcode and str(stockcode) != (self.stockcode or ""):
            self.log.info(
                "RESOLVED %s stockcode=%s name=%r (bake into products.py)",
                self.key,
                stockcode,
                node.get("Name") or node.get("name"),
            )
        return self._from_api_payload(node, "api-search")

    # ------------------------------------------------------------------ #
    def _best_search_match(self, payload: Any) -> Any:
        """Pick the exact catalog product - not gift packs, other sizes or
        sibling expressions."""
        from ..extract import iter_json_objects

        assert self.product is not None
        best = None
        for obj in iter_json_objects(payload):
            name = obj.get("Name") or obj.get("name")
            stockcode = obj.get("Stockcode") or obj.get("stockcode")
            if not isinstance(name, str):
                continue
            if self.stockcode and str(stockcode) == self.stockcode:
                return obj
            if not self.product.matches_name(name):
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
        deals = [
            MultiBuy(quantity=q, total_price=t, description=text)
            for q, t, text in find_multibuys(payload)
        ]
        self._log_offer_fields(payload)
        return self.make_result(
            price=price,
            available=True if available is None else bool(available),
            product_name=self._name_from_payload(payload),
            strategy=f"{label}:{hint}",
            on_special=self._looks_on_special(payload),
            multibuy=deals,
        )

    def _log_offer_fields(self, payload: Any) -> None:
        """Record promo-looking fields so a real run reveals BWS's exact shape.

        Bulk offers are parsed from wording ("2 for $110"); this line exists so
        that if BWS ever describes one only in structured fields, the log shows
        what to teach the parser next.
        """
        from ..extract import iter_json_objects

        seen: list[str] = []
        for obj in iter_json_objects(payload):
            for raw_key, raw_value in obj.items():
                key = str(raw_key).lower()
                if not any(word in key for word in ("promo", "offer", "multibuy", "bulk", "deal")):
                    continue
                snippet = f"{raw_key}={str(raw_value)[:90]}"
                if snippet not in seen:
                    seen.append(snippet)
        if seen:
            self.log.info("OFFER-FIELDS %s: %s", self.key, " | ".join(seen[:8])[:600])

    @staticmethod
    def _looks_on_special(payload: Any) -> bool | None:
        """Best-effort promo flag from the API payload. None = field absent."""
        from ..extract import iter_json_objects

        saw_flag = False
        for obj in iter_json_objects(payload):
            for key, value in obj.items():
                lowered = key.lower() if isinstance(key, str) else ""
                if lowered in {"isonspecial", "onspecial", "isspecial", "ismemberoffer"}:
                    saw_flag = True
                    if bool(value):
                        return True
                elif lowered in {"promoprice", "specialprice"}:
                    inner = value.get("Value") if isinstance(value, dict) else value
                    if isinstance(inner, (int, float)) and inner:
                        return True
        return False if saw_flag else None

    @staticmethod
    def _candidate_names(payload: Any) -> list[str]:
        """Names of actual products (objects that carry a stockcode) - facet
        and metadata objects also have name fields, so filter those out."""
        from ..extract import iter_json_objects

        names: list[str] = []
        for obj in iter_json_objects(payload):
            name = obj.get("Name") or obj.get("name")
            stockcode = obj.get("Stockcode") or obj.get("stockcode")
            if not stockcode:
                continue
            if isinstance(name, str) and name.strip() and name.strip() not in names:
                names.append(name.strip())
        return names

    def _name_from_payload(self, payload: Any) -> str | None:
        from ..extract import iter_json_objects

        assert self.product is not None
        fallback = None
        for obj in iter_json_objects(payload):
            name = obj.get("Name") or obj.get("name")
            if not isinstance(name, str):
                continue
            if self.product.matches_name(name):
                return name
            if fallback is None:
                fallback = name
        return fallback


def _make_class(spec: ProductSpec) -> type[BWSScraper]:
    return type(
        f"BWS_{spec.key.replace('-', '_')}",
        (BWSScraper,),
        {
            "key": f"bws:{spec.key}",
            "product": spec,
            "product_url": spec.bws.url or "",
            "stockcode": spec.bws.stockcode,
            "expected_name_tokens": spec.name_tokens,
        },
    )


for _spec in PRODUCTS:
    register(_make_class(_spec))
