from scheduling import free_slots, overlaps, to_minutes


def test_to_minutes():
    assert to_minutes("09:30") == 570
    assert to_minutes("00:05") == 5


def test_overlaps():
    assert overlaps(60, 120, 90, 150)
    assert not overlaps(60, 120, 120, 180)
    assert not overlaps(120, 180, 60, 120)


def test_free_slots_between_meetings():
    busy = [(600, 660), (540, 570)]
    assert free_slots(busy, 480, 720) == [(480, 540), (570, 600), (660, 720)]


def test_free_slots_respects_min_length():
    busy = [(500, 600)]
    assert free_slots(busy, 480, 630) == [(600, 630)]
    assert free_slots(busy, 480, 629) == []


def test_free_slots_merges_overlapping_meetings():
    busy = [(500, 560), (520, 540)]
    assert free_slots(busy, 500, 600) == [(560, 600)]
