"""Scraper tests that never touch the network."""

import requests

from jwbot.config import Config
from jwbot.products import PRODUCTS, PRODUCTS_BY_KEY
from jwbot.scrapers import get_scrapers, registry
from jwbot.scrapers.base import BaseScraper, ScrapeFailure


def make_config(**kwargs):
    base = dict(use_playwright=False, max_retries=1, http_timeout=1.0)
    base.update(kwargs)
    return Config(**base)


class TestRegistry:
    def test_every_product_registered_for_both_retailers(self):
        keys = set(registry())
        expected = {
            f"{retailer}:{spec.key}"
            for retailer in ("liquorland", "bws")
            for spec in PRODUCTS
        }
        assert expected <= keys
        assert len(keys) == 2 * len(PRODUCTS)

    def test_retailer_filter_selects_every_product(self):
        scrapers = get_scrapers(make_config(enabled_retailers=("bws",)))
        assert len(scrapers) == len(PRODUCTS)
        assert all(s.key.startswith("bws:") for s in scrapers)

    def test_product_filter_selects_every_retailer(self):
        scrapers = get_scrapers(make_config(enabled_retailers=("jw-black-700",)))
        assert sorted(s.key for s in scrapers) == [
            "bws:jw-black-700",
            "liquorland:jw-black-700",
        ]

    def test_exact_key_filter(self):
        scrapers = get_scrapers(make_config(enabled_retailers=("bws:jw-blue-700",)))
        assert [s.key for s in scrapers] == ["bws:jw-blue-700"]

    def test_all_when_unset(self):
        assert len(get_scrapers(make_config())) == 2 * len(PRODUCTS)


class TestProductSpecMatching:
    def test_size_discrimination(self):
        p700 = PRODUCTS_BY_KEY["jw-black-700"]
        p1l = PRODUCTS_BY_KEY["jw-black-1l"]
        name_700 = "Johnnie Walker Black Label Scotch Whisky 700mL"
        name_1l = "Johnnie Walker Black Label Scotch Whisky 1L"
        assert p700.matches_name(name_700)
        assert not p700.matches_name(name_1l)
        assert p1l.matches_name(name_1l)
        assert not p1l.matches_name(name_700)

    def test_variant_exclusions(self):
        p700 = PRODUCTS_BY_KEY["jw-black-700"]
        assert not p700.matches_name("Johnnie Walker Double Black Whisky 700mL")
        assert not p700.matches_name("Johnnie Walker Black Label 700mL 2x Glasses Gift Pack")
        blue = PRODUCTS_BY_KEY["jw-blue-700"]
        assert blue.matches_name("Johnnie Walker Blue Label Blended Scotch Whisky 700mL")
        assert not blue.matches_name("Johnnie Walker Blue Label King George V 700mL")

    def test_ballantines_variants(self):
        finest = PRODUCTS_BY_KEY["ballantines-finest-700"]
        twelve = PRODUCTS_BY_KEY["ballantines-12-700"]
        assert finest.matches_name("Ballantine's Finest Blended Scotch Whisky 700mL")
        assert not finest.matches_name("Ballantine's Finest Scotch Whisky 1 Litre")
        assert twelve.matches_name("Ballantine's 12 Year Old Blended Scotch Whisky 700mL")
        assert not twelve.matches_name("Ballantine's Finest Blended Scotch Whisky 700mL")

    def test_slug_matching(self):
        p1l = PRODUCTS_BY_KEY["jw-black-1l"]
        assert p1l.matches_name("johnnie-walker-black-label-scotch-whisky-1l_12345")


class TestFetchNeverRaises:
    def test_network_failure_becomes_result(self, monkeypatch):
        scraper = registry()["bws:jw-black-700"](make_config())

        def boom(*a, **kw):
            raise requests.ConnectionError("dns failure")

        monkeypatch.setattr(scraper.__class__, "strategy_api_product", lambda self: boom())
        monkeypatch.setattr(scraper.__class__, "strategy_api_search", lambda self: boom())
        monkeypatch.setattr(scraper.__class__, "strategy_static_html", lambda self: boom())

        result = scraper.fetch()
        assert result.price is None
        assert result.error is not None
        assert result.ok is False
        assert result.retailer == "bws:jw-black-700"
        assert result.product_key == "jw-black-700"

    def test_unexpected_exception_is_caught(self, monkeypatch):
        scraper = registry()["liquorland:jw-black-700"](make_config())
        monkeypatch.setattr(
            scraper.__class__,
            "strategy_static_html",
            lambda self: (_ for _ in ()).throw(ValueError("weird")),
        )
        result = scraper.fetch()
        assert result.error and "ValueError" in result.error

    def test_urlless_product_without_playwright_fails_loudly(self):
        scraper = registry()["liquorland:jw-blue-700"](make_config())
        result = scraper.fetch()
        assert result.error
        assert result.product_key == "jw-blue-700"

    def test_first_success_wins(self, monkeypatch):
        scraper = registry()["bws:jw-black-700"](make_config())
        monkeypatch.setattr(
            scraper.__class__,
            "strategy_api_product",
            lambda self: self.make_result(55.0, True, "JW Black 700mL", "api-product"),
        )
        result = scraper.fetch()
        assert result.price == 55.0
        assert result.ok is True
        assert result.duration_s is not None
        assert result.product_label == "Johnnie Walker Black Label 700mL"


class TestBWSPayloadParsing:
    def test_from_api_payload(self):
        scraper = registry()["bws:jw-black-700"](make_config())
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
        scraper = registry()["bws:jw-black-700"](make_config())
        payload = {
            "Products": [
                {"Stockcode": 73605, "Name": "Johnnie Walker Black Label Whisky 700mL 2x Glasses Gift Pack"},
                {"Stockcode": 707820, "Name": "Johnnie Walker Double Black Blended Scotch Whisky 700mL"},
                {"Stockcode": 9067, "Name": "Johnnie Walker Black Label Scotch Whisky 700mL"},
            ]
        }
        match = scraper._best_search_match(payload)
        assert match["Stockcode"] == 9067

    def test_search_match_picks_right_size_without_stockcode(self):
        scraper = registry()["bws:jw-black-1l"](make_config())
        payload = {
            "Products": [
                {"Stockcode": 9067, "Name": "Johnnie Walker Black Label Scotch Whisky 700mL"},
                {"Stockcode": 233450, "Name": "Johnnie Walker Black Label Scotch Whisky 1L"},
            ]
        }
        match = scraper._best_search_match(payload)
        assert match["Stockcode"] == 233450

    def test_search_match_separates_ballantines_expressions(self):
        scraper = registry()["bws:ballantines-12-700"](make_config())
        payload = {
            "Products": [
                {"Stockcode": 1111, "Name": "Ballantine's Finest Blended Scotch Whisky 700mL"},
                {"Stockcode": 2222, "Name": "Ballantine's 12 Year Old Blended Scotch Whisky 700mL"},
            ]
        }
        match = scraper._best_search_match(payload)
        assert match["Stockcode"] == 2222

    def test_no_price_raises_scrape_failure(self):
        scraper = registry()["bws:jw-black-700"](make_config())
        try:
            scraper._from_api_payload({"Products": []}, "api-product")
        except ScrapeFailure:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected ScrapeFailure")

    def test_candidate_names_skip_facet_objects(self):
        scraper = registry()["bws:ballantines-finest-700"](make_config())
        payload = {
            "Products": [{"Stockcode": 5, "Name": "Real Product 700ml"}],
            "Facets": [{"Name": "productname"}, {"name": "categoryleafnodeid"}],
        }
        assert scraper._candidate_names(payload) == ["Real Product 700ml"]

    def test_search_with_candidates_but_no_match_is_not_listed(self, monkeypatch):
        """BWS answering with other products means 'not stocked', not an error."""
        import jwbot.scrapers.bws as bws_mod

        scraper = registry()["bws:ballantines-finest-700"](make_config())
        monkeypatch.setattr(
            bws_mod,
            "get_json",
            lambda *a, **kw: {"Products": [{"Stockcode": 1, "Name": "Ballantine's Scotch Whisky 500ml"}]},
        )
        monkeypatch.setattr(scraper.__class__, "prime_session", lambda self, s: None)
        result = scraper.fetch()
        assert result.error is None
        assert result.available is False
        assert result.price is None
        assert result.strategy == "api-search:not-listed"


class TestLiquorlandLinkPicking:
    # Mixed absolute/relative hrefs, different category segments, plus
    # non-product links that must be ignored (no _sku suffix).
    HTML = """
    <a href="https://www.liquorland.com.au/spirits/johnnie-walker-black-label-12yo-scotch-whisky-700ml_30663">x</a>
    <a href="/whisky/johnnie-walker-black-label-scotch-whisky-1l_30664">x</a>
    <a href="/spirits/johnnie-walker-double-black-scotch-whisky-700ml_31000">x</a>
    <a href="/spirits/johnnie-walker-black-label-700ml-gift-pack_31234">x</a>
    <a href="/spirits/whisky">category</a>
    <a href="https://example.com/other/thing_999">offsite</a>
    """

    def test_picks_exact_size(self):
        scraper = registry()["liquorland:jw-black-1l"](make_config())
        assert scraper._pick_product_link(self.HTML) == (
            "/whisky/johnnie-walker-black-label-scotch-whisky-1l_30664"
        )

    def test_skips_variants_and_bundles(self):
        scraper = registry()["liquorland:jw-black-700"](make_config())
        assert scraper._pick_product_link(self.HTML) == (
            "/spirits/johnnie-walker-black-label-12yo-scotch-whisky-700ml_30663"
        )

    def test_none_when_absent(self):
        scraper = registry()["liquorland:jw-blue-700"](make_config())
        assert scraper._pick_product_link(self.HTML) is None


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
