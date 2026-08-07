"""Bulk-offer parsing and bot-protection detection."""

from jwbot.config import Config
from jwbot.extract import find_multibuys, find_multibuys_in_html, looks_blocked
from jwbot.models import MultiBuy, PriceResult
from jwbot.scrapers import registry
from jwbot.scrapers.base import BotWallBlocked, ScrapeFailure


def make_config(**kwargs):
    base = dict(use_playwright=False, max_retries=1, http_timeout=1.0)
    base.update(kwargs)
    return Config(**base)


class TestMultiBuyParsing:
    def test_bws_style_price_message(self):
        """The real shape Alfred spotted: single $69, two for $110."""
        payload = {
            "Products": [
                {
                    "Stockcode": 9067,
                    "Name": "Johnnie Walker Black Label Scotch Whisky 700mL",
                    "Prices": {
                        "singleprice": {"Value": 69.0, "Message": "1 for $69.00"},
                        "promoprice": {"Value": 55.0, "Message": "2 for $110.00"},
                    },
                }
            ]
        }
        deals = find_multibuys(payload)
        assert deals == [(2, 110.0, "2 for $110.00")]

    def test_single_price_message_is_not_a_deal(self):
        payload = {"Prices": {"singleprice": {"Message": "1 for $69.00"}}}
        assert find_multibuys(payload) == []

    def test_each_when_you_buy_phrasing(self):
        payload = {"promoMessage": "$55.00 each when you buy 2"}
        deals = find_multibuys(payload)
        assert deals and deals[0][0] == 2 and deals[0][1] == 110.0

    def test_any_2_for_phrasing(self):
        payload = {"offerDescription": "Any 2 for $110"}
        assert find_multibuys(payload)[0][:2] == (2, 110.0)

    def test_buy_prefix(self):
        payload = {"promoText": "Buy 3 for $150.00"}
        assert find_multibuys(payload)[0][:2] == (3, 150.0)

    def test_cheapest_per_bottle_first(self):
        payload = {"promoA": {"Message": "2 for $120"}, "promoB": {"Message": "3 for $150"}}
        deals = find_multibuys(payload)
        assert deals[0][0] == 3  # $50 each beats $60 each

    def test_ignores_unrelated_numbers(self):
        payload = {"Name": "Ballantine's 12 Year Old 700mL", "description": "40% ABV"}
        assert find_multibuys(payload) == []

    def test_ignores_absurd_quantities(self):
        assert find_multibuys({"promoText": "50 for $2"}) == []

    def test_only_scans_offer_ish_keys(self):
        """A random field shouldn't invent a deal."""
        assert find_multibuys({"randomField": "2 for $110"}) == []

    def test_html_page_badge(self):
        html = (
            "<html><body><div class='promo'>2 for $110.00</div>"
            + "x" * 300
            + "</body></html>"
        )
        assert find_multibuys_in_html(html)[0][:2] == (2, 110.0)

    def test_html_embedded_json(self):
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type":"Product","name":"JW Black","offers":{"price":"69.00"},'
            '"promoDescription":"2 for $110.00"}'
            "</script></head><body>" + "x" * 300 + "</body></html>"
        )
        assert find_multibuys_in_html(html)[0][:2] == (2, 110.0)


class TestMultiBuyModel:
    def test_unit_price(self):
        assert MultiBuy(quantity=2, total_price=110.0).unit_price == 55.0
        assert MultiBuy(quantity=3, total_price=150.0).unit_price == 50.0

    def test_best_multibuy_requires_a_real_saving(self):
        result = PriceResult(retailer="bws:x", display_name="BWS", price=55.0, available=True)
        result.multibuy = [MultiBuy(quantity=2, total_price=110.0)]
        assert result.best_multibuy is None  # $55 each is not cheaper than $55

    def test_best_multibuy_picks_lowest_unit(self):
        result = PriceResult(retailer="bws:x", display_name="BWS", price=69.0, available=True)
        result.multibuy = [
            MultiBuy(quantity=2, total_price=130.0),
            MultiBuy(quantity=2, total_price=110.0),
        ]
        assert result.best_multibuy.total_price == 110.0

    def test_no_deals_is_none(self):
        result = PriceResult(retailer="bws:x", display_name="BWS", price=69.0, available=True)
        assert result.best_multibuy is None


class TestScraperDropsNonDeals:
    def test_make_result_filters_and_keeps(self):
        scraper = registry()["bws:jw-black-700"](make_config())
        result = scraper.make_result(
            price=69.0,
            available=True,
            multibuy=[
                MultiBuy(quantity=2, total_price=110.0),   # $55 each - keep
                MultiBuy(quantity=2, total_price=140.0),   # $70 each - drop
            ],
        )
        assert [d.total_price for d in result.multibuy] == [110.0]


class TestBotWallDetection:
    CAPTCHA = "<html><head><title>ShieldSquare Captcha</title></head><body>Please verify</body></html>"

    def test_detects_shieldsquare(self):
        assert looks_blocked(self.CAPTCHA)

    def test_detects_cloudflare(self):
        assert looks_blocked("<html><title>Attention Required! | Cloudflare</title></html>")

    def test_normal_page_is_not_blocked(self):
        html = "<html><body>" + "Johnnie Walker Black Label $61.00 " * 200 + "</body></html>"
        assert not looks_blocked(html)

    def test_big_page_mentioning_captcha_is_not_blocked(self):
        """A real page with a stray 'captcha' word must not be misread."""
        html = "<html><body>" + ("product listing " * 5000) + "captcha</body></html>"
        assert not looks_blocked(html)

    def test_empty_is_not_blocked(self):
        assert not looks_blocked("")

    def test_result_from_html_raises_blocked(self):
        scraper = registry()["liquorland:jw-black-700"](make_config())
        try:
            scraper._result_from_html(self.CAPTCHA + "x" * 300, "static-html")
        except BotWallBlocked:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected BotWallBlocked")

    def test_blocked_is_a_scrape_failure(self):
        """fetch() must keep treating it as a recoverable strategy failure."""
        assert issubclass(BotWallBlocked, ScrapeFailure)

    def test_fetch_marks_result_blocked(self, monkeypatch):
        scraper = registry()["liquorland:jw-black-700"](make_config())
        monkeypatch.setattr(
            scraper.__class__,
            "strategy_static_html",
            lambda self: (_ for _ in ()).throw(BotWallBlocked("captcha")),
        )
        result = scraper.fetch()
        assert result.blocked is True
        assert result.error and "bot protection" in result.error

    def test_ordinary_failure_is_not_blocked(self, monkeypatch):
        scraper = registry()["liquorland:jw-black-700"](make_config())
        monkeypatch.setattr(
            scraper.__class__,
            "strategy_static_html",
            lambda self: (_ for _ in ()).throw(ScrapeFailure("no price")),
        )
        result = scraper.fetch()
        assert result.blocked is False

    def test_empty_sitemap_counts_as_blocked(self, monkeypatch):
        import jwbot.scrapers.liquorland as ll_mod

        monkeypatch.setattr(ll_mod, "_sitemap_cache", {"paths": None, "error": None})
        monkeypatch.setattr(ll_mod, "get_text", lambda *a, **kw: "<sitemapindex></sitemapindex>")
        scraper = registry()["liquorland:jw-blue-700"](make_config())
        scraper.product_url = ""
        monkeypatch.setattr(scraper.__class__, "prime_session", lambda self, s: None)
        result = scraper.fetch()
        assert result.blocked is True
