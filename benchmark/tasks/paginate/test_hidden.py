import pytest
from solution import paginate

ITEMS = list(range(1, 11))


def test_first_page():
    assert paginate(ITEMS, 1, 3) == [1, 2, 3]


def test_middle_page():
    assert paginate(ITEMS, 2, 3) == [4, 5, 6]


def test_last_partial_page():
    assert paginate(ITEMS, 4, 3) == [10]


def test_past_the_end_is_empty():
    assert paginate(ITEMS, 5, 3) == []


@pytest.mark.parametrize("page,size", [(0, 3), (-1, 3), (1, 0)])
def test_invalid_arguments(page, size):
    with pytest.raises(Exception):
        paginate(ITEMS, page, size)
