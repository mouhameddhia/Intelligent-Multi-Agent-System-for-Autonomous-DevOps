"""Coupons. A coupon is a dict with code, kind ('percent', 'fixed' or 'bulk'),
value, valid_from and valid_to (dates), an optional min_subtotal, and for
'bulk' coupons the sku and min_quantity it applies to."""
from dates import is_within
from money import percent_of, to_money


def is_valid(coupon, subtotal, today):
    return (
        is_within(today, coupon["valid_from"], coupon["valid_to"])
        and subtotal >= coupon.get("min_subtotal", 0)
    )


def coupon_discount(coupon, cart_lines, subtotal):
    """Discount for one coupon, never more than the subtotal."""
    kind = coupon["kind"]
    if kind == "percent":
        amount = percent_of(subtotal, coupon["value"])
    elif kind == "fixed":
        amount = to_money(coupon["value"])
    elif kind == "bulk":
        amount = to_money(0)
        for product, quantity, line_total in cart_lines:
            if product["sku"] == coupon["sku"] and quantity >= coupon["min_quantity"]:
                amount = percent_of(line_total, coupon["value"])
    else:
        raise ValueError(f"unknown coupon kind {kind}")
    return min(amount, subtotal)


def best_discount(coupons, cart_lines, subtotal, today):
    """(code, amount) of the valid coupon with the largest discount, or
    (None, 0.00). Coupons do not stack."""
    best_code, best_amount = None, to_money(0)
    for coupon in coupons:
        if not is_valid(coupon, subtotal, today):
            continue
        amount = coupon_discount(coupon, cart_lines, subtotal)
        if amount > best_amount:
            best_code, best_amount = coupon["code"], amount
    return best_code, best_amount
