"""Calendar helpers for delivery estimates and coupon validity."""
from datetime import date, timedelta

HOLIDAYS = {date(2026, 1, 1), date(2026, 3, 20), date(2026, 5, 1), date(2026, 12, 25)}


def parse_date(text):
    """'2026-03-05' -> date(2026, 3, 5)."""
    year, month, day = (int(part) for part in text.split("-"))
    return date(year, month, day)


def is_business_day(day):
    """Monday to Friday, except public holidays."""
    return day.weekday() < 5 and day not in HOLIDAYS


def add_business_days(start, days):
    """The date `days` business days after start. Start itself is not counted."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if is_business_day(current):
            remaining -= 1
    return current


def is_within(day, start, end):
    """True when start <= day <= end: both ends are inclusive."""
    return start <= day <= end
