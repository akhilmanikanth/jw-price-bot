"""Build the Telegram message."""

from __future__ import annotations

from datetime import datetime
from html import escape

from .history import diff
from .models import PriceResult, RunReport
from .products import (
    DEFAULT_LEGACY_PRODUCT,
    PRODUCTS,
    PRODUCTS_BY_KEY,
    apply_legacy_aliases,
)

TITLE = "Whisky Weekly Price Update"

TREND_ICONS = {
    "up": "\U0001F53A",
    "down": "\U0001F53B",
    "same": "➖",
    "new": "\U0001F195",
    "unknown": "",
}


def esc(text: str) -> str:
    """Escape for Telegram HTML *text* nodes: <, > and & only - apostrophes
    and quotes stay readable."""
    return escape(text, quote=False)


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def format_check_time(moment: datetime) -> str:
    """e.g. 'Friday, 07 Aug 2026 - 3:00 PM (AEST)'."""
    day = moment.strftime("%A")
    date = moment.strftime("%d %b %Y")
    hour = moment.strftime("%I").lstrip("0") or "12"
    clock = f"{hour}:{moment.strftime('%M %p')}"
    tzname = moment.tzname() or ""
    suffix = f" ({tzname})" if tzname else ""
    return f"{day}, {date} - {clock}{suffix}"


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #
def _product_key_of(result: PriceResult) -> str:
    if result.product_key:
        return result.product_key
    # Rows recorded before multi-product support belong to the original bottle.
    return DEFAULT_LEGACY_PRODUCT


def _group_by_product(report: RunReport) -> list[tuple[str, str, bool, list[PriceResult]]]:
    """-> [(product_key, label, brief, results)] in catalog order; unknown keys last."""
    buckets: dict[str, list[PriceResult]] = {}
    for result in report.results:
        buckets.setdefault(_product_key_of(result), []).append(result)

    groups: list[tuple[str, str, bool, list[PriceResult]]] = []
    for spec in PRODUCTS:
        if spec.key in buckets:
            groups.append((spec.key, spec.label, spec.brief, buckets.pop(spec.key)))
    for key, results in buckets.items():  # products no longer in the catalog
        label = results[0].product_label or key
        groups.append((key, label, False, results))
    return groups


def _short_label(product_key: str, fallback: str) -> str:
    spec = PRODUCTS_BY_KEY.get(product_key)
    return spec.short_label if spec else fallback


def _linkify(result: PriceResult, include_links: bool) -> str:
    name = esc(result.display_name)
    if include_links and result.url:
        return f'<a href="{escape(result.url)}">{name}</a>'
    return name


# --------------------------------------------------------------------------- #
# Full block (main products)
# --------------------------------------------------------------------------- #
def _full_block(
    label: str,
    results: list[PriceResult],
    previous_prices: dict[str, float],
    include_links: bool,
) -> list[str]:
    lines = [f"<b>{esc(label)}</b>"]

    for result in results:
        tag = _linkify(result, include_links)
        if result.error:
            lines.append(f"\U0001F943 {tag}: ⚠️ unavailable (error)")
            continue
        if result.price is None or not result.available:
            lines.append(f"\U0001F943 {tag}: ❌ Out of stock / not listed")
            continue

        was = previous_prices.get(result.retailer)
        direction, delta = diff(result, was)
        icon = TREND_ICONS.get(direction, "")
        trend = ""
        if direction in {"up", "down"} and delta is not None:
            trend = f"  {icon} {'+' if delta > 0 else '−'}{money(abs(delta))} (was {money(was)})"
        elif direction == "same":
            trend = f"  {icon} no change"
        elif direction == "new":
            trend = f"  {icon} first reading"

        extra = f" <i>({esc(result.note)})</i>" if result.note else ""
        lines.append(f"\U0001F943 {tag}: <b>{money(result.price)}</b>{extra}{trend}")

    good = [r for r in results if r.ok]
    if good:
        cheapest = min(good, key=lambda r: r.price)  # type: ignore[arg-type]
        others = [r for r in good if r.retailer != cheapest.retailer]
        saving = ""
        if others:
            gap = round(min(r.price for r in others) - cheapest.price, 2)  # type: ignore[type-var]
            if gap > 0:
                saving = f" (save {money(gap)})"
        lines.append(
            f"\U0001F4B0 <b>Cheapest today: {esc(cheapest.display_name)} - {money(cheapest.price)}</b>{saving}"
        )
    else:
        lines.append("\U0001F4B0 <b>Cheapest today:</b> no prices retrieved")
    return lines


# --------------------------------------------------------------------------- #
# Brief line (watch-list products)
# --------------------------------------------------------------------------- #
def _brief_segments(
    results: list[PriceResult],
    previous_prices: dict[str, float],
    include_links: bool,
) -> tuple[str, list[str]]:
    """-> (signal, [segment, ...]) where signal drives the line icon.

    Forcing signals (error/up/down/new) render their own line; "oos" and
    "same" are stable and collapse into the shared no-change line.
    """
    signal_rank = {"error": 0, "up": 1, "down": 2, "new": 3, "oos": 4, "same": 5}
    signal = "same"
    segments: list[str] = []

    for result in results:
        tag = _linkify(result, include_links)
        if result.error:
            segments.append(f"{tag} ⚠️")
            state = "error"
        elif result.price is None or not result.available:
            segments.append(f"{tag} ❌")
            state = "oos"
        else:
            was = previous_prices.get(result.retailer)
            direction, delta = diff(result, was)
            if direction == "up":
                segments.append(f"{tag} <b>{money(result.price)}</b> (\U0001F53A +{money(delta)})")
                state = "up"
            elif direction == "down":
                segments.append(f"{tag} <b>{money(result.price)}</b> (\U0001F53B −{money(abs(delta))})")
                state = "down"
            elif direction == "new":
                segments.append(f"{tag} <b>{money(result.price)}</b> \U0001F195")
                state = "new"
            else:
                segments.append(f"{tag} <b>{money(result.price)}</b> ➖")
                state = "same"
        if signal_rank[state] < signal_rank[signal]:
            signal = state

    return signal, segments


SIGNAL_ICONS = {
    "error": "⚠️",
    "oos": "❌",
    "up": "\U0001F53A",
    "down": "\U0001F53B",
    "new": "\U0001F195",
    "same": "➖",
}


# --------------------------------------------------------------------------- #
def build_message(
    report: RunReport,
    previous_prices: dict[str, float] | None = None,
    checked_at: datetime | None = None,
    include_links: bool = True,
) -> str:
    """Telegram HTML-formatted message."""
    previous_prices = apply_legacy_aliases(previous_prices or {})
    moment = checked_at or datetime.fromisoformat(report.started_at)

    lines: list[str] = [f"\U0001F3F7 <b>{TITLE}</b>", ""]
    if report.manual:
        lines.insert(1, "<i>Manual check</i>")

    groups = _group_by_product(report)

    for _key, label, brief, results in groups:
        if brief:
            continue
        lines.extend(_full_block(label, results, previous_prices, include_links))
        lines.append("")

    watch = [(key, label, results) for key, label, brief, results in groups if brief]
    if watch:
        lines.append("\U0001F4CC <b>Also watching</b>")
        unchanged: list[str] = []
        for key, label, results in watch:
            signal, segments = _brief_segments(results, previous_prices, include_links)
            short = esc(_short_label(key, label))
            if signal in {"same", "oos"}:
                # Stable states collapse: a bottle a retailer simply doesn't
                # list shouldn't shout every single week.
                good = [r for r in results if r.ok]
                cheapest = min(good, key=lambda r: r.price) if good else None  # type: ignore[arg-type]
                unchanged.append(
                    f"{short} {money(cheapest.price)}" if cheapest else f"{short} not listed"
                )
            else:
                lines.append(f"{SIGNAL_ICONS[signal]} {short}: " + " · ".join(segments))
        if unchanged:
            lines.append("➖ No change: " + ", ".join(unchanged))
        lines.append("")

    lines.append(f"\U0001F4C5 Checked: {esc(format_check_time(moment))}")

    problems = [r for r in report.results if r.error]
    if problems or report.errors:
        lines.append("")
        lines.append("⚠️ <b>Issues</b>")
        for result in problems:
            detail = (result.error or "").strip()
            if len(detail) > 220:
                detail = detail[:217] + "..."
            where = esc(result.display_name)
            what = _short_label(_product_key_of(result), result.product_label or "")
            label = f"{where} ({esc(what)})" if what else where
            lines.append(f"• {label}: <code>{esc(detail)}</code>")
        for extra in report.errors:
            trimmed = extra if len(extra) <= 220 else extra[:217] + "..."
            lines.append(f"• <code>{esc(trimmed)}</code>")

    return "\n".join(lines)


def build_history_message(history, listings: list[tuple[str, str]], limit: int = 8) -> str:
    """A compact '/history' style summary. `listings` = [(scraper_key, label)]."""
    lines = ["\U0001F4C8 <b>Recent prices</b>", ""]
    any_data = False
    for key, display in listings:
        series = history.price_series(key, limit=limit)
        if not series:
            continue
        any_data = True
        lines.append(f"<b>{esc(display)}</b>")
        for run_key, price in series:
            lines.append(f"  {esc(run_key)}  {money(price)}")
        lines.append("")
    if not any_data:
        return "No price history recorded yet. Run /check to take the first reading."
    return "\n".join(lines).strip()
