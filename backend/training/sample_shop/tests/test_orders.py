"""End-to-end checkout: every module takes part, so a bug anywhere shows up here."""
from datetime import date
from decimal import Decimal

import pytest

from cart import cart_lines
from invoices import render
from orders import checkout


def test_checkout_with_coupon_and_free_shipping(catalog, coupons):
    order = checkout({"APL": 3, "NOV": 1, "HDP": 1}, catalog, "domestic", date(2026, 3, 5), coupons)
    assert order == {
        "subtotal": Decimal("79.37"),
        "coupon": "SPRING10",
        "discount": Decimal("7.94"),
        "tax": Decimal("11.47"),
        "shipping": Decimal("0.00"),
        "total": Decimal("82.90"),
        "delivery": date(2026, 3, 10),
        "points": 82,
    }


def test_checkout_abroad_without_coupon(catalog):
    order = checkout({"KTL": 1, "BRD": 2}, catalog, "eu", date(2026, 3, 19))
    assert order["subtotal"] == Decimal("42.30")
    assert order["tax"] == Decimal("7.11")
    assert order["shipping"] == Decimal("14.90")
    assert order["total"] == Decimal("64.31")
    assert order["delivery"] == date(2026, 3, 30)
    assert order["points"] == 64


def test_free_shipping_uses_the_discounted_subtotal(catalog, coupons):
    order = checkout({"NOV": 4, "BRD": 1}, catalog, "domestic", date(2026, 6, 10), coupons)
    assert order["subtotal"] == Decimal("51.90")
    assert order["coupon"] == "FIVEOFF"
    assert order["shipping"] == Decimal("6.10")
    assert order["tax"] == Decimal("3.28")
    assert order["total"] == Decimal("56.28")
    assert order["delivery"] == date(2026, 6, 15)


def test_gold_member_checkout(catalog):
    order = checkout({"KTL": 1, "BRD": 2}, catalog, "eu", date(2026, 3, 19), loyalty_points=2500)
    assert order["points"] == 128


def test_checkout_rejects_an_empty_cart(catalog):
    with pytest.raises(ValueError):
        checkout({}, catalog, "domestic", date(2026, 3, 5))


def test_invoice_for_a_checkout(catalog):
    cart = {"KTL": 1, "BRD": 2}
    order = checkout(cart, catalog, "eu", date(2026, 3, 19))
    text = render(order, cart_lines(cart, catalog), "INV-2026-00001")
    assert "Shipping: 14.90 EUR" in text
    assert text.endswith("Total: 64.31 EUR")
    assert "Discount" not in text
