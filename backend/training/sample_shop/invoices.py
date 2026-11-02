"""Invoice numbering and plain-text rendering."""
from money import format_money


def invoice_number(year, sequence):
    """'INV-2026-00042'. Sequences restart at 1 every year."""
    if sequence < 1:
        raise ValueError("sequence starts at 1")
    return f"INV-{year}-{sequence:05d}"


def next_sequence(issued, year):
    """The next sequence number for `year`, given the invoice numbers issued so far."""
    used = [int(number.split("-")[2]) for number in issued if number.split("-")[1] == str(year)]
    return max(used, default=0) + 1


def render(order, lines, number):
    rows = [f"Invoice {number}"]
    for product, quantity, line_total in lines:
        rows.append(f"{quantity} x {product['name']}: {format_money(line_total)}")
    if order["discount"]:
        rows.append(f"Discount ({order['coupon']}): -{format_money(order['discount'])}")
    rows.append(f"VAT: {format_money(order['tax'])}")
    rows.append(f"Shipping: {format_money(order['shipping'])}")
    rows.append(f"Total: {format_money(order['total'])}")
    return "\n".join(rows)
