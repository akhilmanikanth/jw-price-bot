"""Scraper tests that never touch the network."""

import requests

from jwbot.config import Config
from jwbot.scrapers import get_scrapers, registry
from jwbot.scrapers.base import BaseScraper, ScrapeFailure


def make_config(**kwargs):
    base = dict(use_playwright=False, max_retries=1, http_timeout=1.0)
    base.update(kwargs)
    return Config(**base)


class TestRegistry:
    def test_built_ins_registered(self):
        keys = set(registry())
        assert {"liquorland", "bws"} <= keys

    def test_enabled_filter(self):
        scrapers = get_scrapers(make_config(enabled_retailers=("bws",)))
        assert [s.key for s in scrapers] == ["bws"]

    def test_all_when_unset(self):
        assert len(get_scrapers(make_config())) >= 2


class TestFetchNeverRaises:
    def test_network_failure_becomes_result(self, monkeypatch):
        scraper = registry()["bws"](make_config())

        def boom(*a, **kw):
            raise requests.ConnectionError("dns failure")

        monkeypatch.setattr(scraper.__class__, "strategy_api_product", lambda self: boom())
        monkeypatch.setattr(scraper.__class__, "strategy_api_search", lambda self: boom())
        monkeypatch.setattr(scraper.__class__, "strategy_static_html", lambda self: boom())

        result = scraper.fetch()
        assert result.price is None
        assert result.error is not None
        assert result.ok is False
        assert result.retailer == "bws"

    def test_unexpected_exception_is_caught(self, monkeypatch):
        scraper = registry()["liquorland"](make_config())
        monkeypatch.setattr(
            scraper.__class__,
            "strategy_static_html",
            lambda self: (_ for _ in ()).throw(ValueError("weird")),
        )
        result = scraper.fetch()
        assert result.error and "ValueError" in result.error

    def test_first_success_wins(self, monkeypatch):
        scraper = registry()["bws"](make_config())
        monkeypatch.setattr(
            scraper.__class__,
            "strategy_api_product",
            lambda self: self.make_result(55.0, True, "JW Black 700mL", "api-product"),
        )
        result = scraper.fetch()
        assert result.price == 55.0
        assert result.ok is True
        assert result.duration_s is not None


class TestBWSPayloadParsing:
    def test_from_api_payload(self):
        scraper = registry()["bws"](make_config())
        payload = {
            "Products": [
                {
                    "Products": [
                        {
                            "Stockcode": 9067,
                            "Name": "Johnnie Walker Black Label Scotch Whisky 700mL",
                            "Prices": {"singleprice": {"Value": 55.0}},
                            "IsAvailable": True,
                        }
                    ]
                }
            ]
        }
        result = scraper._from_api_payload(payload, "api-product")
        assert result.price == 55.0
        assert result.available is True
        assert "Johnnie Walker" in result.product_name

    def test_search_match_skips_gift_packs(self):
        scraper = registry()["bws"](make_config())
        payload = {
            "Products": [
                {"Stockcode": 73605, "Name": "Johnnie Walker Black Label Whisky 700mL 2x Glasses Gift Pack"},
                {"Stockcode": 707820, "Name": "Johnnie Walker Double Black Blended Scotch Whisky 700mL"},
                {"Stockcode": 9067, "Name": "Johnnie Walker Black Label Scotch Whisky 700mL"},
            ]
        }
        match = scraper._best_search_match(payload)
        assert match["Stockcode"] == 9067

    def test_no_price_raises_scrape_failure(self):
        scraper = registry()["bws"](make_config())
        try:
            scraper._from_api_payload({"Products": []}, "api-product")
        except ScrapeFailure:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected ScrapeFailure")


class TestExtensibility:
    def test_new_retailer_needs_only_a_subclass(self):
        html = """
        <html><head><script type="application/ld+json">
        {"@type":"Product","name":"JW Black 700mL",
         "offers":{"price":"58.00","availability":"InStock"}}
        </script></head><body></body></html>
        """

        class FakeShop(BaseScraper):
            key = "fakeshop"
            display_name = "Fake Shop"
            product_url = "https://example.com/jw-black-700ml"

        scraper = FakeShop(make_config())
        result = scraper._result_from_html(html, "static-html")
        assert result.price == 58.0
        assert result.display_name == "Fake Shop"
