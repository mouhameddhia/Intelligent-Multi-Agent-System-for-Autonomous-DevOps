from decimal import Decimal

import pytest

from catalog import find_product, in_category, parse_catalog


def test_parse_catalog_reads_products(catalog):
    assert len(catalog) == 7
    assert catalog["APL"]["name"] == "Apples 1kg"
    assert catalog["APL"]["price"] == Decimal("2.49")
    assert catalog["TNT"]["weight_kg"] == Decimal("4.7")
    assert catalog["HDP"]["category"] == "electronics"


def test_parse_catalog_strips_whitespace():
    catalog = parse_catalog([" APL , Apples 1kg , 2.49 , food , 1.0 "])
    assert catalog["APL"]["name"] == "Apples 1kg"
    assert catalog["APL"]["category"] == "food"


def test_parse_catalog_reports_the_bad_line():
    with pytest.raises(ValueError, match="line 2"):
        parse_catalog(["sku,name,price,category,weight_kg", "APL,Apples,2.49,food"])


def test_parse_catalog_rejects_duplicates():
    with pytest.raises(ValueError, match="duplicate sku APL"):
        parse_catalog(["APL,Apples,2.49,food,1.0", "APL,Green apples,2.99,food,1.0"])


def test_find_product(catalog):
    assert find_product(catalog, "NOV")["price"] == Decimal("12.00")
    with pytest.raises(KeyError, match="unknown sku XYZ"):
        find_product(catalog, "XYZ")


def test_in_category_cheapest_first(catalog):
    assert [p["sku"] for p in in_category(catalog, "electronics")] == ["KTL", "HDP"]
    assert [p["sku"] for p in in_category(catalog, "food")] == ["APL", "BRD"]
    assert in_category(catalog, "toys") == []


def test_in_category_ties_ordered_by_sku():
    catalog = parse_catalog(["PEN,Pen,1.00,office,0.1", "CLP,Clip,1.00,office,0.1", "INK,Ink,0.50,office,0.1"])
    assert [p["sku"] for p in in_category(catalog, "office")] == ["INK", "CLP", "PEN"]
