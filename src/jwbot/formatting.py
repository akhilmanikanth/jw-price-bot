"""Build the Telegram message.

Layout rules, in priority order:
  1. Lead with what he acts on: target hits, then the cheapest price per bottle.
  2. Call out bulk deals - "2 for $110" is the real price of a Black Label.
  3. Show only what MOVED in the changes block; steady prices collapse to a line.
  4. A retailer blocking us is not an error. Show the last known price and say
     when it was seen. Raw scraper diagnostics never reach the phone.
"""

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

TITLE = "WHISKY WATCH"


def esc(text: str) -> str:
    """Escape for Telegram HTML *text* nodes: <, > and & only - apostrophes
    and quotes stay readable."""
    return escape(text, quote=False)


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    if abs(value - round(value)) < 0.005:
        return f"${value:,.0f}"  # "$61" reads faster than "$61.00"
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


def _short_when(stamp: str, moment: datetime) -> str:
    """'1 Aug' / 'today' / 'yesterday' from an ISO-ish date string."""
    try:
        seen = datetime.fromisoformat(stamp[:10])
    except ValueError:
        return stamp
    days = (moment.date() - seen.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{seen.day} {seen.strftime('%b')}"


def _age_phrase(stamp: str, moment: datetime) -> str:
    """Compact 'how long has this price held' badge: 5d / 3w."""
    try:
        start = datetime.fromisoformat(stamp[:10])
    except ValueError:
        return ""
    days = (moment.date() - start.date()).days
    if days <= 0:
        return ""
    if days < 7:
        return f"{days}d"
    return f"{days // 7}w"


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
    for key, results in buckets.items():
        # Bottles added at runtime via /addbottle, or removed from the catalog.
        spec = PRODUCTS_BY_KEY.get(key)
        if spec is not None:
            groups.append((key, spec.label, spec.brief, results))
        else:
            groups.append((key, results[0].product_label or key, False, results))
    return groups


def _short_label(product_key: str, fallback: str) -> str:
    spec = PRODUCTS_BY_KEY.get(product_key)
    return spec.short_label if spec else fallback


def _shop(result: PriceResult, include_links: bool) -> str:
    name = esc(result.display_name)
    if include_links and result.url:
        return f'<a href="{escape(result.url)}">{name}</a>'
    return name


# --------------------------------------------------------------------------- #
def build_message(
    report: RunReport,
    previous_prices: dict[str, float] | None = None,
    checked_at: datetime | None = None,
    include_links: bool = True,
    price_since: dict[str, tuple[str, int]] | None = None,
    targets: dict[str, float] | None = None,
    last_known: dict[str, tuple[float, str]] | None = None,
) -> str:
    """Telegram HTML-formatted message."""
    previous_prices = apply_legacy_aliases(previous_prices or {})
    price_since = price_since or {}
    last_known = last_known or {}
    moment = checked_at or datetime.fromisoformat(report.started_at)
    groups = _group_by_product(report)

    lines: list[str] = [f"🥃 <b>{TITLE}</b>"]
    if report.manual:
        lines.append("⚡ <i>Manual check</i>")
    lines.append(f"📅 {esc(format_check_time(moment))}")

    # ---------------- target hits ---------------- #
    if targets:
        from .userdata import target_hits

        hits = target_hits(report.results, targets)
        if hits:
            lines += ["", "🎯 <b>TARGET HIT</b>"]
            for result, target in hits:
                short = _short_label(_product_key_of(result), result.product_label or "")
                lines.append(
                    f"   ✅ {esc(short)} — <b>{money(result.price)}</b> at "
                    f"{_shop(result, include_links)} <i>(target {money(target)})</i>"
                )

    # ---------------- best price per bottle + bulk deals ---------------- #
    best_lines: list[str] = []
    deal_lines: list[str] = []
    for key, label, _brief, results in groups:
        short = esc(_short_label(key, label))
        priced = [r for r in results if r.ok]
        if priced:
            cheapest = min(priced, key=lambda r: r.price)  # type: ignore[arg-type]
            others = [r for r in priced if r.retailer != cheapest.retailer]
            tail = ""
            if others:
                alt = min(others, key=lambda r: r.price)  # type: ignore[arg-type]
                tail = f" <i>· {esc(alt.display_name)} {money(alt.price)}</i>"
            flag = " 🏷" if cheapest.on_special else ""
            best_lines.append(
                f"   🥃 {short} — <b>{money(cheapest.price)}</b> "
                f"{_shop(cheapest, include_links)}{flag}{tail}"
            )
        for result in results:
            deal = result.best_multibuy
            if deal is None:
                continue
            saving = ""
            if result.price is not None:
                per_bottle = round(result.price - deal.unit_price, 2)
                if per_bottle > 0:
                    saving = f" — save {money(per_bottle)}/bottle"
            deal_lines.append(
                f"   🎉 {deal.quantity}× {short} at {esc(result.display_name)} = "
                f"<b>{money(deal.total_price)}</b> "
                f"(<b>{money(deal.unit_price)}</b> each){saving}"
            )

    if best_lines:
        lines += ["", "💰 <b>BEST PRICE NOW</b>", *best_lines]
    if deal_lines:
        lines += ["", "🎁 <b>BULK DEALS</b>", *deal_lines]

    # ---------------- movements / steady / unavailable ---------------- #
    moves: list[str] = []
    steady: list[str] = []
    stale: list[str] = []
    not_stocked: list[str] = []

    for key, label, _brief, results in groups:
        short = esc(_short_label(key, label))
        for result in results:
            shop = _shop(result, include_links)
            if result.error:
                seen = last_known.get(result.retailer)
                if seen:
                    price, stamp = seen
                    stale.append(
                        f"   ⏸ {short} · {shop} — last seen <b>{money(price)}</b>"
                        f" ({_short_when(stamp, moment)})"
                    )
                else:
                    stale.append(f"   ⏸ {short} · {shop} — no price on record yet")
                continue
            if result.price is None or not result.available:
                not_stocked.append(f"   • {short} · {shop}")
                continue

            was = previous_prices.get(result.retailer)
            direction, delta = diff(result, was)
            if direction == "up" and delta is not None:
                moves.append(
                    f"   🔺 {short} · {shop}  {money(was)} → <b>{money(result.price)}</b>"
                    f"  <b>+{money(abs(delta))}</b>"
                )
            elif direction == "down" and delta is not None:
                moves.append(
                    f"   🔻 {short} · {shop}  {money(was)} → <b>{money(result.price)}</b>"
                    f"  <b>−{money(abs(delta))}</b>"
                )
            elif direction == "new":
                moves.append(f"   🆕 {short} · {shop}  <b>{money(result.price)}</b>  first look")
            else:
                held = price_since.get(result.retailer)
                badge = _age_phrase(held[0], moment) if held else ""
                age = f" <i>({badge})</i>" if badge else ""
                steady.append(f"   {short} · {shop} {money(result.price)}{age}")

    if moves:
        lines += ["", "📊 <b>WHAT CHANGED</b>", *moves]
    if steady:
        lines += ["", "➖ <b>HOLDING STEADY</b>", *steady]
    if not_stocked:
        lines += ["", "🚫 <b>NOT STOCKED</b>", *not_stocked]
    if stale:
        lines += ["", "⏸ <b>COULDN'T CHECK TODAY</b>", *stale]

    # ---------------- footer ---------------- #
    blocked_shops = sorted({r.display_name for r in report.results if r.blocked})
    broken_shops = sorted({r.display_name for r in report.results if r.error and not r.blocked})
    notes: list[str] = []
    if blocked_shops:
        count = sum(1 for r in report.results if r.blocked)
        notes.append(
            f"🛡 {esc(' & '.join(blocked_shops))} blocked {count} check"
            f"{'s' if count != 1 else ''} today (bot protection) — "
            "last known prices shown above."
        )
    if broken_shops:
        notes.append(f"⚠️ {esc(' & '.join(broken_shops))} hit a real error — worth a look.")
    for extra in report.errors:
        trimmed = extra if len(extra) <= 160 else extra[:157] + "..."
        notes.append(f"⚠️ {esc(trimmed)}")
    if not report.successful:
        notes.append("⚠️ No prices at all this run.")
    if notes:
        lines += ["", *notes]

    return "\n".join(lines)


def build_history_message(history, listings: list[tuple[str, str]], limit: int = 8) -> str:
    """A compact '/history' style summary. `listings` = [(scraper_key, label)]."""
    lines = ["📈 <b>Recent prices</b>", ""]
    any_data = False
    for key, display in listings:
        series = history.price_series(key, limit=limit)
        if not series:
            continue
        any_data = True
        lines.append(f"<b>{esc(display)}</b>")
        for run_key, price in series:
            lines.append(f"   {esc(run_key)}  {money(price)}")
        lines.append("")
    if not any_data:
        return "No price history recorded yet. Run /check to take the first reading."
    return "\n".join(lines).strip()
