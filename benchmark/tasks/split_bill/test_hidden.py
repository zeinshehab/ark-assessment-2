from decimal import Decimal

import pytest
from solution import split_bill


def test_even_split():
    assert split_bill("90.00", 3) == ["30.00", "30.00", "30.00"]


def test_remainder_goes_to_first_shares():
    assert split_bill("100.00", 3) == ["33.34", "33.33", "33.33"]


def test_small_amount():
    assert split_bill("0.05", 2) == ["0.03", "0.02"]


def test_shares_sum_exactly():
    shares = split_bill("10.01", 7)
    assert sum(Decimal(s) for s in shares) == Decimal("10.01")


def test_single_person():
    assert split_bill("12.34", 1) == ["12.34"]


@pytest.mark.parametrize("total,people", [("10.00", 0), ("-5.00", 2), ("abc", 2)])
def test_invalid_input(total, people):
    with pytest.raises(Exception):
        split_bill(total, people)
