"""VAT. Catalogue prices exclude VAT; each category has its own rate in percent."""
from decimal import Decimal

from money import percent_of, to_money

RATES = {"food": 7, "books": 7, "electronics": 19, "clothing": 19}
DEFAULT_RATE = 19


def rate_for(category):
    return RATES.get(category, DEFAULT_RATE)


def taxes_by_rate(cart_lines, discount=0):
    """{rate: VAT}. A discount is spread over the lines in proportion to their
    totals before VAT is computed, so a coupon also lowers the VAT."""
    subtotal = sum(line_total for _, _, line_total in cart_lines)
    taxable = {}
    for product, _, line_total in cart_lines:
        share = line_total
        if discount and subtotal:
            share = line_total - Decimal(discount) * line_total / subtotal
        rate = rate_for(product["category"])
        taxable[rate] = taxable.get(rate, 0) + share
    return {rate: percent_of(amount, rate) for rate, amount in sorted(taxable.items())}


def total_tax(cart_lines, discount=0):
    return sum(taxes_by_rate(cart_lines, discount).values(), to_money(0))
