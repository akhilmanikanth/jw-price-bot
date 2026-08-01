"""Build the Telegram message."""

from __future__ import annotations

from datetime import datetime
from html import escape

from .history import diff
from .models import RunReport

PRODUCT_LABEL = "Johnnie Walker Black Label 700mL"

TREND_ICONS = {
    "up": "\U0001F53A",
    "down": "\U0001F53B",
    "same": "➖",
    "new": "\U0001F195",
    "unknown": "",
}


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


def build_message(
    report: RunReport,
    previous_prices: dict[str, float] | None = None,
    checked_at: datetime | None = None,
    include_links: bool = True,
) -> str:
    """Telegram HTML-formatted message."""
    previous_prices = previous_prices or {}
    moment = checked_at or datetime.fromisoformat(report.started_at)

    lines: list[str] = [
        f"\U0001F3F7 <b>{escape(PRODUCT_LABEL)} Weekly Price Update</b>",
        "",
    ]
    if report.manual:
        lines.insert(1, "<i>Manual check</i>")

    for result in report.results:
        name = escape(result.display_name)
        label = f"<a href=\"{escape(result.url)}\">{name}</a>" if (include_links and result.url) else name

        if result.error:
            lines.append(f"\U0001F943 {label}: ⚠️ unavailable (error)")
            continue
        if result.price is None or not result.available:
            lines.append(f"\U0001F943 {label}: ❌ Out of stock / not listed")
            continue

        direction, delta = diff(result, previous_prices.get(result.retailer))
        icon = TREND_ICONS.get(direction, "")
        trend = ""
        if direction in {"up", "down"} and delta is not None:
            was = previous_prices.get(result.retailer)
            trend = f"  {icon} {'+' if delta > 0 else '−'}{money(abs(delta))} (was {money(was)})"
        elif direction == "same":
            trend = f"  {icon} no change"
        elif direction == "new":
            trend = f"  {icon} first reading"

        extra = f" <i>({escape(result.note)})</i>" if result.note else ""
        lines.append(f"\U0001F943 {label}: <b>{money(result.price)}</b>{extra}{trend}")

    lines.append("")

    cheapest = report.cheapest
    if cheapest is not None:
        others = [r for r in report.successful if r.retailer != cheapest.retailer]
        saving = ""
        if others:
            gap = round(min(r.price for r in others) - cheapest.price, 2)  # type: ignore[type-var]
            if gap > 0:
                saving = f" (save {money(gap)})"
        lines.append(
            f"\U0001F4B0 <b>Cheapest today: {escape(cheapest.display_name)} - {money(cheapest.price)}</b>{saving}"
        )
    else:
        lines.append("\U0001F4B0 <b>Cheapest today:</b> no prices retrieved")

    lines.append("")
    lines.append(f"\U0001F4C5 Checked: {escape(format_check_time(moment))}")

    problems = [r for r in report.results if r.error]
    if problems or report.errors:
        lines.append("")
        lines.append("⚠️ <b>Issues</b>")
        for result in problems:
            detail = (result.error or "").strip()
            if len(detail) > 220:
                detail = detail[:217] + "..."
            lines.append(f"• {escape(result.display_name)}: <code>{escape(detail)}</code>")
        for extra in report.errors:
            trimmed = extra if len(extra) <= 220 else extra[:217] + "..."
            lines.append(f"• <code>{escape(trimmed)}</code>")

    return "\n".join(lines)


def build_history_message(history, retailers: list[tuple[str, str]], limit: int = 8) -> str:
    """A compact '/history' style summary."""
    lines = [f"\U0001F4C8 <b>{escape(PRODUCT_LABEL)} - recent prices</b>", ""]
    any_data = False
    for key, display in retailers:
        series = history.price_series(key, limit=limit)
        if not series:
            continue
        any_data = True
        lines.append(f"<b>{escape(display)}</b>")
        for run_key, price in series:
            lines.append(f"  {escape(run_key)}  {money(price)}")
        lines.append("")
    if not any_data:
        return "No price history recorded yet. Run /check to take the first reading."
    return "\n".join(lines).strip()
