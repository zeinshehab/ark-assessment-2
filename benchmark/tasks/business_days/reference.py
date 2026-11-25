from datetime import date, timedelta


def business_days(start: str, end: str) -> int:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first:
        raise ValueError("end is before start")
    days = (last - first).days + 1
    return sum(1 for i in range(days) if (first + timedelta(days=i)).weekday() < 5)
