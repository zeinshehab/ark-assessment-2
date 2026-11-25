def read_config(path: str) -> dict[str, str]:
    config = {}
    f = open(path, encoding="utf-8")
    for number, line in enumerate(f, start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"malformed line {number}: {line!r}")
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    f.close()
    return config
