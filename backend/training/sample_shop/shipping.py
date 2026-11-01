"""Shipping cost and delivery estimate."""
import math
from decimal import Decimal

from dates import add_business_days
from money import to_money

# zone -> (base fee, fee per started kg above INCLUDED_KG, transit business days)
ZONES = {
    "domestic": ("4.90", "1.20", 2),
    "eu": ("9.90", "2.50", 5),
    "world": ("19.90", "5.00", 10),
}
FREE_SHIPPING_FROM = {"domestic": Decimal("50.00"), "eu": Decimal("120.00")}
INCLUDED_KG = 2


def shipping_cost(zone, weight_kg, subtotal):
    """Free from the zone's threshold (subtotal after discount, before VAT);
    otherwise the base fee plus a fee for each started kg above INCLUDED_KG."""
    if zone not in ZONES:
        raise ValueError(f"unknown zone {zone}")
    threshold = FREE_SHIPPING_FROM.get(zone)
    if threshold is not None and subtotal >= threshold:
        return to_money(0)
    base, per_kg, _ = ZONES[zone]
    extra_kg = max(0, math.ceil(weight_kg - INCLUDED_KG))
    return to_money(Decimal(base) + Decimal(per_kg) * extra_kg)


def delivery_date(zone, order_day):
    """Orders ship on the next business day, then spend the zone's transit days."""
    _, _, transit_days = ZONES[zone]
    return add_business_days(order_day, transit_days + 1)
