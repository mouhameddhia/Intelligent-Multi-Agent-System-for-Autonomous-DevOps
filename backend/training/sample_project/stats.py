"""Summary statistics for the metrics dashboard."""


def mean(values):
    if not values:
        raise ValueError("mean of an empty list")
    return sum(values) / len(values)


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    middle = n // 2
    if n % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def moving_average(values, window):
    if window < 1 or window > len(values):
        raise ValueError("invalid window")
    return [
        sum(values[i:i + window]) / window
        for i in range(len(values) - window + 1)
    ]


def clamp(value, low, high):
    return max(low, min(value, high))
