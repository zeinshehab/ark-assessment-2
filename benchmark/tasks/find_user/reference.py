import sqlite3


def find_user(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute("SELECT id, username, email FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        return None
    return {"id": row[0], "username": row[1], "email": row[2]}
