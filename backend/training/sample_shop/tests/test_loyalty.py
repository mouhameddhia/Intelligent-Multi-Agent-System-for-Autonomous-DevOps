from decimal import Decimal

import pytest

from loyalty import points_earned, redeem, tier_for


def test_tier_thresholds():
    assert tier_for(0) == "bronze"
    assert tier_for(499) == "bronze"
    assert tier_for(500) == "silver"
    assert tier_for(1999) == "silver"
    assert tier_for(2000) == "gold"
    assert tier_for(5000) == "gold"


def test_points_are_whole_euros():
    assert points_earned(Decimal("75.99"), 0) == 75
    assert points_earned(Decimal("75.99"), 500) == 75
    assert points_earned(Decimal("75.99"), 1990) == 75  # the tier is the one before this order


def test_gold_members_earn_double():
    assert points_earned(Decimal("75.99"), 2000) == 150


def test_redeem_in_whole_blocks():
    assert redeem(350, 250) == (150, 10)
    assert redeem(350, 99) == (350, 0)
    assert redeem(100, 100) == (0, 5)


def test_redeem_needs_enough_points():
    with pytest.raises(ValueError):
        redeem(80, 100)
