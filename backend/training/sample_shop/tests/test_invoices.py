from decimal import Decimal

import pytest

from cart import cart_lines
from invoices import invoice_number, next_sequence, render


def test_invoice_number_is_zero_padded():
    assert invoice_number(2026, 42) == "INV-2026-00042"
    assert invoice_number(2026, 1) == "INV-2026-00001"
    with pytest.raises(ValueError):
        invoice_number(2026, 0)


def test_next_sequence_restarts_each_year():
    issued = ["INV-2025-00007", "INV-2026-00003", "INV-2026-00011"]
    assert next_sequence(issued, 2026) == 12
    assert next_sequence(issued, 2025) == 8
    assert next_sequence(issued, 2027) == 1
    assert next_sequence([], 2026) == 1


def test_render_with_discount(catalog):
    lines = cart_lines({"APL": 3, "HDP": 1}, catalog)
    order = {"coupon": "SPRING10", "discount": Decimal("7.94"), "tax": Decimal("11.47"),
             "shipping": Decimal("0.00"), "total": Decimal("1082.90")}
    assert render(order, lines, "INV-2026-00042") == "\n".join([
        "Invoice INV-2026-00042",
        "3 x Apples 1kg: 7.47 EUR",
        "1 x Headphones: 59.90 EUR",
        "Discount (SPRING10): -7.94 EUR",
        "VAT: 11.47 EUR",
        "Shipping: 0.00 EUR",
        "Total: 1,082.90 EUR",
    ])


def test_render_without_discount(catalog):
    lines = cart_lines({"NOV": 1}, catalog)
    order = {"coupon": None, "discount": Decimal("0.00"), "tax": Decimal("0.84"),
             "shipping": Decimal("4.90"), "total": Decimal("17.74")}
    assert "Discount" not in render(order, lines, "INV-2026-00001")
