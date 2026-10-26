"""Meeting-room scheduling. Times are minutes since midnight."""


def to_minutes(hhmm):
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def overlaps(start_a, end_a, start_b, end_b):
    """True when [start_a, end_a) and [start_b, end_b) share any time."""
    return start_a < end_b and start_b < end_a


def free_slots(busy, day_start, day_end, min_length=30):
    """Gaps of at least min_length minutes between busy (start, end) pairs."""
    slots = []
    cursor = day_start
    for start, end in sorted(busy):
        if start - cursor >= min_length:
            slots.append((cursor, start))
        cursor = max(cursor, end)
    if day_end - cursor >= min_length:
        slots.append((cursor, day_end))
    return slots
