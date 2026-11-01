from datetime import date
from decimal import Decimal

import pytest

from cart import cart_lines
from discounts import best_discount, coupon_discount, is_valid

MARCH = date(2026, 3, 15)
APRIL = date(2026, 4, 15)


def by_code(coupons, code):
    return next(coupon for coupon in coupons if coupon["code"] == code)


def test_is_valid_on_boundary_days(coupons):
    spring = by_code(coupons, "SPRING10")
    assert is_valid(spring, Decimal("40.00"), date(2026, 3, 1))
    assert is_valid(spring, Decimal("40.00"), date(2026, 3, 31))
    assert not is_valid(spring, Decimal("40.00"), date(2026, 4, 1))


def test_is_valid_minimum_subtotal_is_inclusive(coupons):
    spring = by_code(coupons, "SPRING10")
    assert is_valid(spring, Decimal("30.00"), MARCH)
    assert not is_valid(spring, Decimal("29.99"), MARCH)


def test_percent_coupon(catalog, coupons):
    lines = cart_lines({"APL": 3, "BRD": 2, "HDP": 1}, catalog)
    assert coupon_discount(by_code(coupons, "SPRING10"), lines, Decimal("75.17")) == Decimal("7.52")


def test_fixed_coupon_is_capped_at_subtotal(catalog, coupons):
    five = by_code(coupons, "FIVEOFF")
    assert coupon_discount(five, cart_lines({"NOV": 1}, catalog), Decimal("12.00")) == Decimal("5.00")
    assert coupon_discount(five, cart_lines({"BRD": 1}, catalog), Decimal("3.90")) == Decimal("3.90")


def test_bulk_coupon_needs_minimum_quantity(catalog, coupons):
    apples = by_code(coupons, "APPLES")
    assert coupon_discount(apples, cart_lines({"APL": 3}, catalog), Decimal("7.47")) == Decimal("3.74")
    assert coupon_discount(apples, cart_lines({"APL": 2}, catalog), Decimal("4.98")) == Decimal("0.00")
    assert coupon_discount(apples, cart_lines({"BRD": 5}, catalog), Decimal("19.50")) == Decimal("0.00")
    mixed = cart_lines({"APL": 3, "NOV": 1}, catalog)
    assert coupon_discount(apples, mixed, Decimal("19.47")) == Decimal("3.74")


def test_unknown_coupon_kind(catalog):
    with pytest.raises(ValueError):
        coupon_discount({"kind": "bogus"}, [], Decimal("10.00"))


def test_best_discount_picks_the_largest(catalog, coupons):
    lines = cart_lines({"APL": 3, "BRD": 2, "HDP": 1}, catalog)
    assert best_discount(coupons, lines, Decimal("75.17"), MARCH) == ("SPRING10", Decimal("7.52"))
    assert best_discount(coupons, lines, Decimal("75.17"), APRIL) == ("FIVEOFF", Decimal("5.00"))


def test_best_discount_without_coupons(catalog):
    assert best_discount([], cart_lines({"NOV": 1}, catalog), Decimal("12.00"), MARCH) == (None, Decimal("0.00"))


def test_best_discount_keeps_the_first_of_equal_coupons(catalog):
    year = {"valid_from": date(2026, 1, 1), "valid_to": date(2026, 12, 31)}
    coupons = [{"code": "A", "kind": "fixed", "value": "2.00", **year},
               {"code": "B", "kind": "fixed", "value": "2.00", **year}]
    assert best_discount(coupons, cart_lines({"NOV": 1}, catalog), Decimal("12.00"), MARCH)[0] == "A"
