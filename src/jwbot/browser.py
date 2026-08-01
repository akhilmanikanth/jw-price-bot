"""Optional Playwright rendering for JavaScript-heavy retailer sites.

Kept isolated so the rest of the project works even when Playwright / Chromium
is not installed - the scrapers simply skip the browser strategy.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger(__name__)

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-AU', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


@contextmanager
def rendered_page(
    url: str,
    user_agent: str,
    headless: bool = True,
    timeout_ms: int = 45000,
    wait_selector: str | None = None,
    wait_after_load_ms: int = 1500,
    block_media: bool = True,
) -> Iterator[str]:
    """Yield the fully rendered HTML of `url`.

    Raises RuntimeError if Playwright or its browser binary is unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Playwright is not installed: {exc}") from exc

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Could not launch Chromium. Run: python -m playwright install chromium"
            ) from exc

        context = browser.new_context(
            user_agent=user_agent,
            locale="en-AU",
            timezone_id="Australia/Sydney",
            viewport={"width": 1440, "height": 900},
            geolocation={"latitude": -33.8688, "longitude": 151.2093},
            permissions=["geolocation"],
            extra_http_headers={"Accept-Language": "en-AU,en;q=0.9"},
        )
        context.add_init_script(_STEALTH_JS)

        if block_media:
            def _route(route):
                if route.request.resource_type in {"image", "media", "font"}:
                    return route.abort()
                return route.continue_()

            context.route("**/*", _route)

        page = context.new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 20000))
                except Exception:
                    log.debug("wait_selector %r not found on %s", wait_selector, url)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
            except Exception:
                pass
            if wait_after_load_ms:
                page.wait_for_timeout(wait_after_load_ms)
            yield page.content()
        finally:
            try:
                context.close()
            finally:
                browser.close()
