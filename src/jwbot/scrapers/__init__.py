"""Scraper package. Importing it registers every built-in retailer.

To add a retailer:
    1. Create `src/jwbot/scrapers/<name>.py` with a `@register`ed BaseScraper subclass.
    2. Add it to the import list below.
"""

from .base import BaseScraper, ScrapeFailure, get_scrapers, register, registry, summarise_errors

# Importing the modules is what populates the registry.
# Import order = the order retailers appear in the Telegram message.
from . import liquorland  # noqa: F401,E402
from . import bws  # noqa: F401,E402

__all__ = [
    "BaseScraper",
    "ScrapeFailure",
    "get_scrapers",
    "register",
    "registry",
    "summarise_errors",
]
