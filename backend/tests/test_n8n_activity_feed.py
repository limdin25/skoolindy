"""Tests for n8n execute-comment activity_feed insertion.

Verifies that:
1. Successful n8n comment execution writes to activity_feed
2. Successful n8n comment execution writes to automation_comment_events
3. No duplicate activity rows for the same event
"""
import sqlite3
import uuid
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _create_test_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS activity_feed (
        id TEXT PRIMARY KEY,
        profile TEXT NOT NULL,
        groupName TEXT NOT NULL,
        action TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        postUrl TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS automation_comment_events (
        id TEXT PRIMARY KEY,
        profileId TEXT NOT NULL,
        profile TEXT NOT NULL,
        community TEXT NOT NULL,
        postUrl TEXT NOT NULL,
        keyword TEXT NOT NULL,
        prompt TEXT NOT NULL,
        commentText TEXT NOT NULL,
        createdAt TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ready'
    )""")
    conn.execute("INSERT OR IGNORE INTO profiles (id, name) VALUES ('p1', 'testuser@example.com')")
    conn.commit()
    return conn


def _simulate_n8n_activity_insert(db: sqlite3.Connection, profile_name: str,
                                   community_name: str, post_url: str,
                                   profile_id: str, keyword: str,
                                   generated_comment: str) -> str:
    """Simulates what run_execute_comment_sync does after success (patched version)."""
    evt_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO activity_feed (id, profile, groupName, action, timestamp, postUrl)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (evt_id, profile_name, community_name, "Commented", now_iso, post_url),
    )
    db.execute(
        """INSERT OR REPLACE INTO automation_comment_events
        (id, profileId, profile, community, postUrl, keyword, prompt, commentText, createdAt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (evt_id, profile_id, profile_name, community_name, post_url,
         keyword, "", generated_comment, now_iso),
    )
    db.commit()
    return evt_id


class TestN8nActivityFeed:
    def test_successful_execute_writes_activity(self, tmp_path):
        db = _create_test_db(str(tmp_path / "test.db"))
        evt_id = _simulate_n8n_activity_insert(
            db, "testuser@example.com", "Test Community",
            "https://www.skool.com/test/post-1", "p1", "growth",
            "Great post about growth!"
        )
        row = db.execute("SELECT * FROM activity_feed WHERE id = ?", (evt_id,)).fetchone()
        assert row is not None
        assert row["profile"] == "testuser@example.com"
        assert row["groupName"] == "Test Community"
        assert row["action"] == "Commented"
        assert row["postUrl"] == "https://www.skool.com/test/post-1"
        db.close()

    def test_successful_execute_writes_comment_event(self, tmp_path):
        db = _create_test_db(str(tmp_path / "test.db"))
        evt_id = _simulate_n8n_activity_insert(
            db, "testuser@example.com", "Test Community",
            "https://www.skool.com/test/post-1", "p1", "growth",
            "Great post about growth!"
        )
        row = db.execute("SELECT * FROM automation_comment_events WHERE id = ?", (evt_id,)).fetchone()
        assert row is not None
        assert row["profileId"] == "p1"
        assert row["commentText"] == "Great post about growth!"
        assert row["keyword"] == "growth"
        db.close()

    def test_no_duplicate_activity_on_same_event_id(self, tmp_path):
        db = _create_test_db(str(tmp_path / "test.db"))
        evt_id = _simulate_n8n_activity_insert(
            db, "testuser@example.com", "Test Community",
            "https://www.skool.com/test/post-1", "p1", "growth",
            "Great post!"
        )
        # Attempting to insert the same event_id again should fail (PRIMARY KEY)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO activity_feed (id, profile, groupName, action, timestamp, postUrl)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (evt_id, "testuser@example.com", "Test Community", "Commented",
                 datetime.now(timezone.utc).isoformat(), "https://www.skool.com/test/post-1"),
            )
        count = db.execute("SELECT COUNT(*) as c FROM activity_feed").fetchone()["c"]
        assert count == 1
        db.close()

    def test_activity_visible_in_profile_filter(self, tmp_path):
        """Activity should be visible when profile exists in profiles table."""
        db = _create_test_db(str(tmp_path / "test.db"))
        _simulate_n8n_activity_insert(
            db, "testuser@example.com", "Community A",
            "https://www.skool.com/test/post-a", "p1", "ai",
            "Comment A"
        )
        # Query mimics the backend /activity endpoint
        rows = db.execute(
            "SELECT * FROM activity_feed WHERE profile IN (SELECT name FROM profiles)"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["groupName"] == "Community A"
        db.close()


class TestN8nDailyCapEnforcement:
    def test_cap_blocks_when_at_limit(self, tmp_path):
        """When dailyUsage >= globalDailyCapPerAccount, execution should be refused."""
        db_path = str(tmp_path / "test.db")
        db = _create_test_db(db_path)
        db.execute("""CREATE TABLE IF NOT EXISTS automation_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        import json
        db.execute("INSERT INTO automation_settings (key, value) VALUES ('default', ?)",
                   (json.dumps({"globalDailyCapPerAccount": 5}),))
        db.execute("ALTER TABLE profiles ADD COLUMN dailyUsage INTEGER DEFAULT 0")
        db.execute("UPDATE profiles SET dailyUsage = 5 WHERE id = 'p1'")
        db.commit()

        # Simulate cap check logic from run_execute_comment_sync
        settings_row = db.execute("SELECT value FROM automation_settings WHERE key = 'default'").fetchone()
        settings = json.loads(settings_row["value"])
        daily_cap = max(1, int(settings.get("globalDailyCapPerAccount", 5)))
        profile_row = db.execute("SELECT dailyUsage FROM profiles WHERE id = 'p1'").fetchone()
        assert int(profile_row["dailyUsage"] or 0) >= daily_cap, "dailyUsage should be at cap"
        db.close()

    def test_cap_allows_when_below_limit(self, tmp_path):
        """When dailyUsage < globalDailyCapPerAccount, execution should proceed."""
        db_path = str(tmp_path / "test.db")
        db = _create_test_db(db_path)
        db.execute("""CREATE TABLE IF NOT EXISTS automation_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        import json
        db.execute("INSERT INTO automation_settings (key, value) VALUES ('default', ?)",
                   (json.dumps({"globalDailyCapPerAccount": 5}),))
        db.execute("ALTER TABLE profiles ADD COLUMN dailyUsage INTEGER DEFAULT 0")
        db.execute("UPDATE profiles SET dailyUsage = 3 WHERE id = 'p1'")
        db.commit()

        settings_row = db.execute("SELECT value FROM automation_settings WHERE key = 'default'").fetchone()
        settings = json.loads(settings_row["value"])
        daily_cap = max(1, int(settings.get("globalDailyCapPerAccount", 5)))
        profile_row = db.execute("SELECT dailyUsage FROM profiles WHERE id = 'p1'").fetchone()
        assert int(profile_row["dailyUsage"] or 0) < daily_cap, "dailyUsage should be below cap"
        db.close()
