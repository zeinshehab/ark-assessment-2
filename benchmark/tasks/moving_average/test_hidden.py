import pytest
from solution import moving_average


def test_basic():
    assert moving_average([1, 2, 3, 4], 2) == pytest.approx([1.5, 2.5, 3.5])


def test_window_equals_length():
    assert moving_average([2, 4, 6], 3) == pytest.approx([4.0])


def test_window_of_one():
    assert moving_average([1, 2, 3], 1) == pytest.approx([1.0, 2.0, 3.0])


def test_window_larger_than_list():
    assert moving_average([1, 2], 3) == []


def test_invalid_window():
    with pytest.raises(Exception):
        moving_average([1, 2, 3], 0)
