"""Checkout: turns a cart into a priced order."""
from cart import cart_lines, subtotal, total_weight
from discounts import best_discount
from loyalty import points_earned
from money import to_money
from shipping import delivery_date, shipping_cost
from tax import total_tax


def checkout(cart, catalog, zone, today, coupons=(), loyalty_points=0):
    if not cart:
        raise ValueError("cart is empty")
    lines = cart_lines(cart, catalog)
    items = subtotal(cart, catalog)
    code, discount = best_discount(coupons, lines, items, today)
    discounted = items - discount
    tax = total_tax(lines, discount)
    shipping = shipping_cost(zone, total_weight(cart, catalog), discounted)
    total = to_money(discounted + tax + shipping)
    return {
        "subtotal": items,
        "coupon": code,
        "discount": discount,
        "tax": tax,
        "shipping": shipping,
        "total": total,
        "delivery": delivery_date(zone, today),
        "points": points_earned(total, loyalty_points),
    }
