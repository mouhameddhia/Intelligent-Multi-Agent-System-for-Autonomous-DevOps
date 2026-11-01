"""Money helpers. Amounts are Decimals rounded to cents, half up, as on receipts."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_money(value):
    """Decimal rounded to cents. Accepts int, str or Decimal, never float."""
    if isinstance(value, float):
        raise TypeError("use str or Decimal for money, not float")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def percent_of(amount, percent):
    """percent % of amount, rounded to cents."""
    return to_money(Decimal(amount) * Decimal(percent) / 100)


def split_evenly(total, parts):
    """Split total into `parts` amounts that sum to total and differ by at most
    one cent. The first shares get the extra cents."""
    if parts <= 0:
        raise ValueError("parts must be positive")
    cents = int(to_money(total) * 100)
    share, extra = divmod(cents, parts)
    return [to_money(Decimal(share + (1 if i < extra else 0)) / 100) for i in range(parts)]


def format_money(amount, currency="EUR"):
    """'1,234.50 EUR'. Negative amounts get a leading minus."""
    amount = to_money(amount)
    sign = "-" if amount < 0 else ""
    return f"{sign}{abs(amount):,.2f} {currency}"
