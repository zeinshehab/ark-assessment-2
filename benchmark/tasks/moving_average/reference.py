def moving_average(values: list[float], window: int) -> list[float]:
    if window < 1:
        raise ValueError("window must be >= 1")
    return [sum(values[i:i + window]) / window for i in range(len(values) - window + 1)]
