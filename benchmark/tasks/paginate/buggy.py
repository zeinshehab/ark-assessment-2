def paginate(items: list, page: int, page_size: int) -> list:
    if page < 0 or page_size < 1:
        raise ValueError("invalid page")
    start = page * page_size
    return items[start:start + page_size]
