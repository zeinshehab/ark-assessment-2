import re

_PATTERN = re.compile(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?", re.IGNORECASE)


def parse_duration(text: str) -> int:
    stripped = text.strip()
    match = _PATTERN.fullmatch(stripped)
    if not stripped or match is None:
        raise ValueError(f"invalid duration: {text!r}")
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds
