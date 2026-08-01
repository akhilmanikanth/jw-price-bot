from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jwbot.config import Config
from jwbot.formatting import build_message, format_check_time, money
from jwbot.history import History, diff
from jwbot.models import PriceResult, RunReport
from jwbot.runner import is_due, run_key_for

SYD = ZoneInfo("Australia/Sydney")


def make_report(prices, run_key="2026-08-07", manual=False, errors=None):
    results = []
    for key, display, price, err in prices:
        results.append(
            PriceResult(
                retailer=key,
                display_name=display,
                product_name="Johnnie Walker Black Label 700mL",
                price=price,
                url=f"https://example.com/{key}",
                available=price is not None,
                error=err,
            )
        )
    return RunReport(
        run_key=run_key,
        started_at=datetime(2026, 8, 7, 15, 0, tzinfo=SYD).isoformat(),
        results=results,
        errors=errors or [],
        manual=manual,
    )


class TestFormatting:
    def test_money(self):
        assert money(55) == "$55.00"
        assert money(1234.5) == "$1,234.50"
        assert money(None) == "N/A"

    def test_check_time(self):
        moment = datetime(2026, 8, 7, 15, 0, tzinfo=SYD)
        text = format_check_time(moment)
        assert text.startswith("Friday, 07 Aug 2026 - 3:00 PM")

    def test_full_message(self):
        report = make_report(
            [
                ("liquorland", "Liquorland", 65.00, None),
                ("bws", "BWS", 55.00, None),
            ]
        )
        msg = build_message(report, previous_prices={"liquorland": 63.0, "bws": 55.0})
        assert "Johnnie Walker Black Label 700mL Weekly Price Update" in msg
        assert "Liquorland" in msg and "$65.00" in msg
        assert "BWS" in msg and "$55.00" in msg
        assert "Cheapest today: BWS - $55.00" in msg
        assert "save $10.00" in msg
        assert "\U0001F53A" in msg          # Liquorland went up
        assert "➖ no change" in msg  # BWS unchanged
        assert "Friday, 07 Aug 2026 - 3:00 PM" in msg

    def test_one_retailer_down(self):
        report = make_report(
            [
                ("liquorland", "Liquorland", None, "network error (ConnectionError)"),
                ("bws", "BWS", 55.00, None),
            ]
        )
        msg = build_message(report, previous_prices={})
        assert "$55.00" in msg
        assert "Cheapest today: BWS" in msg
        assert "Issues" in msg
        assert "network error" in msg

    def test_all_retailers_down(self):
        report = make_report(
            [
                ("liquorland", "Liquorland", None, "HTTP 503"),
                ("bws", "BWS", None, "timeout"),
            ]
        )
        msg = build_message(report, previous_prices={})
        assert "no prices retrieved" in msg
        assert "HTTP 503" in msg

    def test_out_of_stock(self):
        report = make_report([("bws", "BWS", None, None)])
        msg = build_message(report, previous_prices={})
        assert "Out of stock" in msg

    def test_html_is_escaped(self):
        report = make_report([("bws", "BWS", None, "<script>bad & worse</script>")])
        msg = build_message(report, previous_prices={})
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg


class TestDiff:
    def test_directions(self):
        up = PriceResult(retailer="a", display_name="A", price=60.0, available=True)
        assert diff(up, 55.0) == ("up", 5.0)
        assert diff(up, 65.0) == ("down", -5.0)
        assert diff(up, 60.0) == ("same", 0.0)
        assert diff(up, None) == ("new", None)


class TestHistory:
    def test_roundtrip_and_previous(self, tmp_path):
        path = tmp_path / "history.json"
        history = History(path)

        history.upsert(make_report([("bws", "BWS", 55.0, None)], run_key="2026-07-31"))
        history.mark_notified("2026-07-31")
        history.save()

        reloaded = History(path)
        assert reloaded.already_notified("2026-07-31") is True
        assert reloaded.previous_prices() == {"bws": 55.0}
        assert reloaded.previous_prices(exclude_run_key="2026-07-31") == {}

    def test_manual_runs_excluded_from_comparison(self, tmp_path):
        path = tmp_path / "history.json"
        history = History(path)
        history.upsert(make_report([("bws", "BWS", 55.0, None)], run_key="2026-07-31"))
        history.upsert(
            make_report([("bws", "BWS", 49.0, None)], run_key="2026-08-03-manual-101010", manual=True)
        )
        assert history.previous_prices() == {"bws": 55.0}
        assert history.previous_prices(include_manual=True) == {"bws": 49.0}

    def test_falls_back_to_older_successful_run(self, tmp_path):
        history = History(tmp_path / "history.json")
        history.upsert(make_report([("bws", "BWS", 55.0, None)], run_key="2026-07-24"))
        history.upsert(make_report([("bws", "BWS", None, "HTTP 500")], run_key="2026-07-31"))
        assert history.previous_prices() == {"bws": 55.0}

    def test_upsert_replaces_same_key(self, tmp_path):
        history = History(tmp_path / "history.json")
        history.upsert(make_report([("bws", "BWS", 55.0, None)], run_key="2026-08-07"))
        history.upsert(make_report([("bws", "BWS", 51.0, None)], run_key="2026-08-07"))
        assert len(history.runs) == 1
        assert history.runs[0].results[0].price == 51.0

    def test_corrupt_file_is_survivable(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text("{not json", encoding="utf-8")
        history = History(path)
        assert history.runs == []


class TestSchedule:
    @pytest.fixture
    def config(self):
        return Config(timezone_name="Australia/Sydney", schedule_day_of_week="fri", schedule_hour=15)

    def test_due_on_time(self, config):
        assert is_due(config, datetime(2026, 8, 7, 15, 0, tzinfo=SYD))

    def test_due_when_late(self, config):
        assert is_due(config, datetime(2026, 8, 7, 16, 30, tzinfo=SYD))

    def test_not_due_too_early(self, config):
        assert not is_due(config, datetime(2026, 8, 7, 14, 0, tzinfo=SYD))

    def test_not_due_wrong_day(self, config):
        assert not is_due(config, datetime(2026, 8, 6, 15, 0, tzinfo=SYD))

    def test_not_due_much_later(self, config):
        assert not is_due(config, datetime(2026, 8, 7, 19, 0, tzinfo=SYD))

    def test_aest_utc_slots(self, config):
        """Sanity-check both GitHub Actions cron slots across DST."""
        from datetime import timezone

        # AEST (winter): 04:00 UTC -> 14:00 Sydney (skip), 05:00 UTC -> 15:00 (run)
        assert not is_due(config, datetime(2026, 8, 7, 4, 0, tzinfo=timezone.utc).astimezone(SYD))
        assert is_due(config, datetime(2026, 8, 7, 5, 0, tzinfo=timezone.utc).astimezone(SYD))

        # AEDT (summer): 04:00 UTC -> 15:00 Sydney (run); the 05:00 slot lands at
        # 16:00 which is still inside the grace window and is caught by the
        # duplicate-notification guard instead.
        assert is_due(config, datetime(2026, 1, 9, 4, 0, tzinfo=timezone.utc).astimezone(SYD))

    def test_run_keys(self, config):
        moment = datetime(2026, 8, 7, 15, 0, tzinfo=SYD)
        assert run_key_for(moment) == "2026-08-07"
        assert run_key_for(moment, manual=True).startswith("2026-08-07-manual-")
