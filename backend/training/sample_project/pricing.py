"""Price calculations for the shop checkout."""

TAX_RATE = 0.2


def apply_discount(price, percent):
    """Return the price after a percentage discount (0-100)."""
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * (100 - percent) / 100, 2)


def add_tax(price, rate=TAX_RATE):
    """Return the price including tax."""
    return round(price * (1 + rate), 2)


def bulk_price(unit_price, quantity):
    """Total price: 10% off from 10 items, 20% off from 50 items."""
    if quantity >= 50:
        return round(unit_price * quantity * 0.8, 2)
    if quantity >= 10:
        return round(unit_price * quantity * 0.9, 2)
    return round(unit_price * quantity, 2)


def split_bill(total, people):
    """Split a bill evenly in cents; the first payers absorb the remainder."""
    if people < 1:
        raise ValueError("at least one person must pay")
    cents = round(total * 100)
    share, remainder = divmod(cents, people)
    return [(share + 1) / 100 if i < remainder else share / 100 for i in range(people)]
