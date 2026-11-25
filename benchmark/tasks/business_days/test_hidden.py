import pytest
from solution import business_days


def test_full_week():
    assert business_days("2024-01-01", "2024-01-05") == 5  # Monday to Friday


def test_same_weekday():
    assert business_days("2024-01-03", "2024-01-03") == 1


def test_same_saturday():
    assert business_days("2024-01-06", "2024-01-06") == 0


def test_friday_to_monday():
    assert business_days("2024-01-05", "2024-01-08") == 2


def test_two_weeks():
    assert business_days("2024-01-01", "2024-01-14") == 10


@pytest.mark.parametrize("start,end", [("2024-01-05", "2024-01-01"), ("2024-02-30", "2024-03-01"), ("yesterday", "2024-01-01")])
def test_invalid(start, end):
    with pytest.raises(Exception):
        business_days(start, end)
