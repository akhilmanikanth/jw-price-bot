"""Generic price / availability extraction helpers.

These are deliberately retailer-agnostic: most Australian liquor sites expose the
price in at least one of these places, so a new retailer usually needs only a URL
and (optionally) a couple of CSS selectors.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Iterator

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

PRICE_RE = re.compile(r"\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)")
NUMBER_RE = re.compile(r"^\s*\$?\s*(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)\s*$")

OUT_OF_STOCK_MARKERS = (
    "out of stock",
    "outofstock",
    "sold out",
    "currently unavailable",
    "not available",
    "unavailable online",
    "temporarily unavailable",
    "no longer available",
    "discontinued",
)

# Keys commonly used for price inside embedded JSON blobs.
PRICE_KEYS = (
    "singleprice",
    "sellprice",
    "saleprice",
    "currentprice",
    "nowprice",
    "onlineprice",
    "shelfprice",
    "price",
    "value",
    "amount",
    "pricevalue",
    "displayprice",
    "listprice",
    "wasprice",
)


def parse_price(text: Any) -> float | None:
    """Parse a price out of a string / number. Returns None when nothing sane found."""
    if text is None:
        return None
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        value = float(text)
        return value if is_plausible_price(value) else None

    s = str(text).strip()
    if not s:
        return None

    m = NUMBER_RE.match(s)
    if m:
        value = float(m.group(1).replace(",", ""))
        return value if is_plausible_price(value) else None

    m = PRICE_RE.search(s)
    if m:
        value = float(m.group(1).replace(",", ""))
        return value if is_plausible_price(value) else None

    # Loose fallback, e.g. "55.00 AUD". Deliberately requires a decimal point:
    # without it we would happily read 12 out of "12 Year Old" or 40 out of "40% ABV".
    if any(word in s.lower() for word in ("year", "yo ", "% ", "abv", "rating", "star")):
        return None
    m = re.search(r"(\d{1,4}(?:,\d{3})*\.\d{1,2})(?!\s*%)", s)
    if m:
        value = float(m.group(1).replace(",", ""))
        return value if is_plausible_price(value) else None
    return None


def is_plausible_price(value: float) -> bool:
    """Guard against grabbing '12' from '12 Year Old' or a rating out of 5."""
    return 5.0 <= value <= 5000.0


def looks_out_of_stock(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in OUT_OF_STOCK_MARKERS)


# --------------------------------------------------------------------------- #
# JSON walking
# --------------------------------------------------------------------------- #

def iter_json_objects(node: Any) -> Iterator[dict]:
    """Yield every dict nested anywhere inside a JSON-ish structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_json_objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_json_objects(item)


def find_price_in_json(node: Any, prefer_keys: Iterable[str] = ()) -> tuple[float | None, str | None]:
    """Search a JSON structure for a plausible price.

    Returns (price, key_path_hint).
    """
    # Each key is scanned across the whole tree before moving to the next, so a
    # caller's first choice ("singleprice") always beats a generic "price" key
    # sitting higher up in the document.
    ordered: list[tuple[str, str]] = [(k.lower().replace("_", "").replace(" ", ""), "preferred") for k in prefer_keys]
    seen = {k for k, _ in ordered}
    ordered += [(k, "generic") for k in PRICE_KEYS if k not in seen]

    objects = list(iter_json_objects(node))

    for wanted, label in ordered:
        for obj in objects:
            for raw_key, raw_value in obj.items():
                if not isinstance(raw_key, str):
                    continue
                if raw_key.lower().replace("_", "").replace(" ", "") != wanted:
                    continue
                # The value may itself be {"Value": 55.0} or {"amount": ...}.
                if isinstance(raw_value, dict):
                    nested, _ = find_price_in_json(raw_value, prefer_keys=("value", "amount", "price"))
                    if nested is not None:
                        return nested, f"{label}:{raw_key}"
                    continue
                if isinstance(raw_value, list):
                    continue
                price = parse_price(raw_value)
                if price is not None:
                    return price, f"{label}:{raw_key}"
    return None, None


def find_availability_in_json(node: Any) -> bool | None:
    """Look for common availability flags. None = unknown."""
    for obj in iter_json_objects(node):
        for raw_key, raw_value in obj.items():
            if not isinstance(raw_key, str):
                continue
            key = raw_key.lower().replace("_", "")
            if key in {"availability", "stockstatus", "availabilitystatus"}:
                if isinstance(raw_value, str):
                    lowered = raw_value.lower()
                    if "outofstock" in lowered.replace(" ", "") or "sold" in lowered:
                        return False
                    if "instock" in lowered.replace(" ", "") or "available" in lowered:
                        return True
            if key in {"isavailable", "available", "instock", "isinstock", "issellable"}:
                if isinstance(raw_value, bool):
                    return raw_value
    return None


# --------------------------------------------------------------------------- #
# HTML extraction
# --------------------------------------------------------------------------- #

def _script_payloads(soup: BeautifulSoup) -> Iterator[tuple[str, Any]]:
    """Yield (source_label, parsed_json) for every embeddable JSON script tag."""
    for tag in soup.find_all("script"):
        script_type = (tag.get("type") or "").lower()
        text = tag.string or tag.get_text() or ""
        text = text.strip()
        if not text:
            continue

        if "ld+json" in script_type:
            for candidate in _loads_lenient(text):
                yield "json-ld", candidate
            continue

        tag_id = (tag.get("id") or "").lower()
        if tag_id in {"__next_data__", "__nuxt_data__", "serverapp-state", "ng-state"} or "json" in script_type:
            for candidate in _loads_lenient(text):
                yield f"script#{tag_id or script_type}", candidate
            continue

        # window.__X__ = {...};  /  window.__INITIAL_STATE__ = {...}
        if "window." in text and "=" in text and "{" in text:
            for match in re.finditer(r"window\.[\w$.]+\s*=\s*(\{.*?\})\s*;?\s*(?:\n|$)", text, re.S):
                for candidate in _loads_lenient(match.group(1)):
                    yield "window-state", candidate


def _loads_lenient(text: str) -> list[Any]:
    """Try hard to turn a script body into JSON. Returns 0..n parsed objects."""
    out: list[Any] = []
    try:
        out.append(json.loads(text))
        return out
    except Exception:
        pass
    # Some sites concatenate several JSON-LD objects, or wrap in HTML comments.
    cleaned = text.strip().lstrip("/*").rstrip("*/").strip()
    try:
        out.append(json.loads(cleaned))
        return out
    except Exception:
        pass
    # Last resort: pull the biggest balanced {...} block.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            out.append(json.loads(cleaned[start : end + 1]))
        except Exception:
            pass
    return out


def extract_from_jsonld(soup: BeautifulSoup) -> tuple[float | None, bool | None, str | None, str | None]:
    """Return (price, available, product_name, strategy) from schema.org markup."""
    for label, payload in _script_payloads(soup):
        if label != "json-ld":
            continue
        for obj in iter_json_objects(payload):
            obj_type = obj.get("@type") or obj.get("type")
            types = [obj_type] if isinstance(obj_type, str) else list(obj_type or [])
            if not any(str(t).lower() == "product" for t in types):
                continue
            name = obj.get("name") if isinstance(obj.get("name"), str) else None
            offers = obj.get("offers")
            price, _hint = find_price_in_json(offers, prefer_keys=("price", "lowprice"))
            available = find_availability_in_json(offers)
            if price is not None:
                return price, available, name, "json-ld"
    return None, None, None, None


def extract_from_meta(soup: BeautifulSoup) -> tuple[float | None, str | None]:
    selectors = (
        ('meta[itemprop="price"]', "content"),
        ('meta[property="product:price:amount"]', "content"),
        ('meta[property="og:price:amount"]', "content"),
        ('[itemprop="price"]', "content"),
    )
    for css, attr in selectors:
        for tag in soup.select(css):
            price = parse_price(tag.get(attr) or tag.get_text())
            if price is not None:
                return price, f"meta:{css}"
    return None, None


def extract_from_embedded_json(
    soup: BeautifulSoup, prefer_keys: Iterable[str] = ()
) -> tuple[float | None, bool | None, str | None]:
    for label, payload in _script_payloads(soup):
        if label == "json-ld":
            continue
        price, hint = find_price_in_json(payload, prefer_keys=prefer_keys)
        if price is not None:
            available = find_availability_in_json(payload)
            return price, available, f"embedded:{label}:{hint}"
    return None, None, None


def extract_from_selectors(soup: BeautifulSoup, selectors: Iterable[str]) -> tuple[float | None, str | None]:
    for css in selectors:
        try:
            nodes = soup.select(css)
        except Exception:  # invalid selector - don't kill the run
            continue
        for node in nodes:
            for attr in ("data-price", "data-product-price", "content", "value"):
                if node.has_attr(attr):
                    price = parse_price(node.get(attr))
                    if price is not None:
                        return price, f"selector:{css}[{attr}]"
            price = parse_price(node.get_text(" ", strip=True))
            if price is not None:
                return price, f"selector:{css}"
    return None, None


def extract_price_from_html(
    html: str,
    selectors: Iterable[str] = (),
    prefer_json_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """Best-effort extraction. Returns dict with price/available/product_name/strategy."""
    soup = BeautifulSoup(html, "lxml")

    result: dict[str, Any] = {
        "price": None,
        "available": None,
        "product_name": None,
        "strategy": None,
    }

    price, available, name, strategy = extract_from_jsonld(soup)
    if price is not None:
        result.update(price=price, available=available, product_name=name, strategy=strategy)

    if result["price"] is None:
        price, strategy = extract_from_selectors(soup, selectors)
        if price is not None:
            result.update(price=price, strategy=strategy)

    if result["price"] is None:
        price, available, strategy = extract_from_embedded_json(soup, prefer_keys=prefer_json_keys)
        if price is not None:
            result.update(price=price, available=available, strategy=strategy)

    if result["price"] is None:
        price, strategy = extract_from_meta(soup)
        if price is not None:
            result.update(price=price, strategy=strategy)

    if result["product_name"] is None:
        heading = soup.select_one("h1")
        if heading:
            result["product_name"] = heading.get_text(" ", strip=True) or None
        if not result["product_name"]:
            title = soup.find("title")
            if title:
                result["product_name"] = title.get_text(" ", strip=True) or None

    if result["available"] is None:
        # Only inspect the main content-ish text to avoid footer noise.
        text = soup.get_text(" ", strip=True)[:20000]
        if looks_out_of_stock(text):
            result["available"] = False
        elif result["price"] is not None:
            result["available"] = True

    return result
