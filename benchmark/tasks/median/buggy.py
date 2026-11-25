def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median of empty list")
    values.sort()
    mid = len(values) // 2
    if len(values) % 2:
        return float(values[mid])
    return (values[mid - 1] + values[mid]) / 2
