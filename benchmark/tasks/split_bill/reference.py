from decimal import Decimal, InvalidOperation


def split_bill(total: str, people: int) -> list[str]:
    if people < 1:
        raise ValueError("people must be >= 1")
    try:
        amount = Decimal(total)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount: {total!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"invalid amount: {total!r}")
    cents = int(amount * 100)
    base, extra = divmod(cents, people)
    shares = [base + 1] * extra + [base] * (people - extra)
    return [f"{Decimal(c) / 100:.2f}" for c in shares]
