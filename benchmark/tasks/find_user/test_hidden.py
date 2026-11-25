import sqlite3

import pytest
from solution import find_user


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, email TEXT)")
    c.executemany("INSERT INTO users(username, email) VALUES (?, ?)",
                  [("alice", "alice@example.com"), ("O'Brien", "ob@example.com")])
    yield c
    c.close()


def test_finds_user(conn):
    assert find_user(conn, "alice") == {"id": 1, "username": "alice", "email": "alice@example.com"}


def test_missing_user(conn):
    assert find_user(conn, "bob") is None


def test_username_with_quote(conn):
    assert find_user(conn, "O'Brien")["email"] == "ob@example.com"


@pytest.mark.parametrize("attack", ["' OR '1'='1", "alice' --", "x' UNION SELECT 1, 'a', 'b' --"])
def test_injection_returns_none(conn, attack):
    assert find_user(conn, attack) is None
