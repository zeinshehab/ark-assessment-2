from datetime import date, timedelta


def business_days(start: str, end: str) -> int:
    current, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < current:
        raise ValueError("end is before start")
    count = 0
    while current < last:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count
