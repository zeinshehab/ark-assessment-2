import pytest
from solution import parse_duration


@pytest.mark.parametrize("text,expected", [
    ("1h30m", 5400), ("45s", 45), ("2h5s", 7205), ("1h 30m 10s", 5410), ("  10M ", 600), ("0s", 0),
])
def test_valid(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "10", "5x", "1h1h", "30m1h", "1h30", "h", "-5s", "1d"])
def test_invalid(text):
    with pytest.raises(Exception):
        parse_duration(text)
