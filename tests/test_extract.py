from jwbot.extract import (
    extract_price_from_html,
    find_availability_in_json,
    find_price_in_json,
    is_plausible_price,
    looks_out_of_stock,
    parse_price,
)


class TestParsePrice:
    def test_dollar_string(self):
        assert parse_price("$55.00") == 55.0
        assert parse_price("  $ 65.99 ") == 65.99
        assert parse_price("$1,299.00") == 1299.0

    def test_numeric(self):
        assert parse_price(55) == 55.0
        assert parse_price(55.5) == 55.5

    def test_embedded(self):
        assert parse_price("Now $62.00 each") == 62.0
        assert parse_price("62.00 AUD") == 62.0

    def test_rejects_implausible(self):
        assert parse_price("12 Year Old") is None   # too low to be a bottle price
        assert parse_price(4.5) is None             # star rating
        assert parse_price(99999) is None
        assert parse_price("") is None
        assert parse_price(None) is None
        assert parse_price(True) is None

    def test_plausible_bounds(self):
        assert is_plausible_price(5.0)
        assert is_plausible_price(5000.0)
        assert not is_plausible_price(4.99)


class TestOutOfStock:
    def test_markers(self):
        assert looks_out_of_stock("This product is Out of Stock right now")
        assert looks_out_of_stock("SOLD OUT")
        assert not looks_out_of_stock("Add to cart")


class TestFindPriceInJson:
    def test_bws_shape(self):
        payload = {
            "Products": [
                {
                    "Products": [
                        {
                            "Stockcode": 9067,
                            "Name": "Johnnie Walker Black Label Scotch Whisky 700mL",
                            "Prices": {
                                "singleprice": {"Value": 55.0, "Message": "$55.00 each"},
                                "inanytwoprice": {"Value": 52.0},
                            },
                            "IsAvailable": True,
                        }
                    ]
                }
            ]
        }
        price, hint = find_price_in_json(payload, prefer_keys=("singleprice",))
        assert price == 55.0
        assert "singleprice" in hint
        assert find_availability_in_json(payload) is True

    def test_availability_string(self):
        assert find_availability_in_json({"availability": "https://schema.org/InStock"}) is True
        assert find_availability_in_json({"availability": "OutOfStock"}) is False
        assert find_availability_in_json({"foo": "bar"}) is None

    def test_generic_fallback(self):
        price, _ = find_price_in_json({"a": {"b": {"price": "$78.50"}}})
        assert price == 78.5

    def test_preferred_key_beats_earlier_generic_key(self):
        """A 'price' key higher in the tree must not shadow 'singleprice'."""
        payload = {
            "page": {"price": 999.0},  # e.g. a bundle/banner price appearing first
            "product": {"Prices": {"singleprice": {"Value": 55.0}}},
        }
        price, hint = find_price_in_json(payload, prefer_keys=("singleprice", "price"))
        assert price == 55.0
        assert "singleprice" in hint

    def test_lists_are_not_parsed_as_prices(self):
        price, _ = find_price_in_json({"price": [1, 2, 3], "sellprice": 61.0})
        assert price == 61.0


class TestExtractFromHtml:
    def test_json_ld(self):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Product",
         "name":"Johnnie Walker Black Label 700mL",
         "offers":{"@type":"Offer","price":"65.00","priceCurrency":"AUD",
                   "availability":"https://schema.org/InStock"}}
        </script></head><body><h1>Johnnie Walker Black Label 700mL</h1></body></html>
        """
        data = extract_price_from_html(html)
        assert data["price"] == 65.0
        assert data["available"] is True
        assert data["strategy"] == "json-ld"
        assert "Johnnie Walker" in data["product_name"]

    def test_selector(self):
        html = """
        <html><body><h1>Johnnie Walker Black Label 12YO 700mL</h1>
        <div data-testid="product-price">$63.99</div></body></html>
        """
        data = extract_price_from_html(html, selectors=('[data-testid="product-price"]',))
        assert data["price"] == 63.99
        assert data["available"] is True

    def test_next_data(self):
        html = """
        <html><body><h1>JW Black 700mL</h1>
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"product":{"name":"JW Black","currentPrice":61.5}}}}
        </script></body></html>
        """
        data = extract_price_from_html(html, prefer_json_keys=("currentprice",))
        assert data["price"] == 61.5

    def test_out_of_stock_detected(self):
        html = "<html><body><h1>JW Black 700mL</h1><p>Currently unavailable</p></body></html>" + "x" * 300
        data = extract_price_from_html(html)
        assert data["price"] is None
        assert data["available"] is False

    def test_meta_tag(self):
        html = """
        <html><head><meta property="product:price:amount" content="59.00"></head>
        <body><h1>JW Black</h1></body></html>
        """
        data = extract_price_from_html(html)
        assert data["price"] == 59.0
