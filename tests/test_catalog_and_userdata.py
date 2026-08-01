"""Tests for /addbottle parsing, the custom catalog file, targets and version."""

import pytest

from jwbot import products as catalog
from jwbot.models import PriceResult
from jwbot.products import (
    BUILTIN_PRODUCTS,
    load_custom_specs,
    resolve_product,
    save_custom_specs,
    spec_from_text,
    spec_to_custom_dict,
)
from jwbot.scrapers import register_product, registry, unregister_product
from jwbot.userdata import load_targets, save_targets, target_hits


class TestSpecFromText:
    def test_700ml(self):
        spec = spec_from_text("Johnnie Walker Red Label 700ml")
        assert spec.key == "johnnie-walker-red-label-700"
        assert spec.label == "Johnnie Walker Red Label 700mL"
        assert spec.name_tokens == ("johnnie", "walker", "red", "label")
        assert spec.size_tokens == ("700",)
        assert spec.brief is True
        assert spec.matches_name("Johnnie Walker Red Label Scotch Whisky 700mL")
        assert not spec.matches_name("Johnnie Walker Red Label Scotch Whisky 1L")

    def test_one_litre_variants(self):
        for text in ("Chivas Regal 1 litre", "chivas regal 1L", "Chivas Regal 1000ml"):
            spec = spec_from_text(text)
            assert spec.key == "chivas-regal-1l"
            assert spec.label == "Chivas Regal 1 Litre"
            assert spec.matches_name("Chivas Regal Blended Scotch Whisky 1L")
            assert not spec.matches_name("Chivas Regal Blended Scotch Whisky 700mL")

    def test_other_ml_sizes(self):
        spec = spec_from_text("Jameson 375ml")
        assert spec.size_tokens == ("375",)
        assert spec.label == "Jameson 375mL"
        assert spec.matches_name("Jameson Irish Whiskey 375mL")

    def test_generic_words_dropped_from_tokens(self):
        spec = spec_from_text("Ballantine's 12 Year Old Blended Scotch Whisky 700ml")
        assert spec.name_tokens == ("ballantine", "12")
        assert spec.matches_name("Ballantine's 12 Year Old Blended Scotch Whisky 700mL")

    def test_missing_size_raises(self):
        with pytest.raises(ValueError):
            spec_from_text("Johnnie Walker Red Label")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            spec_from_text("   ")

    def test_bare_700(self):
        spec = spec_from_text("glenfiddich 12 700")
        assert spec.size_tokens == ("700",)
        assert spec.name_tokens == ("glenfiddich", "12")


class TestCustomCatalogFile:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "products-custom.json"
        spec = spec_from_text("Johnnie Walker Red Label 700ml")
        save_custom_specs((spec,), path)
        loaded = load_custom_specs(path)
        assert len(loaded) == 1
        assert loaded[0] == spec

    def test_missing_file_is_empty(self, tmp_path):
        assert load_custom_specs(tmp_path / "nope.json") == ()

    def test_broken_file_is_empty(self, tmp_path):
        path = tmp_path / "products-custom.json"
        path.write_text("{broken", encoding="utf-8")
        assert load_custom_specs(path) == ()

    def test_invalid_and_duplicate_entries_skipped(self, tmp_path):
        import json

        path = tmp_path / "products-custom.json"
        good = spec_to_custom_dict(spec_from_text("Jameson 700ml"))
        clash = spec_to_custom_dict(spec_from_text("Jameson 700ml"))
        builtin_clash = dict(good, key=BUILTIN_PRODUCTS[0].key)
        path.write_text(
            json.dumps({"products": [good, clash, builtin_clash, {"key": "broken-only"}]}),
            encoding="utf-8",
        )
        loaded = load_custom_specs(path)
        assert [s.key for s in loaded] == ["jameson-700"]


class TestRuntimeRegistration:
    def test_add_then_remove(self):
        spec = spec_from_text("Test Runtime Bottle 700ml")
        assert spec.key not in catalog.PRODUCTS_BY_KEY
        try:
            catalog.add_spec_runtime(spec)
            register_product(spec)
            assert spec.key in catalog.PRODUCTS_BY_KEY
            assert f"liquorland:{spec.key}" in registry()
            assert f"bws:{spec.key}" in registry()
        finally:
            catalog.remove_spec_runtime(spec.key)
            unregister_product(spec.key)
        assert spec.key not in catalog.PRODUCTS_BY_KEY
        assert f"bws:{spec.key}" not in registry()

    def test_duplicate_add_raises(self):
        spec = BUILTIN_PRODUCTS[0]
        with pytest.raises(ValueError):
            catalog.add_spec_runtime(spec)


class TestResolveProduct:
    def test_exact_key(self):
        spec, _ = resolve_product("jw-black-700")
        assert spec is not None and spec.key == "jw-black-700"

    def test_words_unique(self):
        spec, _ = resolve_product("black label 1l")
        assert spec is not None and spec.key == "jw-black-1l"
        spec, _ = resolve_product("blue")
        assert spec is not None and spec.key == "jw-blue-700"

    def test_apostrophe_insensitive(self):
        spec, _ = resolve_product("ballantines 12")
        assert spec is not None and spec.key == "ballantines-12-700"

    def test_ambiguous_returns_candidates(self):
        spec, candidates = resolve_product("black label")
        assert spec is None
        assert {c.key for c in candidates} == {"jw-black-700", "jw-black-1l"}

    def test_no_match(self):
        spec, candidates = resolve_product("laphroaig quarter cask")
        assert spec is None and candidates == []


class TestTargets:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "targets.json"
        save_targets(path, {"jw-black-1l": 80, "jw-blue-700": 300.5})
        assert load_targets(path) == {"jw-black-1l": 80.0, "jw-blue-700": 300.5}

    def test_missing_and_broken(self, tmp_path):
        assert load_targets(tmp_path / "none.json") == {}
        bad = tmp_path / "targets.json"
        bad.write_text("[]", encoding="utf-8")
        assert load_targets(bad) == {}

    def test_non_numeric_values_skipped(self, tmp_path):
        import json

        path = tmp_path / "targets.json"
        path.write_text(json.dumps({"targets": {"a": "cheap", "b": 12}}), encoding="utf-8")
        assert load_targets(path) == {"b": 12.0}

    def test_target_hits(self):
        results = [
            PriceResult(
                retailer="bws:jw-black-1l",
                display_name="BWS",
                product_key="jw-black-1l",
                price=79.0,
                available=True,
            ),
            PriceResult(
                retailer="liquorland:jw-black-1l",
                display_name="Liquorland",
                product_key="jw-black-1l",
                price=95.0,
                available=True,
            ),
            PriceResult(
                retailer="bws:jw-blue-700",
                display_name="BWS",
                product_key="jw-blue-700",
                price=326.0,
                available=True,
            ),
        ]
        hits = target_hits(results, {"jw-black-1l": 80.0, "jw-blue-700": 300.0})
        assert [(r.retailer, t) for r, t in hits] == [("bws:jw-black-1l", 80.0)]

    def test_errored_result_never_hits(self):
        broken = PriceResult(
            retailer="bws:jw-black-1l",
            display_name="BWS",
            product_key="jw-black-1l",
            price=1.0,
            available=True,
            error="weird partial state",
        )
        assert target_hits([broken], {"jw-black-1l": 80.0}) == []


class TestOnSpecialDetection:
    @property
    def cls(self):
        return registry()["bws:jw-black-700"]

    def test_flag_true(self):
        payload = {"Products": [{"Stockcode": 9067, "IsOnSpecial": True}]}
        assert self.cls._looks_on_special(payload) is True

    def test_flag_false(self):
        payload = {"Products": [{"Stockcode": 9067, "IsOnSpecial": False}]}
        assert self.cls._looks_on_special(payload) is False

    def test_absent_is_none(self):
        payload = {"Products": [{"Stockcode": 9067, "Name": "x"}]}
        assert self.cls._looks_on_special(payload) is None

    def test_promo_price_value(self):
        payload = {"Prices": {"promoprice": {"Value": 49.0}}}
        assert self.cls._looks_on_special(payload) is True
