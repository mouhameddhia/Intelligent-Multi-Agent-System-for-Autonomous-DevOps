"""Loyalty points: one point per whole euro spent, doubled for gold members."""

TIERS = [(0, "bronze"), (500, "silver"), (2000, "gold")]
POINTS_PER_VOUCHER = 100
VOUCHER_VALUE = 5


def tier_for(points):
    """The highest tier whose threshold the points reach."""
    name = TIERS[0][1]
    for threshold, tier in TIERS:
        if points >= threshold:
            name = tier
    return name


def points_earned(total, current_points):
    points = int(total)
    if tier_for(current_points) == "gold":
        points *= 2
    return points


def redeem(points, wanted):
    """Redeem points in whole blocks of POINTS_PER_VOUCHER, each worth
    VOUCHER_VALUE euros. Returns (points_left, euros_off)."""
    if wanted > points:
        raise ValueError("not enough points")
    vouchers = wanted // POINTS_PER_VOUCHER
    return points - vouchers * POINTS_PER_VOUCHER, vouchers * VOUCHER_VALUE
