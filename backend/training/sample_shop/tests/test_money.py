from decimal import Decimal

import pytest

from money import format_money, percent_of, split_evenly, to_money


def test_to_money_rounds_half_up():
    assert to_money("2.005") == Decimal("2.01")
    assert to_money("2.004") == Decimal("2.00")
    assert to_money("-1.005") == Decimal("-1.01")


def test_to_money_keeps_two_decimals():
    assert str(to_money(3)) == "3.00"
    assert str(to_money("0.5")) == "0.50"


def test_to_money_rejects_float():
    with pytest.raises(TypeError):
        to_money(0.1)


def test_percent_of_rounds_the_result():
    assert percent_of("19.99", 15) == Decimal("3.00")
    assert percent_of("0.10", 50) == Decimal("0.05")
    assert percent_of("33.33", 7) == Decimal("2.33")


def test_split_evenly_gives_extra_cents_to_first_shares():
    assert split_evenly("10.00", 3) == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
    assert split_evenly("0.02", 3) == [Decimal("0.01"), Decimal("0.01"), Decimal("0.00")]
    assert split_evenly("9.00", 3) == [Decimal("3.00")] * 3


def test_split_evenly_preserves_the_total():
    shares = split_evenly("100.01", 7)
    assert sum(shares) == Decimal("100.01")
    assert max(shares) - min(shares) == Decimal("0.01")


def test_split_evenly_rejects_no_parts():
    with pytest.raises(ValueError):
        split_evenly("10.00", 0)


def test_format_money():
    assert format_money("1234.5") == "1,234.50 EUR"
    assert format_money("1234567.891") == "1,234,567.89 EUR"
    assert format_money("0") == "0.00 EUR"
    assert format_money("-3.2", "USD") == "-3.20 USD"
