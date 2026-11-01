from decimal import Decimal

import pytest

from cart import add_item, cart_lines, remove_item, subtotal, total_weight


def test_add_item_returns_a_new_cart(catalog):
    cart = {}
    new_cart = add_item(cart, catalog, "APL", 2)
    assert new_cart == {"APL": 2}
    assert cart == {}


def test_add_item_accumulates(catalog):
    cart = add_item({}, catalog, "APL")
    cart = add_item(cart, catalog, "APL", 3)
    assert cart == {"APL": 4}


def test_add_item_validates(catalog):
    with pytest.raises(ValueError):
        add_item({}, catalog, "APL", 0)
    with pytest.raises(KeyError):
        add_item({}, catalog, "XYZ")


def test_add_item_quantity_limit(catalog):
    cart = add_item({}, catalog, "APL", 99)
    assert cart == {"APL": 99}
    with pytest.raises(ValueError):
        add_item(cart, catalog, "APL", 1)


def test_remove_item(catalog):
    cart = {"APL": 3, "NOV": 1}
    assert remove_item(cart, "APL") == {"APL": 2, "NOV": 1}
    assert remove_item(cart, "APL", 3) == {"NOV": 1}
    assert remove_item(cart, "APL", 5) == {"NOV": 1}
    assert cart == {"APL": 3, "NOV": 1}
    with pytest.raises(KeyError):
        remove_item(cart, "HDP")


def test_cart_lines_sorted_by_sku(catalog):
    lines = cart_lines({"NOV": 1, "APL": 3}, catalog)
    assert [(product["sku"], quantity, total) for product, quantity, total in lines] == [
        ("APL", 3, Decimal("7.47")),
        ("NOV", 1, Decimal("12.00")),
    ]


def test_subtotal(catalog):
    assert subtotal({"APL": 3, "BRD": 2, "HDP": 1}, catalog) == Decimal("75.17")
    assert subtotal({}, catalog) == Decimal("0.00")


def test_total_weight(catalog):
    assert total_weight({"APL": 3, "KTL": 1}, catalog) == Decimal("4.6")
