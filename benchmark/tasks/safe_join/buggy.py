import os


def safe_join(base_dir: str, user_path: str) -> str:
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base, user_path))
    if not target.startswith(base):
        raise ValueError(f"path escapes the base directory: {user_path!r}")
    return target
