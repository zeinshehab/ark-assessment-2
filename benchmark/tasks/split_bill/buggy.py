def split_bill(total: str, people: int) -> list[str]:
    if people < 1:
        raise ValueError("people must be >= 1")
    amount = float(total)
    if amount < 0:
        raise ValueError("negative total")
    share = round(amount / people, 2)
    return [f"{share:.2f}"] * people
