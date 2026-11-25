import re

_UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    parts = re.findall(r"(\d+)([hms])", text.lower())
    return sum(int(number) * _UNITS[unit] for number, unit in parts)
