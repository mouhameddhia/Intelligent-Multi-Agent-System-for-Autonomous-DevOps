import pytest

from pricing import add_tax, apply_discount, bulk_price, split_bill


def test_apply_discount_reduces_price():
    assert apply_discount(80.0, 25) == 60.0


def test_apply_discount_bounds():
    assert apply_discount(50.0, 0) == 50.0
    assert apply_discount(50.0, 100) == 0.0


def test_apply_discount_rejects_invalid_percent():
    with pytest.raises(ValueError):
        apply_discount(10.0, -1)
    with pytest.raises(ValueError):
        apply_discount(10.0, 101)


def test_add_tax_default_rate():
    assert add_tax(100.0) == 120.0


def test_add_tax_custom_rate():
    assert add_tax(50.0, rate=0.1) == 55.0


def test_bulk_price_tiers():
    assert bulk_price(2.0, 9) == 18.0
    assert bulk_price(2.0, 10) == 18.0
    assert bulk_price(2.0, 49) == 88.2
    assert bulk_price(2.0, 50) == 80.0


def test_split_bill_even():
    assert split_bill(30.0, 3) == [10.0, 10.0, 10.0]


def test_split_bill_remainder_goes_to_first_payers():
    assert split_bill(10.0, 3) == [3.34, 3.33, 3.33]


def test_split_bill_needs_a_payer():
    with pytest.raises(ValueError):
        split_bill(10.0, 0)
