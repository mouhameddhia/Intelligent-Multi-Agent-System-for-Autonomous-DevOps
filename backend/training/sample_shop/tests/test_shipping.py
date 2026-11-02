from datetime import date
from decimal import Decimal

import pytest

from shipping import delivery_date, shipping_cost


def test_base_fee_covers_the_first_two_kg():
    assert shipping_cost("domestic", Decimal("1.5"), Decimal("20.00")) == Decimal("4.90")
    assert shipping_cost("domestic", Decimal("2.0"), Decimal("20.00")) == Decimal("4.90")


def test_each_started_kg_above_two_costs_extra():
    assert shipping_cost("domestic", Decimal("2.1"), Decimal("20.00")) == Decimal("6.10")
    assert shipping_cost("domestic", Decimal("4.6"), Decimal("20.00")) == Decimal("8.50")
    assert shipping_cost("eu", Decimal("3"), Decimal("119.99")) == Decimal("12.40")


def test_free_shipping_threshold_is_inclusive():
    assert shipping_cost("domestic", Decimal("1"), Decimal("50.00")) == Decimal("0.00")
    assert shipping_cost("domestic", Decimal("1"), Decimal("49.99")) == Decimal("4.90")
    assert shipping_cost("eu", Decimal("1"), Decimal("120.00")) == Decimal("0.00")


def test_world_shipping_is_never_free():
    assert shipping_cost("world", Decimal("1"), Decimal("1000.00")) == Decimal("19.90")


def test_unknown_zone():
    with pytest.raises(ValueError):
        shipping_cost("moon", Decimal("1"), Decimal("10.00"))


def test_delivery_date_counts_dispatch_and_transit_days():
    assert delivery_date("domestic", date(2026, 3, 5)) == date(2026, 3, 10)
    assert delivery_date("eu", date(2026, 3, 5)) == date(2026, 3, 13)


def test_delivery_date_skips_holidays():
    assert delivery_date("domestic", date(2026, 3, 19)) == date(2026, 3, 25)
