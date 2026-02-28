"""Shared fixtures for joiner tests."""
from __future__ import annotations
import sqlite3
import os
import sys
from contextlib import contextmanager
import pytest

# Ensure backend dir is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _init_test_db(db_path: str) -> None:
    """Initialize a test DB with profiles and joiner tables."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            email TEXT,
            proxy TEXT,
            avatar TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ready',
            dailyUsage INTEGER NOT NULL DEFAULT 0,
            groupsConnected INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("INSERT OR IGNORE INTO profiles (id, name, username, password, status) VALUES ('p1', 'Profile 1', 'user1', 'pass1', 'ready')")
    conn.execute("INSERT OR IGNORE INTO profiles (id, name, username, password, status) VALUES ('p2', 'Profile 2', 'user2', 'pass2', 'ready')")
    conn.execute("INSERT OR IGNORE INTO profiles (id, name, username, password, status) VALUES ('p3', 'Profile 3', 'user3', 'pass3', 'idle')")
    conn.commit()

    from joiner import ensure_joiner_tables
    ensure_joiner_tables(conn)
    conn.commit()
    conn.close()


@pytest.fixture
def test_db(tmp_path):
    """Return a path-based DB that can be opened from any thread."""
    db_path = str(tmp_path / "test.db")
    _init_test_db(db_path)
    # Return a connection for direct assertions (check_same_thread=False)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def test_db_path(tmp_path):
    """Return just the DB path."""
    db_path = str(tmp_path / "test.db")
    _init_test_db(db_path)
    return db_path
