from datetime import date

from dates import add_business_days, is_business_day, is_within, parse_date


def test_parse_date():
    assert parse_date("2026-03-05") == date(2026, 3, 5)
    assert parse_date("2026-12-25") == date(2026, 12, 25)


def test_is_business_day():
    assert is_business_day(date(2026, 3, 5))       # Thursday
    assert not is_business_day(date(2026, 3, 7))   # Saturday
    assert not is_business_day(date(2026, 3, 8))   # Sunday
    assert not is_business_day(date(2026, 3, 20))  # public holiday (a Friday)


def test_add_business_days_skips_weekends():
    assert add_business_days(date(2026, 3, 5), 1) == date(2026, 3, 6)
    assert add_business_days(date(2026, 3, 6), 1) == date(2026, 3, 9)
    assert add_business_days(date(2026, 3, 5), 5) == date(2026, 3, 12)


def test_add_business_days_skips_holidays():
    assert add_business_days(date(2026, 3, 19), 1) == date(2026, 3, 23)


def test_add_zero_business_days():
    assert add_business_days(date(2026, 3, 7), 0) == date(2026, 3, 7)


def test_is_within_includes_both_ends():
    start, end = date(2026, 3, 1), date(2026, 3, 31)
    assert is_within(start, start, end)
    assert is_within(end, start, end)
    assert is_within(date(2026, 3, 15), start, end)
    assert not is_within(date(2026, 2, 28), start, end)
    assert not is_within(date(2026, 4, 1), start, end)
