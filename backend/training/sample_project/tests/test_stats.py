import pytest

from stats import clamp, mean, median, moving_average


def test_mean():
    assert mean([1, 2, 3, 6]) == 3


def test_mean_rejects_empty():
    with pytest.raises(ValueError):
        mean([])


def test_median_odd_and_even():
    assert median([5, 1, 3]) == 3
    assert median([4, 1, 3, 2]) == 2.5


def test_moving_average():
    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]
    assert moving_average([2, 4], 2) == [3.0]


def test_moving_average_window_bounds():
    assert moving_average([7], 1) == [7.0]
    with pytest.raises(ValueError):
        moving_average([1, 2], 0)
    with pytest.raises(ValueError):
        moving_average([1, 2], 3)


def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-3, 0, 10) == 0
    assert clamp(42, 0, 10) == 10
