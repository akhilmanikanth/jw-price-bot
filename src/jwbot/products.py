"""Product catalog: every bottle the bot tracks, at every retailer.

Adding a bottle = one ProductSpec entry in PRODUCTS, or - zero-code - a
`/addbottle <name> <size>` message to the bot, which stores the spec in
`data/products-custom.json`. That file is read here at import time, so the
cloud runs and the local bot both pick custom bottles up automatically.
Retailer references (canonical URL, BWS stockcode) are optional: when they
are missing, the scrapers fall back to their search-based strategies, resolve
the product by name, and log what they found so the reference can be baked in
later.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SKU_SUFFIX_RE = re.compile(r"_\d+$")

# Tokens that mark bundles and variants we never want, for any product.
GLOBAL_EXCLUDES = (
    "gift",
    "glass",
    "pack",
    "case",
    "cradle",
    "miniature",
    "50ml",
    "200ml",
    "375ml",
)


@dataclass(frozen=True)
class RetailerRef:
    """What we know about one product at one retailer (all optional)."""

    url: str | None = None
    stockcode: str | None = None  # BWS / Endeavour stock code


@dataclass(frozen=True)
class ProductSpec:
    key: str  # machine key, e.g. "jw-black-700"
    label: str  # full human label used in headings
    short_label: str  # compact label for one-line summaries
    search_term: str  # what to type into a retailer's search box
    name_tokens: tuple[str, ...]  # ALL must appear in a candidate name
    size_tokens: tuple[str, ...]  # at least ONE must appear
    exclude_tokens: tuple[str, ...] = ()  # none may appear (plus GLOBAL_EXCLUDES)
    brief: bool = False  # True => compact "Also watching" line
    liquorland: RetailerRef = field(default_factory=RetailerRef)
    bws: RetailerRef = field(default_factory=RetailerRef)

    def matches_name(self, name: str) -> bool:
        """Does a retailer's product name / URL slug look like this product?"""
        if not name:
            return False
        # Strip Liquorland-style trailing SKUs (...-700ml_30663) so their digits
        # can't collide with numeric size/exclude tokens like "12" or "700".
        lowered = _SKU_SUFFIX_RE.sub("", name.lower()).replace("-", " ")
        if not all(token in lowered for token in self.name_tokens):
            return False
        if not any(token in lowered for token in self.size_tokens):
            return False
        for token in self.exclude_tokens:
            if token in lowered:
                return False
        for token in GLOBAL_EXCLUDES:
            # A global size-based exclude (e.g. "375ml" for miniatures) must
            # not veto a product whose *wanted* size it is.
            if any(size in token for size in self.size_tokens):
                continue
            if token in lowered:
                return False
        return True


SIZE_700 = ("700",)
SIZE_1L = ("1l", "1 l", "1 litre", "1litre", "1000ml", "1000 ml")

# Aged / special Ballantine's expressions that must not match the base blend.
_BALLANTINES_AGED = (
    "12",
    "17",
    "21",
    "23",
    "30",
    "7 year",
    "bourbon",
    "light",
    "brasil",
)

# Excludes that keep the two Black Label sizes from matching each other,
# and keep special editions out of the plain Black Label results.
_BLACK_EXCLUDES = (
    "double",
    "triple",
    "sherry",
    "ruby",
    "origin",
    "icons",
    "speyside",
    "islay",
    "lowlands",
)

BUILTIN_PRODUCTS: tuple[ProductSpec, ...] = (
    ProductSpec(
        key="jw-black-700",
        label="Johnnie Walker Black Label 700mL",
        short_label="Black Label 700mL",
        search_term="johnnie walker black label",
        name_tokens=("johnnie", "walker", "black"),
        size_tokens=SIZE_700,
        exclude_tokens=(*_BLACK_EXCLUDES, *SIZE_1L),
        brief=False,
        liquorland=RetailerRef(
            url=(
                "https://www.liquorland.com.au/spirits/"
                "johnnie-walker-black-label-12yo-scotch-whisky-700ml_30663"
            )
        ),
        bws=RetailerRef(
            url="https://bws.com.au/product/9067/johnnie-walker-black-label-scotch-whisky-700ml",
            stockcode="9067",
        ),
    ),
    ProductSpec(
        key="jw-black-1l",
        label="Johnnie Walker Black Label 1 Litre",
        short_label="Black Label 1L",
        search_term="johnnie walker black label",
        name_tokens=("johnnie", "walker", "black"),
        size_tokens=SIZE_1L,
        exclude_tokens=(*_BLACK_EXCLUDES, "700"),
        brief=False,
        bws=RetailerRef(
            # Resolved live on 2026-08-01: "Johnnie Walker Black Label 12 Year
            # Old Blended Scotch Whisky 1l"
            url="https://bws.com.au/product/776048/johnnie-walker-black-label-blended-scotch-whisky-1l",
            stockcode="776048",
        ),
    ),
    ProductSpec(
        key="jw-blue-700",
        label="Johnnie Walker Blue Label 700mL",
        short_label="Blue Label 700mL",
        search_term="johnnie walker blue label",
        name_tokens=("johnnie", "walker", "blue"),
        size_tokens=SIZE_700,
        exclude_tokens=("king george", "ghost", "ultra", "elusive", *SIZE_1L),
        brief=True,
        bws=RetailerRef(
            # Resolved live on 2026-08-01: "Johnnie Walker Blue Label Blended
            # Scotch Whisky 700ml"
            url="https://bws.com.au/product/93472/johnnie-walker-blue-label-blended-scotch-whisky-700ml",
            stockcode="93472",
        ),
    ),
    # BWS names the base blend plainly "Ballantine's Scotch Whisky 700ml" -
    # no "Finest" - so the finest specs match on the brand alone and exclude
    # the aged / special expressions instead.
    ProductSpec(
        key="ballantines-finest-700",
        label="Ballantine's Finest 700mL",
        short_label="Ballantine's Finest 700mL",
        search_term="ballantines finest",
        name_tokens=("ballantine",),
        size_tokens=SIZE_700,
        exclude_tokens=(*_BALLANTINES_AGED, *SIZE_1L, "500"),
        brief=True,
        liquorland=RetailerRef(
            # Slug surfaced by the 2026-08-01 live run's search diagnostics.
            url="https://www.liquorland.com.au/spirits/ballantines-scotch-whisky-700ml_30151"
        ),
    ),
    ProductSpec(
        key="ballantines-finest-1l",
        label="Ballantine's Finest 1 Litre",
        short_label="Ballantine's Finest 1L",
        search_term="ballantines finest",
        name_tokens=("ballantine",),
        size_tokens=SIZE_1L,
        exclude_tokens=(*_BALLANTINES_AGED, "700", "500"),
        brief=True,
    ),
    ProductSpec(
        key="ballantines-12-700",
        label="Ballantine's 12 Year Old 700mL",
        short_label="Ballantine's 12YO 700mL",
        search_term="ballantines 12 year old",
        name_tokens=("ballantine", "12"),
        size_tokens=SIZE_700,
        exclude_tokens=("finest", "7 year", "21", "30 year", "bourbon", *SIZE_1L),
        brief=True,
        liquorland=RetailerRef(
            # Resolved live on 2026-08-01 ($62.00 via json-ld).
            url="https://www.liquorland.com.au/spirits/ballantines-12yo-blended-scotch-whisky-700ml_4521560"
        ),
    ),
)

# --------------------------------------------------------------------------- #
# Custom bottles (added via /addbottle, stored as JSON so no code change is
# ever needed to track a new product).
# --------------------------------------------------------------------------- #
CUSTOM_PRODUCTS_PATH = Path(
    os.getenv("CUSTOM_PRODUCTS_PATH") or PROJECT_ROOT / "data" / "products-custom.json"
)


def spec_from_custom_dict(raw: dict) -> ProductSpec:
    """Build a ProductSpec from one products-custom.json entry."""
    refs = {}
    for retailer in ("liquorland", "bws"):
        node = raw.get(retailer) or {}
        refs[retailer] = RetailerRef(url=node.get("url"), stockcode=node.get("stockcode"))
    return ProductSpec(
        key=str(raw["key"]),
        label=str(raw["label"]),
        short_label=str(raw.get("short_label") or raw["label"]),
        search_term=str(raw["search_term"]),
        name_tokens=tuple(raw["name_tokens"]),
        size_tokens=tuple(raw["size_tokens"]),
        exclude_tokens=tuple(raw.get("exclude_tokens", ())),
        brief=bool(raw.get("brief", True)),
        liquorland=refs["liquorland"],
        bws=refs["bws"],
    )


def spec_to_custom_dict(spec: ProductSpec) -> dict:
    out: dict = {
        "key": spec.key,
        "label": spec.label,
        "short_label": spec.short_label,
        "search_term": spec.search_term,
        "name_tokens": list(spec.name_tokens),
        "size_tokens": list(spec.size_tokens),
        "exclude_tokens": list(spec.exclude_tokens),
        "brief": spec.brief,
    }
    if spec.liquorland.url or spec.liquorland.stockcode:
        out["liquorland"] = {"url": spec.liquorland.url, "stockcode": spec.liquorland.stockcode}
    if spec.bws.url or spec.bws.stockcode:
        out["bws"] = {"url": spec.bws.url, "stockcode": spec.bws.stockcode}
    return out


def load_custom_specs(path: Path | None = None) -> tuple[ProductSpec, ...]:
    """Read custom bottle specs; a broken file never breaks the run."""
    path = Path(path) if path else CUSTOM_PRODUCTS_PATH
    if not path.exists():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Could not read custom products file %s: %s", path, exc)
        return ()
    specs: list[ProductSpec] = []
    builtin_keys = {p.key for p in BUILTIN_PRODUCTS}
    for entry in raw.get("products", []) if isinstance(raw, dict) else []:
        try:
            spec = spec_from_custom_dict(entry)
        except (KeyError, TypeError, ValueError) as exc:
            log.error("Skipping invalid custom product entry %r: %s", entry, exc)
            continue
        if spec.key in builtin_keys or any(s.key == spec.key for s in specs):
            log.warning("Skipping duplicate custom product key %r", spec.key)
            continue
        specs.append(spec)
    return tuple(specs)


def save_custom_specs(specs: Iterable[ProductSpec], path: Path | None = None) -> None:
    path = Path(path) if path else CUSTOM_PRODUCTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"products": [spec_to_custom_dict(s) for s in specs]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


PRODUCTS: tuple[ProductSpec, ...] = BUILTIN_PRODUCTS + load_custom_specs()

PRODUCTS_BY_KEY: dict[str, ProductSpec] = {p.key: p for p in PRODUCTS}


def custom_specs() -> tuple[ProductSpec, ...]:
    """The currently loaded custom (non-builtin) specs."""
    builtin = {p.key for p in BUILTIN_PRODUCTS}
    return tuple(p for p in PRODUCTS if p.key not in builtin)


def add_spec_runtime(spec: ProductSpec) -> None:
    """Make a new spec visible to the *running* process.

    PRODUCTS_BY_KEY is mutated in place (shared by every importer); PRODUCTS is
    rebound, which existing `from ... import PRODUCTS` holders won't see - the
    formatting fallback handles that by consulting PRODUCTS_BY_KEY.
    """
    global PRODUCTS
    if spec.key in PRODUCTS_BY_KEY:
        raise ValueError(f"product key already exists: {spec.key}")
    PRODUCTS = PRODUCTS + (spec,)
    PRODUCTS_BY_KEY[spec.key] = spec


def remove_spec_runtime(key: str) -> None:
    global PRODUCTS
    PRODUCTS = tuple(p for p in PRODUCTS if p.key != key)
    PRODUCTS_BY_KEY.pop(key, None)


# --------------------------------------------------------------------------- #
# "/addbottle Johnnie Walker Red Label 700ml" -> ProductSpec
# --------------------------------------------------------------------------- #
_GENERIC_WORDS = {
    "whisky", "whiskey", "scotch", "blended", "blend", "the", "a", "an", "of",
    "year", "years", "old", "yo", "bottle",
}
_SIZE_1L_RE = re.compile(r"\b(1\s*l(itre|iter)?|1000\s*ml)\b", re.I)
_SIZE_ML_RE = re.compile(r"\b(\d{3,4})\s*ml\b", re.I)
_SIZE_700_BARE_RE = re.compile(r"\b700\b")


def spec_from_text(text: str) -> ProductSpec:
    """Parse a free-text bottle description into a trackable spec.

    Raises ValueError with a user-facing message when the size is missing.
    """
    original = " ".join(text.split())
    if not original:
        raise ValueError("Tell me the bottle, e.g. /addbottle Johnnie Walker Red Label 700ml")

    lowered = original.lower()
    size_tokens: tuple[str, ...]
    size_suffix: str
    size_slug: str
    if _SIZE_1L_RE.search(lowered):
        size_tokens, size_suffix, size_slug = SIZE_1L, "1 Litre", "1l"
        stripped = _SIZE_1L_RE.sub(" ", lowered)
    else:
        ml = _SIZE_ML_RE.search(lowered)
        if ml:
            digits = ml.group(1)
            stripped = _SIZE_ML_RE.sub(" ", lowered)
        elif _SIZE_700_BARE_RE.search(lowered):
            digits = "700"
            stripped = _SIZE_700_BARE_RE.sub(" ", lowered)
        else:
            raise ValueError(
                "I need a size - end with e.g. 700ml, 1 litre or 375ml. "
                "Example: /addbottle Chivas Regal 12 700ml"
            )
        size_tokens, size_suffix, size_slug = (digits,), f"{digits}mL", digits

    words = re.findall(r"[a-z0-9']+", stripped)
    tokens = []
    for word in words:
        word = re.sub(r"'s$", "", word).replace("'", "")
        if not word or word in _GENERIC_WORDS:
            continue
        if word not in tokens:
            tokens.append(word)
    if not tokens:
        raise ValueError("I couldn't find a product name in that. Example: /addbottle Chivas Regal 12 700ml")

    # Rebuild a clean label from the pre-size words of the original text.
    pre_size = _SIZE_1L_RE.sub(" ", lowered)
    pre_size = _SIZE_ML_RE.sub(" ", pre_size)
    pre_size = _SIZE_700_BARE_RE.sub(" ", pre_size)
    label_words = [w if w.isdigit() else w.capitalize() for w in pre_size.split()]
    label_base = " ".join(label_words) or " ".join(tokens).title()
    key = "-".join(tokens + [size_slug])

    return ProductSpec(
        key=key,
        label=f"{label_base} {size_suffix}",
        short_label=f"{label_base} {size_suffix}",
        search_term=" ".join(pre_size.split()),
        name_tokens=tuple(tokens),
        size_tokens=size_tokens,
        exclude_tokens=(),
        brief=True,
    )


def resolve_product(text: str) -> tuple[ProductSpec | None, list[ProductSpec]]:
    """Find the catalog product a user means by key or by words.

    Returns (match, candidates): match is set when exactly one product fits.
    """
    cleaned = " ".join(text.split()).lower()
    if not cleaned:
        return None, []
    if cleaned in PRODUCTS_BY_KEY:
        return PRODUCTS_BY_KEY[cleaned], [PRODUCTS_BY_KEY[cleaned]]
    words = [re.sub(r"'s$", "", w).replace("'", "") for w in re.findall(r"[a-z0-9']+", cleaned)]
    words = [w for w in words if w]
    if not words:
        return None, []
    candidates = []
    for spec in PRODUCTS_BY_KEY.values():
        haystack = f"{spec.key.replace('-', ' ')} {spec.label.lower()} {spec.short_label.lower()}"
        haystack = haystack.replace("'", "")
        if all(w in haystack for w in words):
            candidates.append(spec)
    return (candidates[0] if len(candidates) == 1 else None), candidates


# History rows written before multi-product support used the bare retailer key.
# Map them onto the product they were actually tracking so week-on-week
# comparison survives the upgrade.
LEGACY_KEY_ALIASES: dict[str, str] = {
    "liquorland": "liquorland:jw-black-700",
    "bws": "bws:jw-black-700",
}

DEFAULT_LEGACY_PRODUCT = "jw-black-700"


def apply_legacy_aliases(prices: dict[str, float]) -> dict[str, float]:
    """Merge pre-multi-product history keys into their modern equivalents."""
    merged = dict(prices)
    for old_key, new_key in LEGACY_KEY_ALIASES.items():
        if old_key in merged and new_key not in merged:
            merged[new_key] = merged[old_key]
    return merged
