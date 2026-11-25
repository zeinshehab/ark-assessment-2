import pytest
from solution import median


def test_odd():
    assert median([3, 1, 2]) == 2


def test_even():
    assert median([4, 1, 3, 2]) == 2.5


def test_single():
    assert median([7]) == 7


def test_empty_raises():
    with pytest.raises(Exception):
        median([])


def test_input_not_modified():
    data = [3, 1, 2]
    median(data)
    assert data == [3, 1, 2]
