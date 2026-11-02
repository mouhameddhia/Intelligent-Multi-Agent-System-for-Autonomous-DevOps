"""Shopping cart: a dict sku -> quantity. Functions return a new cart and never
change the one they are given."""
from catalog import find_product
from money import to_money

MAX_QUANTITY = 99


def add_item(cart, catalog, sku, quantity=1):
    """A new cart with `quantity` more of sku."""
    find_product(catalog, sku)
    if quantity < 1:
        raise ValueError("quantity must be at least 1")
    new_cart = dict(cart)
    new_cart[sku] = new_cart.get(sku, 0) + quantity
    if new_cart[sku] > MAX_QUANTITY:
        raise ValueError(f"at most {MAX_QUANTITY} of one item")
    return new_cart


def remove_item(cart, sku, quantity=1):
    """A new cart with `quantity` fewer of sku; the line disappears at zero."""
    if sku not in cart:
        raise KeyError(f"{sku} is not in the cart")
    new_cart = dict(cart)
    left = new_cart[sku] - quantity
    if left > 0:
        new_cart[sku] = left
    else:
        del new_cart[sku]
    return new_cart


def cart_lines(cart, catalog):
    """[(product, quantity, line_total)], sorted by sku."""
    lines = []
    for sku in sorted(cart):
        product = find_product(catalog, sku)
        quantity = cart[sku]
        lines.append((product, quantity, to_money(product["price"] * quantity)))
    return lines


def subtotal(cart, catalog):
    return sum((line_total for _, _, line_total in cart_lines(cart, catalog)), to_money(0))


def total_weight(cart, catalog):
    return sum(find_product(catalog, sku)["weight_kg"] * quantity for sku, quantity in cart.items())
