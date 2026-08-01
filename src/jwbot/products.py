"""Product catalog: every bottle the bot tracks, at every retailer.

Adding a bottle = one ProductSpec entry in PRODUCTS. Retailer references
(canonical URL, BWS stockcode) are optional: when they are missing, the
scrapers fall back to their search-based strategies, resolve the product by
name, and log what they found so the reference can be baked in later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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
        # Strip Liquorland-style trailing SKUs (…-700ml_30663) so their digits
        # can't collide with numeric size/exclude tokens like "12" or "700".
        lowered = _SKU_SUFFIX_RE.sub("", name.lower()).replace("-", " ")
        if not all(token in lowered for token in self.name_tokens):
            return False
        if not any(token in lowered for token in self.size_tokens):
            return False
        for token in (*self.exclude_tokens, *GLOBAL_EXCLUDES):
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

PRODUCTS: tuple[ProductSpec, ...] = (
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

PRODUCTS_BY_KEY: dict[str, ProductSpec] = {p.key: p for p in PRODUCTS}

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
