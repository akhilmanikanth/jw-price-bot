from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jwbot.config import Config, load_config
from jwbot.formatting import build_message, format_check_time, money
from jwbot.history import History, diff
from jwbot.models import MultiBuy, PriceResult, RunReport
from jwbot.products import PRODUCTS_BY_KEY, apply_legacy_aliases
from jwbot.runner import is_due, run_key_for

SYD = ZoneInfo("Australia/Sydney")


def make_report(prices, run_key="2026-08-07", manual=False, errors=None):
    """prices = [(scraper_key, display, price, error)].

    A scraper_key like "bws:jw-black-700" fills in the catalog product; a bare
    key ("bws") emulates a pre-multi-product row.
    """
    results = []
    for key, display, price, err in prices:
        _, _, product_part = key.partition(":")
        spec = PRODUCTS_BY_KEY.get(product_part)
        results.append(
            PriceResult(
                retailer=key,
                display_name=display,
                product_key=spec.key if spec else None,
                product_label=spec.label if spec else None,
                product_name=spec.label if spec else "Johnnie Walker Black Label 700mL",
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


LL_BLACK = "liquorland:jw-black-700"
BWS_BLACK = "bws:jw-black-700"
LL_1L = "liquorland:jw-black-1l"
BWS_1L = "bws:jw-black-1l"
LL_BLUE = "liquorland:jw-blue-700"
BWS_BLUE = "bws:jw-blue-700"
LL_FIN = "liquorland:ballantines-finest-700"
BWS_FIN = "bws:ballantines-finest-700"


class TestFormatting:
    def test_money_drops_pointless_cents(self):
        assert money(55) == "$55"
        assert money(55.00) == "$55"
        assert money(54.99) == "$54.99"
        assert money(1234.5) == "$1,234.50"
        assert money(None) == "N/A"

    def test_check_time(self):
        moment = datetime(2026, 8, 7, 15, 0, tzinfo=SYD)
        assert format_check_time(moment).startswith("Friday, 07 Aug 2026 - 3:00 PM")

    def test_header_and_best_price(self):
        report = make_report(
            [(LL_BLACK, "Liquorland", 65.00, None), (BWS_BLACK, "BWS", 55.00, None)]
        )
        msg = build_message(report, previous_prices={LL_BLACK: 63.0, BWS_BLACK: 55.0})
        assert "WHISKY WATCH" in msg
        assert "BEST PRICE NOW" in msg
        # Cheapest wins the headline, the other shop trails it.
        assert "Black Label 700mL — <b>$55</b>" in msg
        assert "Liquorland $65" in msg
        assert "Friday, 07 Aug 2026 - 3:00 PM" in msg

    def test_only_movers_appear_in_changes(self):
        report = make_report(
            [(LL_BLACK, "Liquorland", 65.00, None), (BWS_BLACK, "BWS", 55.00, None)]
        )
        msg = build_message(report, previous_prices={LL_BLACK: 63.0, BWS_BLACK: 55.0})
        assert "WHAT CHANGED" in msg
        assert "🔺" in msg and "+$2" in msg          # Liquorland moved up
        assert "HOLDING STEADY" in msg               # BWS did not
        assert "BWS" in msg.split("HOLDING STEADY")[1]

    def test_manual_flag(self):
        report = make_report([(BWS_BLACK, "BWS", 55.0, None)], manual=True)
        assert "Manual check" in build_message(report, previous_prices={})

    def test_legacy_rows_map_to_original_product(self):
        report = make_report(
            [("liquorland", "Liquorland", 61.00, None), ("bws", "BWS", 55.00, None)]
        )
        msg = build_message(report, previous_prices={"liquorland": 61.0, "bws": 58.0})
        assert "Black Label 700mL" in msg   # grouped under the OG bottle
        assert "🔻" in msg                   # BWS down vs the legacy baseline

    def test_every_bottle_gets_one_best_line(self):
        report = make_report(
            [
                (LL_BLACK, "Liquorland", 65.00, None),
                (BWS_BLACK, "BWS", 55.00, None),
                (LL_1L, "Liquorland", 88.00, None),
                (BWS_1L, "BWS", 84.00, None),
            ]
        )
        msg = build_message(report, previous_prices={})
        best = msg.split("BEST PRICE NOW")[1].split("\n\n")[0]
        assert best.count("🥃") == 2
        assert "Black Label 700mL" in best and "Black Label 1L" in best

    def test_watch_list_bottles_are_not_second_class(self):
        """Blue/Ballantine's share the same layout - no separate noisy block."""
        report = make_report(
            [(LL_BLUE, "Liquorland", 255.00, None), (BWS_BLUE, "BWS", 250.00, None)]
        )
        msg = build_message(report, previous_prices={LL_BLUE: 250.0, BWS_BLUE: 250.0})
        assert "Blue Label 700mL — <b>$250</b>" in msg
        assert "Also watching" not in msg

    def test_first_reading(self):
        msg = build_message(make_report([(BWS_BLUE, "BWS", 250.00, None)]), previous_prices={})
        assert "first look" in msg and "🆕" in msg

    def test_not_stocked_is_quiet(self):
        report = make_report(
            [(LL_BLUE, "Liquorland", 255.00, None), (BWS_BLUE, "BWS", None, None)]
        )
        msg = build_message(report, previous_prices={LL_BLUE: 255.0})
        assert "NOT STOCKED" in msg
        assert "COULDN'T CHECK" not in msg
        assert "⚠️" not in msg

    def test_out_of_stock_never_claims_a_price(self):
        msg = build_message(make_report([(BWS_BLACK, "BWS", None, None)]), previous_prices={})
        assert "NOT STOCKED" in msg
        assert "BEST PRICE NOW" not in msg

    def test_raw_scraper_errors_never_reach_the_phone(self):
        report = make_report(
            [(LL_FIN, "Liquorland", None, "browser-search: 0 slugs; <b>ShieldSquare</b>")]
        )
        report.results[0].blocked = True
        msg = build_message(report, previous_prices={})
        assert "ShieldSquare" not in msg
        assert "browser-search" not in msg
        assert "<b>ShieldSquare" not in msg
        assert "bot protection" in msg

    def test_run_level_errors_are_escaped(self):
        report = make_report([(BWS_BLACK, "BWS", 55.0, None)], errors=["<script>x</script>"])
        msg = build_message(report, previous_prices={})
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg


class TestBlockedHandling:
    def test_blocked_shows_last_known_price(self):
        report = make_report([(LL_FIN, "Liquorland", None, "blocked by bot protection")])
        report.results[0].blocked = True
        msg = build_message(
            report, previous_prices={}, last_known={LL_FIN: (52.0, "2026-08-01")}
        )
        assert "COULDN'T CHECK TODAY" in msg
        assert "last seen <b>$52</b>" in msg
        assert "1 Aug" in msg
        assert "🛡" in msg and "blocked 1 check" in msg

    def test_blocked_without_history_says_so(self):
        report = make_report([(LL_FIN, "Liquorland", None, "blocked")])
        report.results[0].blocked = True
        msg = build_message(report, previous_prices={}, last_known={})
        assert "no price on record yet" in msg

    def test_real_error_is_flagged_separately(self):
        msg = build_message(make_report([(BWS_BLACK, "BWS", None, "HTTP 500")]), previous_prices={})
        assert "hit a real error" in msg
        assert "🛡" not in msg

    def test_plural_wording(self):
        report = make_report(
            [(LL_FIN, "Liquorland", None, "blocked"), (LL_BLUE, "Liquorland", None, "blocked")]
        )
        for result in report.results:
            result.blocked = True
        assert "blocked 2 checks" in build_message(report, previous_prices={})


class TestMultiBuyInMessage:
    def test_bulk_deal_line(self):
        report = make_report([(BWS_BLACK, "BWS", 69.00, None)])
        report.results[0].multibuy = [
            MultiBuy(quantity=2, total_price=110.0, description="2 for $110")
        ]
        msg = build_message(report, previous_prices={})
        assert "BULK DEALS" in msg
        assert "2× Black Label 700mL at BWS = <b>$110</b>" in msg
        assert "<b>$55</b> each" in msg
        assert "save $14/bottle" in msg

    def test_deal_hidden_when_not_cheaper(self):
        report = make_report([(BWS_BLACK, "BWS", 50.00, None)])
        report.results[0].multibuy = [MultiBuy(quantity=2, total_price=110.0)]
        assert "BULK DEALS" not in build_message(report, previous_prices={})


class TestPriceSinceDisplay:
    def test_age_badge_on_steady_price(self):
        report = make_report([(BWS_BLACK, "BWS", 55.0, None)])
        msg = build_message(
            report,
            previous_prices={BWS_BLACK: 55.0},
            price_since={BWS_BLACK: ("2026-06-12", 8)},
        )
        assert "HOLDING STEADY" in msg
        assert "(8w)" in msg

    def test_no_badge_when_price_moved(self):
        report = make_report([(BWS_BLACK, "BWS", 55.0, None)])
        msg = build_message(
            report,
            previous_prices={BWS_BLACK: 60.0},
            price_since={BWS_BLACK: ("2026-06-12", 8)},
        )
        assert "8w" not in msg
        assert "🔻" in msg


class TestTargetsInMessage:
    def test_target_hit_line(self):
        report = make_report([(BWS_1L, "BWS", 79.0, None), (BWS_BLACK, "BWS", 55.0, None)])
        msg = build_message(report, previous_prices={}, targets={"jw-black-1l": 80.0})
        assert "TARGET HIT" in msg
        assert "Black Label 1L — <b>$79</b>" in msg
        assert "target $80" in msg

    def test_no_target_line_when_above(self):
        report = make_report([(BWS_1L, "BWS", 97.0, None)])
        assert "TARGET HIT" not in build_message(
            report, previous_prices={}, targets={"jw-black-1l": 80.0}
        )


class TestOnSpecialFlag:
    def test_special_flag_rendered(self):
        report = make_report([(BWS_BLACK, "BWS", 49.0, None)])
        report.results[0].on_special = True
        assert "🏷" in build_message(report, previous_prices={})

    def test_absent_flag_silent(self):
        report = make_report([(BWS_BLACK, "BWS", 55.0, None)])
        assert "🏷" not in build_message(report, previous_prices={})


class TestMessageFitsTelegram:
    def test_full_catalog_message_under_limit(self):
        rows = []
        for product in (
            "jw-black-700", "jw-black-1l", "jw-blue-700",
            "ballantines-finest-700", "ballantines-finest-1l", "ballantines-12-700",
        ):
            rows.append((f"liquorland:{product}", "Liquorland", 61.0, None))
            rows.append((f"bws:{product}", "BWS", 55.0, None))
        msg = build_message(make_report(rows), previous_prices={})
        assert len(msg) < 4096


class TestLegacyAliases:
    def test_merge(self):
        merged = apply_legacy_aliases({"liquorland": 61.0, "bws": 55.0})
        assert merged["liquorland:jw-black-700"] == 61.0
        assert merged["bws:jw-black-700"] == 55.0
        merged2 = apply_legacy_aliases({"bws": 55.0, "bws:jw-black-700": 54.0})
        assert merged2["bws:jw-black-700"] == 54.0


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
        history.upsert(make_report([(BWS_BLACK, "BWS", 55.0, None)], run_key="2026-07-31"))
        history.mark_notified("2026-07-31")
        history.save()

        reloaded = History(path)
        assert reloaded.already_notified("2026-07-31") is True
        assert reloaded.previous_prices() == {BWS_BLACK: 55.0}
        assert reloaded.previous_prices(exclude_run_key="2026-07-31") == {}

    def test_multibuy_survives_a_save_load_cycle(self, tmp_path):
        path = tmp_path / "history.json"
        history = History(path)
        report = make_report([(BWS_BLACK, "BWS", 69.0, None)], run_key="2026-08-07")
        report.results[0].multibuy = [
            MultiBuy(quantity=2, total_price=110.0, description="2 for $110")
        ]
        history.upsert(report)
        history.save()

        deals = History(path).runs[0].results[0].multibuy
        assert len(deals) == 1
        assert isinstance(deals[0], MultiBuy)
        assert deals[0].quantity == 2 and deals[0].unit_price == 55.0

    def test_manual_runs_excluded_from_comparison(self, tmp_path):
        history = History(tmp_path / "history.json")
        history.upsert(make_report([(BWS_BLACK, "BWS", 55.0, None)], run_key="2026-07-31"))
        history.upsert(
            make_report([(BWS_BLACK, "BWS", 49.0, None)], run_key="2026-08-03-manual-101010", manual=True)
        )
        assert history.previous_prices() == {BWS_BLACK: 55.0}
        assert history.previous_prices(include_manual=True) == {BWS_BLACK: 49.0}

    def test_falls_back_to_older_successful_run(self, tmp_path):
        history = History(tmp_path / "history.json")
        history.upsert(make_report([(BWS_BLACK, "BWS", 55.0, None)], run_key="2026-07-24"))
        history.upsert(make_report([(BWS_BLACK, "BWS", None, "HTTP 500")], run_key="2026-07-31"))
        assert history.previous_prices() == {BWS_BLACK: 55.0}

    def test_upsert_replaces_same_key(self, tmp_path):
        history = History(tmp_path / "history.json")
        history.upsert(make_report([(BWS_BLACK, "BWS", 55.0, None)], run_key="2026-08-07"))
        history.upsert(make_report([(BWS_BLACK, "BWS", 51.0, None)], run_key="2026-08-07"))
        assert len(history.runs) == 1
        assert history.runs[0].results[0].price == 51.0

    def test_old_history_rows_still_load(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text(
            """
            {"version": 1, "runs": [{"run_key": "2026-08-01", "started_at": "x",
              "notified": true, "manual": false, "errors": [],
              "results": [{"retailer": "bws", "display_name": "BWS",
                           "price": 55.0, "available": true}]}]}
            """,
            encoding="utf-8",
        )
        history = History(path)
        assert history.previous_prices() == {"bws": 55.0}
        assert apply_legacy_aliases(history.previous_prices())["bws:jw-black-700"] == 55.0

    def test_corrupt_file_is_survivable(self, tmp_path):
        path = tmp_path / "history.json"
        path.write_text("{not json", encoding="utf-8")
        assert History(path).runs == []


class TestLastKnown:
    def test_finds_most_recent_price(self, tmp_path):
        history = History(tmp_path / "history.json")
        history.upsert(make_report([(LL_FIN, "Liquorland", 52.0, None)], run_key="2026-08-01"))
        history.upsert(make_report([(LL_FIN, "Liquorland", None, "blocked")], run_key="2026-08-07"))
        assert history.last_known([LL_FIN], exclude_run_key="2026-08-07") == {
            LL_FIN: (52.0, "2026-08-01")
        }

    def test_walks_back_past_several_failures(self, tmp_path):
        history = History(tmp_path / "history.json")
        history.upsert(make_report([(LL_FIN, "Liquorland", 52.0, None)], run_key="2026-07-17"))
        for key in ("2026-07-24", "2026-07-31"):
            history.upsert(make_report([(LL_FIN, "Liquorland", None, "blocked")], run_key=key))
        assert history.last_known([LL_FIN]) == {LL_FIN: (52.0, "2026-07-17")}

    def test_manual_runs_count(self, tmp_path):
        history = History(tmp_path / "history.json")
        history.upsert(
            make_report([(LL_FIN, "Liquorland", 48.0, None)], run_key="2026-08-05-manual-1", manual=True)
        )
        assert history.last_known([LL_FIN])[LL_FIN][0] == 48.0

    def test_legacy_key_fallback(self, tmp_path):
        history = History(tmp_path / "history.json")
        history.upsert(make_report([("bws", "BWS", 55.0, None)], run_key="2026-07-24"))
        assert history.last_known([BWS_BLACK], reverse_aliases={BWS_BLACK: "bws"}) == {
            BWS_BLACK: (55.0, "2026-07-24")
        }

    def test_unknown_listing_absent(self, tmp_path):
        assert History(tmp_path / "history.json").last_known([LL_FIN]) == {}


class TestSchedule:
    @pytest.fixture
    def config(self):
        return Config(
            timezone_name="Australia/Sydney", schedule_days=("tue", "fri"), schedule_hour=15
        )

    def test_due_on_friday(self, config):
        assert is_due(config, datetime(2026, 8, 7, 15, 0, tzinfo=SYD))

    def test_due_on_tuesday(self, config):
        assert is_due(config, datetime(2026, 8, 4, 15, 0, tzinfo=SYD))

    def test_due_when_late(self, config):
        assert is_due(config, datetime(2026, 8, 4, 16, 30, tzinfo=SYD))

    def test_not_due_too_early(self, config):
        assert not is_due(config, datetime(2026, 8, 7, 14, 0, tzinfo=SYD))

    def test_not_due_on_other_days(self, config):
        for day in (3, 5, 6, 8, 9):  # Mon, Wed, Thu, Sat, Sun that week
            assert not is_due(config, datetime(2026, 8, day, 15, 0, tzinfo=SYD))

    def test_not_due_much_later(self, config):
        assert not is_due(config, datetime(2026, 8, 7, 19, 0, tzinfo=SYD))

    def test_both_utc_cron_slots_across_dst(self, config):
        from datetime import timezone

        # AEST (winter): 04:00 UTC -> 14:00 Sydney (skip), 05:00 UTC -> 15:00 (run)
        assert not is_due(config, datetime(2026, 8, 7, 4, 0, tzinfo=timezone.utc).astimezone(SYD))
        assert is_due(config, datetime(2026, 8, 7, 5, 0, tzinfo=timezone.utc).astimezone(SYD))
        # AEDT (summer): 04:00 UTC -> 15:00 Sydney (run)
        assert is_due(config, datetime(2026, 1, 9, 4, 0, tzinfo=timezone.utc).astimezone(SYD))
        # Tuesday behaves identically
        assert is_due(config, datetime(2026, 8, 4, 5, 0, tzinfo=timezone.utc).astimezone(SYD))

    def test_unparseable_days_are_treated_as_due(self):
        assert is_due(Config(schedule_days=("noneday",)), datetime(2026, 8, 5, 15, 0, tzinfo=SYD))

    def test_run_keys(self, config):
        moment = datetime(2026, 8, 7, 15, 0, tzinfo=SYD)
        assert run_key_for(moment) == "2026-08-07"
        assert run_key_for(moment, manual=True).startswith("2026-08-07-manual-")


class TestScheduleConfig:
    def test_defaults_to_tue_and_fri(self, monkeypatch):
        for var in ("SCHEDULE_DAYS", "SCHEDULE_DAY_OF_WEEK"):
            monkeypatch.delenv(var, raising=False)
        assert load_config().schedule_days == ("tue", "fri")

    def test_parses_list(self, monkeypatch):
        monkeypatch.setenv("SCHEDULE_DAYS", "Mon, Thursday ,fri")
        assert load_config().schedule_days == ("mon", "thu", "fri")

    def test_legacy_single_day_still_honoured(self, monkeypatch):
        monkeypatch.delenv("SCHEDULE_DAYS", raising=False)
        monkeypatch.setenv("SCHEDULE_DAY_OF_WEEK", "wed")
        assert load_config().schedule_days == ("wed",)

    def test_cron_and_label(self):
        cfg = Config(schedule_days=("tue", "fri"), schedule_hour=15, schedule_minute=0)
        assert cfg.cron_day_of_week == "tue,fri"
        assert cfg.schedule_label == "Tuesday & Friday at 3:00 PM"
