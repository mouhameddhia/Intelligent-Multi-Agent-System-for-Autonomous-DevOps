from decimal import Decimal

from cart import cart_lines
from tax import rate_for, taxes_by_rate, total_tax


def test_rate_for():
    assert rate_for("food") == 7
    assert rate_for("books") == 7
    assert rate_for("electronics") == 19
    assert rate_for("outdoor") == 19


def test_taxes_by_rate_without_discount(catalog):
    lines = cart_lines({"APL": 3, "NOV": 1, "HDP": 1}, catalog)
    taxes = taxes_by_rate(lines)
    assert taxes == {7: Decimal("1.36"), 19: Decimal("11.38")}
    assert list(taxes) == [7, 19]
    assert total_tax(lines) == Decimal("12.74")


def test_discount_lowers_vat_proportionally(catalog):
    lines = cart_lines({"APL": 3, "NOV": 1, "HDP": 1}, catalog)
    assert taxes_by_rate(lines, Decimal("7.94")) == {7: Decimal("1.23"), 19: Decimal("10.24")}
    assert total_tax(lines, Decimal("7.94")) == Decimal("11.47")


def test_half_cent_vat_rounds_up(catalog):
    lines = cart_lines({"KTL": 1}, catalog)
    assert total_tax(lines) == Decimal("6.56")


def test_no_lines_no_tax():
    assert taxes_by_rate([]) == {}
    assert total_tax([]) == Decimal("0.00")
