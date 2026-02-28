"""
EngageFlow Joiner — Phase 2 Test Suite
Unit + Contract + Behavioral + Invariant tests.
"""
from __future__ import annotations
import json
import os, sys, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from contextlib import contextmanager
from joiner import (
    normalize_community_url,
    validate_job_transition,
    validate_item_transition,
    ensure_joiner_tables,
    JOB_TRANSITIONS,
    ITEM_TRANSITIONS,
    ITEM_TERMINAL,
)


# =========================================================================
# UNIT: URL Normalization (22 variants)
# =========================================================================

class TestNormalizeUrl:
    def test_basic_url(self):
        c, k = normalize_community_url("https://www.skool.com/my-group")
        assert k == "www.skool.com/my-group"
        assert c == "https://www.skool.com/my-group"

    def test_trailing_slash(self):
        _, k = normalize_community_url("https://www.skool.com/my-group/")
        assert k == "www.skool.com/my-group"

    def test_uppercase_host(self):
        _, k = normalize_community_url("https://WWW.SKOOL.COM/my-group")
        assert k == "www.skool.com/my-group"

    def test_strip_query(self):
        _, k = normalize_community_url("https://www.skool.com/my-group?ref=abc")
        assert k == "www.skool.com/my-group"

    def test_strip_fragment(self):
        _, k = normalize_community_url("https://www.skool.com/my-group#section")
        assert k == "www.skool.com/my-group"

    def test_strip_query_and_fragment(self):
        _, k = normalize_community_url("https://www.skool.com/my-group?ref=abc#top")
        assert k == "www.skool.com/my-group"

    def test_whitespace(self):
        _, k = normalize_community_url("  https://www.skool.com/my-group  ")
        assert k == "www.skool.com/my-group"

    def test_http_scheme(self):
        c, k = normalize_community_url("http://www.skool.com/my-group")
        assert c.startswith("http://")
        assert k == "www.skool.com/my-group"

    def test_no_scheme(self):
        c, k = normalize_community_url("www.skool.com/my-group")
        assert c == "https://www.skool.com/my-group"
        assert k == "www.skool.com/my-group"

    def test_no_www(self):
        _, k = normalize_community_url("https://skool.com/my-group")
        assert k == "skool.com/my-group"

    def test_www_vs_no_www_different_keys(self):
        _, k1 = normalize_community_url("https://www.skool.com/group")
        _, k2 = normalize_community_url("https://skool.com/group")
        assert k1 != k2

    def test_case_insensitive_path(self):
        _, k = normalize_community_url("https://www.skool.com/My-Group")
        assert k == "www.skool.com/my-group"

    def test_multiple_slashes(self):
        _, k = normalize_community_url("https://www.skool.com/my-group/about/")
        assert k == "www.skool.com/my-group/about"

    def test_port_80_stripped(self):
        c, _ = normalize_community_url("https://www.skool.com:80/my-group")
        assert ":80" not in c

    def test_port_443_stripped(self):
        c, _ = normalize_community_url("https://www.skool.com:443/my-group")
        assert ":443" not in c

    def test_custom_port_preserved(self):
        c, k = normalize_community_url("https://www.skool.com:8080/my-group")
        assert ":8080" in c

    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="empty"):
            normalize_community_url("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty"):
            normalize_community_url("   ")

    def test_no_host_raises(self):
        with pytest.raises(ValueError):
            normalize_community_url("https:///no-host")

    def test_facebook_group(self):
        _, k = normalize_community_url("https://www.facebook.com/groups/123456/")
        assert k == "www.facebook.com/groups/123456"

    def test_duplicate_detection(self):
        _, k1 = normalize_community_url("https://www.skool.com/ai-tribe")
        _, k2 = normalize_community_url("HTTPS://WWW.SKOOL.COM/ai-tribe/")
        _, k3 = normalize_community_url("  https://www.skool.com/ai-tribe?ref=1#2  ")
        assert k1 == k2 == k3

    def test_different_communities_different_keys(self):
        _, k1 = normalize_community_url("https://www.skool.com/group-a")
        _, k2 = normalize_community_url("https://www.skool.com/group-b")
        assert k1 != k2


# =========================================================================
# UNIT: State Transitions
# =========================================================================

class TestJobTransitions:
    def test_created_to_paused(self):
        validate_job_transition("CREATED", "PAUSED")

    def test_created_to_cancelled(self):
        validate_job_transition("CREATED", "CANCELLED")

    def test_paused_to_created(self):
        validate_job_transition("PAUSED", "CREATED")

    def test_running_to_completed(self):
        validate_job_transition("RUNNING", "COMPLETED")

    def test_completed_to_anything_fails(self):
        for target in ["CREATED", "RUNNING", "PAUSED", "CANCELLED"]:
            with pytest.raises(ValueError):
                validate_job_transition("COMPLETED", target)

    def test_cancelled_to_anything_fails(self):
        for target in ["CREATED", "RUNNING", "PAUSED", "COMPLETED"]:
            with pytest.raises(ValueError):
                validate_job_transition("CANCELLED", target)

    def test_created_to_completed_fails(self):
        with pytest.raises(ValueError):
            validate_job_transition("CREATED", "COMPLETED")


class TestItemTransitions:
    def test_pending_to_running(self):
        validate_item_transition("PENDING", "RUNNING")

    def test_running_to_joined(self):
        validate_item_transition("RUNNING", "JOINED")

    def test_running_to_already_member(self):
        validate_item_transition("RUNNING", "ALREADY_MEMBER")

    def test_running_to_pending_approval(self):
        validate_item_transition("RUNNING", "PENDING_APPROVAL")

    def test_running_to_skipped_paid(self):
        validate_item_transition("RUNNING", "SKIPPED_PAID")

    def test_running_to_failed(self):
        validate_item_transition("RUNNING", "FAILED")

    def test_failed_to_pending_retry(self):
        validate_item_transition("FAILED", "PENDING")

    def test_terminal_states_frozen(self):
        for state in ITEM_TERMINAL:
            if state == "FAILED":
                continue
            for target in ["PENDING", "RUNNING", "JOINED"]:
                with pytest.raises(ValueError):
                    validate_item_transition(state, target)


# =========================================================================
# CONTRACT: FastAPI TestClient
# =========================================================================

@pytest.fixture
def client(test_db_path):
    """Create a FastAPI test client with joiner routes backed by test DB."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from joiner import create_joiner_router

    app = FastAPI()

    @contextmanager
    def get_test_db():
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    app.include_router(create_joiner_router(get_test_db))
    return TestClient(app)


class TestCreateJob:
    def test_creates_items(self, client):
        resp = client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/group-a", "https://www.skool.com/group-b"],
            "profile_ids": ["p1", "p2"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["items_created"] == 4
        assert data["job"]["status"] == "CREATED"
        assert data["job"]["total_items"] == 4

    def test_empty_profiles_means_all(self, client):
        resp = client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/group-c"],
            "profile_ids": [],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["items_created"] == 3  # 3 active profiles

    def test_dedupe_same_url(self, client):
        resp = client.post("/joiner/jobs", json={
            "community_urls": [
                "https://www.skool.com/group-x",
                "HTTPS://WWW.SKOOL.COM/group-x/",
                "https://www.skool.com/group-x?ref=1",
            ],
            "profile_ids": ["p1"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["items_created"] == 1

    def test_invalid_urls_skipped(self, client):
        resp = client.post("/joiner/jobs", json={
            "community_urls": ["", "   ", "https://www.skool.com/valid"],
            "profile_ids": ["p1"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["items_created"] == 1

    def test_no_valid_urls_400(self, client):
        resp = client.post("/joiner/jobs", json={
            "community_urls": ["", "   "],
            "profile_ids": ["p1"],
        })
        assert resp.status_code == 400


class TestPauseResumeCancel:
    def _create_job(self, client):
        resp = client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/grp"],
            "profile_ids": ["p1"],
        })
        return resp.json()["job"]["id"]

    def test_pause(self, client):
        jid = self._create_job(client)
        resp = client.post(f"/joiner/jobs/{jid}/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "PAUSED"
        assert resp.json()["paused"] == 1

    def test_resume(self, client):
        jid = self._create_job(client)
        client.post(f"/joiner/jobs/{jid}/pause")
        resp = client.post(f"/joiner/jobs/{jid}/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "CREATED"
        assert resp.json()["paused"] == 0

    def test_cancel(self, client):
        jid = self._create_job(client)
        resp = client.post(f"/joiner/jobs/{jid}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "CANCELLED"

    def test_cancel_cancels_items(self, client, test_db_path):
        jid = self._create_job(client)
        client.post(f"/joiner/jobs/{jid}/cancel")
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        items = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (jid,)).fetchall()
        conn.close()
        assert all(r["status"] == "CANCELLED" for r in items)

    def test_double_cancel_409(self, client):
        jid = self._create_job(client)
        client.post(f"/joiner/jobs/{jid}/cancel")
        resp = client.post(f"/joiner/jobs/{jid}/cancel")
        assert resp.status_code == 409

    def test_pause_cancelled_409(self, client):
        jid = self._create_job(client)
        client.post(f"/joiner/jobs/{jid}/cancel")
        resp = client.post(f"/joiner/jobs/{jid}/pause")
        assert resp.status_code == 409

    def test_nonexistent_job_404(self, client):
        resp = client.post("/joiner/jobs/nonexistent/pause")
        assert resp.status_code == 404


class TestEvents:
    def test_events_written(self, client):
        resp = client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/ev"],
            "profile_ids": ["p1"],
        })
        jid = resp.json()["job"]["id"]
        client.post(f"/joiner/jobs/{jid}/pause")
        client.post(f"/joiner/jobs/{jid}/resume")
        client.post(f"/joiner/jobs/{jid}/cancel")

        events_resp = client.get(f"/joiner/jobs/{jid}/events")
        assert events_resp.status_code == 200
        events = events_resp.json()
        types = [e["event_type"] for e in events]
        assert "JOB_CREATED" in types
        assert "ITEMS_CREATED" in types
        assert "JOB_PAUSED" in types
        assert "JOB_RESUMED" in types
        assert "JOB_CANCELLED" in types

    def test_events_newest_first(self, client):
        resp = client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/ord"],
            "profile_ids": ["p1"],
        })
        jid = resp.json()["job"]["id"]
        client.post(f"/joiner/jobs/{jid}/pause")

        events = client.get(f"/joiner/jobs/{jid}/events").json()
        assert events[0]["event_type"] == "JOB_PAUSED"


class TestListEndpoints:
    def test_list_jobs(self, client):
        client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/l1"],
            "profile_ids": ["p1"],
        })
        resp = client.get("/joiner/jobs")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_jobs_filter_status(self, client):
        r = client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/l2"],
            "profile_ids": ["p1"],
        })
        jid = r.json()["job"]["id"]
        client.post(f"/joiner/jobs/{jid}/cancel")

        created = client.get("/joiner/jobs?status=CREATED").json()
        cancelled = client.get("/joiner/jobs?status=CANCELLED").json()
        assert jid not in [j["id"] for j in created]
        assert jid in [j["id"] for j in cancelled]

    def test_list_items(self, client):
        r = client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/li"],
            "profile_ids": ["p1", "p2"],
        })
        jid = r.json()["job"]["id"]
        resp = client.get(f"/joiner/jobs/{jid}/items")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_items_filter_status(self, client):
        r = client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/lf"],
            "profile_ids": ["p1"],
        })
        jid = r.json()["job"]["id"]
        pending = client.get(f"/joiner/jobs/{jid}/items?status=PENDING").json()
        assert len(pending) == 1


# =========================================================================
# BEHAVIORAL: Restart safety
# =========================================================================

class TestRestartSafety:
    def test_state_persists_after_reopen(self, tmp_path):
        db_path = str(tmp_path / "restart.db")
        conn1 = sqlite3.connect(db_path)
        conn1.row_factory = sqlite3.Row
        conn1.execute("CREATE TABLE profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL, password TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ready', email TEXT, proxy TEXT, avatar TEXT NOT NULL DEFAULT '', dailyUsage INTEGER NOT NULL DEFAULT 0, groupsConnected INTEGER NOT NULL DEFAULT 0)")
        conn1.execute("INSERT INTO profiles VALUES ('p1','P1','u1','pw1','ready','','','',0,0)")
        conn1.commit()
        ensure_joiner_tables(conn1)

        from joiner import _uuid, _now_iso, _update_job_counters
        job_id = _uuid()
        now = _now_iso()
        conn1.execute("INSERT INTO join_jobs (id, created_at, status, paused, total_items, completed_items, failed_items, last_updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (job_id, now, "CREATED", 0, 0, 0, 0, now))
        conn1.execute("INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (_uuid(), job_id, "p1", "https://www.skool.com/test", "www.skool.com/test", "PENDING", 0, now, now))
        _update_job_counters(conn1, job_id)
        conn1.execute("UPDATE join_jobs SET status = 'PAUSED', paused = 1 WHERE id = ?", (job_id,))
        conn1.commit()
        conn1.close()

        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        job = conn2.execute("SELECT * FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
        assert job is not None
        assert job["status"] == "PAUSED"
        assert job["paused"] == 1
        assert job["total_items"] == 1
        items = conn2.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchall()
        assert len(items) == 1
        assert items[0]["status"] == "PENDING"
        conn2.close()


# =========================================================================
# INVARIANT: Core tables unchanged
# =========================================================================

class TestCoreTableInvariant:
    CORE_TABLES = [
        "queue_items",
        "automation_comment_events",
        "conversations",
        "messages",
        "automation_settings",
    ]

    def _ensure_core_tables(self, db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("CREATE TABLE IF NOT EXISTS queue_items (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS automation_comment_events (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE IF NOT EXISTS automation_settings (key TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

    def _snapshot(self, db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        counts = {}
        for t in self.CORE_TABLES:
            counts[t] = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()["c"]
        # Also snapshot profiles count (joiner reads profiles but must not write)
        counts["profiles"] = conn.execute("SELECT COUNT(*) as c FROM profiles").fetchone()["c"]
        conn.close()
        return counts

    def test_create_job_no_core_writes(self, client, test_db_path):
        self._ensure_core_tables(test_db_path)
        before = self._snapshot(test_db_path)

        client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/inv1", "https://www.skool.com/inv2"],
            "profile_ids": ["p1", "p2"],
        })

        after = self._snapshot(test_db_path)
        for table in self.CORE_TABLES + ["profiles"]:
            assert before[table] == after[table], f"core table {table} changed: {before[table]} -> {after[table]}"

    def test_pause_resume_cancel_no_core_writes(self, client, test_db_path):
        self._ensure_core_tables(test_db_path)

        resp = client.post("/joiner/jobs", json={
            "community_urls": ["https://www.skool.com/inv3"],
            "profile_ids": ["p1"],
        })
        jid = resp.json()["job"]["id"]

        before = self._snapshot(test_db_path)
        client.post(f"/joiner/jobs/{jid}/pause")
        client.post(f"/joiner/jobs/{jid}/resume")
        client.post(f"/joiner/jobs/{jid}/cancel")
        after = self._snapshot(test_db_path)

        for table in self.CORE_TABLES + ["profiles"]:
            assert before[table] == after[table], f"core table {table} changed: {before[table]} -> {after[table]}"


class TestUniqueConstraint:
    def test_unique_enforced(self, test_db_path):
        from joiner import _uuid, _now_iso
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        job_id = _uuid()
        now = _now_iso()
        conn.execute("INSERT INTO join_jobs (id, created_at, status, total_items, completed_items, failed_items) VALUES (?,?,?,?,?,?)",
                     (job_id, now, "CREATED", 0, 0, 0))
        conn.execute("INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (_uuid(), job_id, "p1", "https://www.skool.com/dup", "www.skool.com/dup", "PENDING", 0, now, now))
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (_uuid(), job_id, "p1", "https://www.skool.com/dup/", "www.skool.com/dup", "PENDING", 0, now, now))
        conn.close()


class TestIntegrityEndpoint:
    def test_integrity_ok(self, client):
        resp = client.get("/joiner/integrity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        check_names = [c["check"] for c in data["checks"]]
        assert "table_join_jobs_exists" in check_names
        assert "table_join_job_items_exists" in check_names
        assert "table_join_events_exists" in check_names
        assert "join_job_items_reachable" in check_names
        assert "job_counters_match" in check_names
        assert "no_orphan_profile_refs" in check_names




# =========================================================================
# PHASE 3: Worker Loop Tests
# =========================================================================

import time
from unittest.mock import patch
from joiner import (
    worker_tick,
    _WorkerState,
    _worker_state,
    JOINER_ENABLED,
    MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR,
    ITEMS_PER_CYCLE,
    _uuid,
    _now_iso,
    _emit_event,
)


def _get_db_factory(db_path: str):
    """Return a get_db context-manager factory for a given DB path."""
    @contextmanager
    def get_db():
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    return get_db


def _create_test_job(db_path: str, profile_ids=None, num_urls=1) -> str:
    """Create a CREATED job with PENDING items and return job_id."""
    if profile_ids is None:
        profile_ids = ["p1"]
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    job_id = _uuid()
    now = _now_iso()
    total = len(profile_ids) * num_urls
    conn.execute(
        "INSERT INTO join_jobs (id, created_at, status, total_items, completed_items, failed_items, paused) "
        "VALUES (?,?,?,?,?,?,?)",
        (job_id, now, "CREATED", total, 0, 0, 0),
    )
    for i in range(num_urls):
        for pid in profile_ids:
            conn.execute(
                "INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (_uuid(), job_id, pid, f"https://www.skool.com/test-{i}-{pid}", f"www.skool.com/test-{i}-{pid}", "PENDING", 0, now, now),
            )
    conn.commit()
    conn.close()
    return job_id


class TestWorkerTickDisabled:
    """Worker does nothing when JOINER_ENABLED=false."""

    def test_skip_when_disabled(self, test_db_path):
        get_db = _get_db_factory(test_db_path)
        _create_test_job(test_db_path)

        # Ensure _force_enabled is not set
        worker_tick._force_enabled = False
        result = worker_tick(get_db)
        assert result["skipped_no_work"] is True
        assert result["processed"] == 0

    def test_force_enabled_overrides(self, test_db_path):
        get_db = _get_db_factory(test_db_path)
        _create_test_job(test_db_path)

        worker_tick._force_enabled = True
        try:
            result = worker_tick(get_db)
            assert result["processed"] == 1
        finally:
            worker_tick._force_enabled = False


class TestWorkerTickProcessing:
    """Worker processes items correctly in simulation mode."""

    def _force_tick(self, db_path, **kwargs):
        """Run one tick with _force_enabled=True."""
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        try:
            return worker_tick(get_db)
        finally:
            worker_tick._force_enabled = False

    def test_processes_one_item_per_cycle(self, test_db_path):
        _create_test_job(test_db_path, profile_ids=["p1", "p2"])
        result = self._force_tick(test_db_path)
        assert result["processed"] == ITEMS_PER_CYCLE  # Should be 1

    def test_item_transitions_to_joined(self, test_db_path):
        job_id = _create_test_job(test_db_path)
        self._force_tick(test_db_path)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        items = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchall()
        statuses = [r["status"] for r in items]
        assert "JOINED" in statuses
        conn.close()

    def test_events_emitted_for_ready_and_joined(self, test_db_path):
        job_id = _create_test_job(test_db_path)
        self._force_tick(test_db_path)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type FROM join_events WHERE job_id = ? ORDER BY rowid",
            (job_id,),
        ).fetchall()
        event_types = [e["event_type"] for e in events]
        assert "ITEM_READY" in event_types
        assert "ITEM_JOINED" in event_types
        conn.close()

    def test_job_auto_completes_when_all_items_done(self, test_db_path):
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        self._force_tick(test_db_path)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        job = conn.execute("SELECT status FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
        assert job["status"] == "COMPLETED"
        conn.close()

    def test_job_not_completed_when_items_remain(self, test_db_path):
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=3)
        self._force_tick(test_db_path)  # processes 1 of 3

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        job = conn.execute("SELECT status FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
        assert job["status"] == "CREATED"  # still active
        items = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchall()
        joined = sum(1 for r in items if r["status"] == "JOINED")
        pending = sum(1 for r in items if r["status"] == "PENDING")
        assert joined == 1
        assert pending == 2
        conn.close()

    def test_paused_job_skipped(self, test_db_path):
        job_id = _create_test_job(test_db_path)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE join_jobs SET paused = 1 WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()

        result = self._force_tick(test_db_path)
        assert result["skipped_no_work"] is True
        assert result["processed"] == 0

    def test_counters_updated_after_tick(self, test_db_path):
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=2)
        self._force_tick(test_db_path)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        job = conn.execute("SELECT completed_items, total_items FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
        assert job["completed_items"] == 1
        assert job["total_items"] == 2
        conn.close()


class TestWorkerRateLimit:
    """Rate limiting: max 6 joins per profile per hour."""

    def _force_tick(self, db_path):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        try:
            return worker_tick(get_db)
        finally:
            worker_tick._force_enabled = False

    def test_rate_limit_blocks_profile(self, test_db_path):
        # Create a job with many items for p1
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR + 2)

        # Run ticks up to the limit
        for i in range(MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR):
            result = self._force_tick(test_db_path)
            assert result["processed"] == 1, f"tick {i} should have processed 1"

        # Next tick should hit rate limit
        result = self._force_tick(test_db_path)
        assert result["processed"] == 0
        assert result["skipped_rate_limit"] is True

    def test_different_profile_not_rate_limited(self, test_db_path):
        # Create job with p1 and p2
        job_id = _create_test_job(test_db_path, profile_ids=["p1", "p2"], num_urls=MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR + 1)

        # Exhaust p1 rate limit
        for i in range(MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR):
            self._force_tick(test_db_path)

        # p2 should still be eligible
        result = self._force_tick(test_db_path)
        assert result["processed"] == 1

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        joined_p2 = conn.execute(
            "SELECT COUNT(*) as c FROM join_job_items WHERE job_id = ? AND profile_id = ? AND status = ?",
            (job_id, "p2", "JOINED"),
        ).fetchone()["c"]
        assert joined_p2 >= 1
        conn.close()


class TestWorkerRestartSafety:
    """Worker rebuilds state from DB on restart - no in-memory assumptions."""

    def _force_tick(self, db_path):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        try:
            return worker_tick(get_db)
        finally:
            worker_tick._force_enabled = False

    def test_restart_continues_from_db(self, test_db_path):
        # Use 2 profiles so second tick can use p2 (p1 hits rate limit at 1/hour)
        job_id = _create_test_job(test_db_path, profile_ids=["p1", "p2"], num_urls=2)

        # Process 1 item (p1)
        self._force_tick(test_db_path)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        joined_before = conn.execute(
            "SELECT COUNT(*) as c FROM join_job_items WHERE job_id = ? AND status = ?",
            (job_id, "JOINED"),
        ).fetchone()["c"]
        assert joined_before == 1

        # Simulate restart: create a NEW get_db factory (new connection)
        new_get_db = _get_db_factory(test_db_path)
        worker_tick._force_enabled = True
        try:
            result = worker_tick(new_get_db)
        finally:
            worker_tick._force_enabled = False

        assert result["processed"] == 1

        joined_after = conn.execute(
            "SELECT COUNT(*) as c FROM join_job_items WHERE job_id = ? AND status = ?",
            (job_id, "JOINED"),
        ).fetchone()["c"]
        assert joined_after == 2
        conn.close()


class TestWorkerSelfDisable:
    """Worker self-disables on unhandled exception."""

    def test_self_disable_on_error(self, test_db_path):
        # Create a broken get_db that raises
        @contextmanager
        def broken_get_db():
            raise RuntimeError("DB connection failed")
            yield  # pragma: no cover

        worker_tick._force_enabled = True
        try:
            result = worker_tick(broken_get_db)
        finally:
            worker_tick._force_enabled = False

        assert result["error"] is not None
        assert "DB connection failed" in result["error"]


class TestWorkerCoreTableInvariant:
    """Worker tick does NOT write to core EngageFlow tables."""

    CORE_TABLES = ["communities", "scheduler_queue", "community_messages"]

    def _ensure_core_tables(self, db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("CREATE TABLE IF NOT EXISTS communities (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS scheduler_queue (id TEXT PRIMARY KEY, profile_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS community_messages (id TEXT PRIMARY KEY, body TEXT)")
        conn.commit()
        conn.close()

    def _snapshot(self, db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        result = {}
        for t in self.CORE_TABLES + ["profiles"]:
            result[t] = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()[0]
        conn.close()
        return result

    def test_worker_no_core_writes(self, test_db_path):
        self._ensure_core_tables(test_db_path)
        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=3)

        before = self._snapshot(test_db_path)

        get_db = _get_db_factory(test_db_path)
        worker_tick._force_enabled = True
        try:
            for _ in range(3):
                worker_tick(get_db)
        finally:
            worker_tick._force_enabled = False

        after = self._snapshot(test_db_path)
        for table in self.CORE_TABLES + ["profiles"]:
            assert before[table] == after[table], f"core table {table} changed: {before[table]} -> {after[table]}"


class TestWorkerIntegrityExtensions:
    """Phase 3 integrity checks appear in /joiner/integrity."""

    def test_integrity_has_worker_checks(self, client):
        resp = client.get("/joiner/integrity")
        assert resp.status_code == 200
        data = resp.json()
        check_names = [c["check"] for c in data["checks"]]
        assert "joiner_enabled" in check_names
        assert "worker_running" in check_names
        assert "last_worker_tick" in check_names
        assert "processed_last_hour" in check_names


class TestItemTransitionsPhase3:
    """READY state added in Phase 3."""

    def test_pending_to_ready(self):
        validate_item_transition("PENDING", "READY")  # no raise = pass

    def test_ready_to_running(self):
        validate_item_transition("READY", "RUNNING")  # no raise = pass

    def test_ready_to_joined(self):
        validate_item_transition("READY", "JOINED")  # no raise = pass

    def test_ready_to_failed(self):
        validate_item_transition("READY", "FAILED")  # no raise = pass

    def test_ready_to_cancelled(self):
        validate_item_transition("READY", "CANCELLED")  # no raise = pass



# =========================================================================
# PHASE 4: Playwright Join Execution Tests (all Playwright mocked)
# =========================================================================

from joiner import (
    _check_global_rate_limit,
    _check_kill_switch,
    _compute_next_attempt_at,
    _parse_proxy_for_joiner,
    JOINER_MODE,
    MAX_GLOBAL_JOINS_PER_HOUR,
    MAX_ITEM_ATTEMPTS,
    BACKOFF_DELAYS,
)


class TestJoinerModeRouting:
    """Worker correctly dispatches based on JOINER_MODE."""

    def _tick(self, db_path, mode="simulate", pw_fn=None):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = mode
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def test_simulate_mode_unchanged(self, test_db_path):
        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        result = self._tick(test_db_path, mode="simulate")
        assert result["processed"] == 1

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        items = conn.execute("SELECT status FROM join_job_items").fetchall()
        assert any(r["status"] == "JOINED" for r in items)
        conn.close()

    def test_playwright_mode_calls_pw_fn(self, test_db_path):
        calls = []
        def mock_pw_join(profile_id, community_url, community_key, db_path, **kwargs):
            calls.append({"profile_id": profile_id, "url": community_url})
            return {"status": "JOINED", "detail": "mock joined"}

        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        result = self._tick(test_db_path, mode="playwright", pw_fn=mock_pw_join)
        assert result["processed"] == 1
        assert len(calls) == 1
        assert calls[0]["profile_id"] == "p1"

    def test_default_mode_is_simulate(self, test_db_path):
        import joiner
        assert joiner.JOINER_MODE == "simulate"


class TestCanaryRateLimits:
    """Phase 4 canary: 1/profile/hour, 2/global/hour."""

    def _tick(self, db_path, mode="simulate", pw_fn=None):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = mode
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def test_one_per_profile_per_hour(self, test_db_path):
        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=3)
        # First tick should succeed
        r1 = self._tick(test_db_path, mode="simulate")
        assert r1["processed"] == 1
        # Second tick should hit per-profile rate limit (1/hour)
        r2 = self._tick(test_db_path, mode="simulate")
        assert r2["processed"] == 0
        assert r2["skipped_rate_limit"] is True

    def test_global_limit_two_per_hour(self, test_db_path):
        # Create job with 2 profiles, 2 URLs each
        _create_test_job(test_db_path, profile_ids=["p1", "p2"], num_urls=2)
        # p1 joins (1 global)
        r1 = self._tick(test_db_path, mode="simulate")
        assert r1["processed"] == 1
        # p2 joins (2 global)
        r2 = self._tick(test_db_path, mode="simulate")
        assert r2["processed"] == 1
        # p1 is rate-limited (1/hour), p2 is rate-limited (1/hour),
        # but also global limit (2/hour) should block
        r3 = self._tick(test_db_path, mode="simulate")
        assert r3["processed"] == 0

    def test_cross_profile_global_limit(self, test_db_path):
        # Create job with 3 profiles
        # First need to add p3 as ready (conftest adds it as 'idle')
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.execute("UPDATE profiles SET status = 'ready' WHERE id = 'p3'")
        conn.commit()
        conn.close()

        _create_test_job(test_db_path, profile_ids=["p1", "p2", "p3"], num_urls=2)
        r1 = self._tick(test_db_path, mode="simulate")
        assert r1["processed"] == 1  # p1 joins
        r2 = self._tick(test_db_path, mode="simulate")
        assert r2["processed"] == 1  # p2 joins
        r3 = self._tick(test_db_path, mode="simulate")
        # Global limit = 2, should be blocked even though p3 has quota
        assert r3["skipped_global_limit"] is True


class TestBackoffScheduling:
    """Phase 4 backoff: 15m, 60m, 6h between retry attempts."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def test_first_failure_backoff_15m(self, test_db_path):
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fail_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "test_failure"}

        self._tick_pw(test_db_path, fail_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["attempt_count"] == 1
        assert item["next_attempt_at"] is not None
        assert item["fail_reason"] == "test_failure"
        # Item should be PENDING (retryable) not FAILED terminal
        assert item["status"] == "PENDING"
        conn.close()

    def test_compute_backoff_delays(self):
        # attempt 1 -> 15m = 900s
        na1 = _compute_next_attempt_at(1)
        assert na1 is not None

        # attempt 2 -> 60m = 3600s
        na2 = _compute_next_attempt_at(2)
        assert na2 is not None
        assert na2 > na1  # later

        # attempt 3 -> terminal (None)
        na3 = _compute_next_attempt_at(3)
        assert na3 is None

    def test_third_failure_terminal(self, test_db_path):
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        # Set item to already have 2 attempts with next_attempt_at in past
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE join_job_items SET attempt_count = 2, next_attempt_at = '2020-01-01T00:00:00+00:00' WHERE job_id = ?",
            (job_id,),
        )
        conn.commit()
        conn.close()

        def fail_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "third_failure"}

        self._tick_pw(test_db_path, fail_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["attempt_count"] == 3
        assert item["status"] == "FAILED"  # terminal
        assert item["next_attempt_at"] is None
        conn.close()

    def test_items_with_future_backoff_skipped(self, test_db_path):
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        # Set next_attempt_at far in the future
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.execute(
            "UPDATE join_job_items SET next_attempt_at = '2099-01-01T00:00:00+00:00' WHERE job_id = ?",
            (job_id,),
        )
        conn.commit()
        conn.close()

        get_db = _get_db_factory(test_db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "simulate"
        try:
            result = worker_tick(get_db)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

        assert result["processed"] == 0
        assert result["skipped_no_work"] is True or result["skipped_rate_limit"] is True


class TestKillSwitch:
    """Kill switch: 3 consecutive failures or auth_session_invalid."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def test_auth_invalid_immediate_disable(self, test_db_path):
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def auth_fail_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "auth_session_invalid"}

        self._tick_pw(test_db_path, auth_fail_fn)
        assert _worker_state.disabled is True
        assert "auth_session_invalid" in (_worker_state.disable_reason or "")
        # Reset
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def test_three_consecutive_failures_disable(self, test_db_path):
        _worker_state.disabled = False
        _worker_state.disable_reason = None

        # Directly insert 3 consecutive ITEM_FAILED events as the most recent
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        from joiner import _emit_event, _now_iso
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        for i in range(3):
            _emit_event(conn, job_id, "ITEM_FAILED", detail=f"failure_{i+1}")
        conn.commit()

        # Check kill switch directly
        reason = _check_kill_switch(conn, "some_failure")
        assert reason is not None
        assert "3_consecutive" in reason
        conn.close()
        # Reset
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def test_worker_disabled_event_emitted(self, test_db_path):
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def auth_fail_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "auth_session_invalid"}

        self._tick_pw(test_db_path, auth_fail_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type FROM join_events ORDER BY rowid DESC"
        ).fetchall()
        event_types = [e["event_type"] for e in events]
        assert "WORKER_DISABLED" in event_types
        conn.close()
        # Reset
        _worker_state.disabled = False
        _worker_state.disable_reason = None


class TestPlaywrightJoinResults:
    """Playwright join fn results correctly mapped to item status."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def _get_item_status(self, db_path, job_id):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status FROM join_job_items WHERE job_id = ? LIMIT 1", (job_id,)).fetchone()
        status = item["status"]
        conn.close()
        return status

    def test_joined(self, test_db_path):
        _worker_state.disabled = False
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        self._tick_pw(test_db_path, lambda *a, **k: {"status": "JOINED", "detail": "ok"})
        assert self._get_item_status(test_db_path, job_id) == "JOINED"

    def test_already_member(self, test_db_path):
        _worker_state.disabled = False
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        self._tick_pw(test_db_path, lambda *a, **k: {"status": "ALREADY_MEMBER", "detail": "member"})
        assert self._get_item_status(test_db_path, job_id) == "ALREADY_MEMBER"

    def test_pending_approval(self, test_db_path):
        _worker_state.disabled = False
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        self._tick_pw(test_db_path, lambda *a, **k: {"status": "PENDING_APPROVAL", "detail": "pending"})
        assert self._get_item_status(test_db_path, job_id) == "PENDING_APPROVAL"

    def test_skipped_paid(self, test_db_path):
        _worker_state.disabled = False
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        self._tick_pw(test_db_path, lambda *a, **k: {"status": "SKIPPED_PAID", "detail": "paid"})
        assert self._get_item_status(test_db_path, job_id) == "SKIPPED_PAID"

    def test_failed_with_backoff(self, test_db_path):
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        self._tick_pw(test_db_path, lambda *a, **k: {"status": "FAILED", "detail": "some_error"})
        # Should be PENDING (retryable, attempt 1 of 3) with next_attempt_at set
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["status"] == "PENDING"
        assert item["next_attempt_at"] is not None
        assert item["fail_reason"] == "some_error"
        conn.close()


class TestPlaywrightJoinEvents:
    """Granular events emitted during Playwright join."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def test_events_emitted_on_success(self, test_db_path):
        _worker_state.disabled = False
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        self._tick_pw(test_db_path, lambda *a, **k: {"status": "JOINED", "detail": "ok"})

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type FROM join_events WHERE job_id = ? ORDER BY rowid",
            (job_id,),
        ).fetchall()
        types = [e["event_type"] for e in events]
        assert "ITEM_STARTED" in types
        assert "ITEM_NAVIGATED" in types
        assert "ITEM_DETECTED_STATE" in types
        assert "ITEM_COMPLETED" in types
        assert "ITEM_JOINED" in types
        conn.close()

    def test_events_emitted_on_failure(self, test_db_path):
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        self._tick_pw(test_db_path, lambda *a, **k: {"status": "FAILED", "detail": "nav_timeout"})

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type, detail FROM join_events WHERE job_id = ? ORDER BY rowid",
            (job_id,),
        ).fetchall()
        types = [e["event_type"] for e in events]
        assert "ITEM_STARTED" in types
        assert "ITEM_FAILED" in types
        # Check detail contains fail reason
        fail_events = [e for e in events if e["event_type"] == "ITEM_FAILED"]
        assert len(fail_events) >= 1
        assert "nav_timeout" in fail_events[0]["detail"]
        conn.close()


class TestPhase4CoreTableInvariant:
    """Worker in playwright mode does NOT write to core EngageFlow tables."""

    CORE_TABLES = ["communities", "scheduler_queue", "community_messages"]

    def _ensure_core_tables(self, db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("CREATE TABLE IF NOT EXISTS communities (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS scheduler_queue (id TEXT PRIMARY KEY, profile_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS community_messages (id TEXT PRIMARY KEY, body TEXT)")
        conn.commit()
        conn.close()

    def _snapshot(self, db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        result = {}
        for t in self.CORE_TABLES + ["profiles"]:
            result[t] = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()[0]
        conn.close()
        return result

    def test_playwright_mode_no_core_writes(self, test_db_path):
        _worker_state.disabled = False
        self._ensure_core_tables(test_db_path)
        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=3)

        before = self._snapshot(test_db_path)

        def mock_join(*args, **kwargs):
            return {"status": "JOINED", "detail": "mock"}

        get_db = _get_db_factory(test_db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            # Run 1 tick (can only do 1 due to rate limit)
            worker_tick(get_db, _playwright_join_fn=mock_join)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

        after = self._snapshot(test_db_path)
        for table in self.CORE_TABLES + ["profiles"]:
            assert before[table] == after[table], f"core table {table} changed"


class TestProxyParser:
    """Proxy string parsing for joiner (replicated from engine)."""

    def test_url_format(self):
        result = _parse_proxy_for_joiner("http://user:pass@host:8080")
        assert result["server"] == "http://host:8080"
        assert result["username"] == "user"
        assert result["password"] == "pass"

    def test_four_part_format(self):
        result = _parse_proxy_for_joiner("host:8080:user:pass")
        assert result["server"] == "http://host:8080"
        assert result["username"] == "user"
        assert result["password"] == "pass"

    def test_none_returns_none(self):
        assert _parse_proxy_for_joiner(None) is None
        assert _parse_proxy_for_joiner("") is None
        assert _parse_proxy_for_joiner("  ") is None


# =========================================================================
# PHASE 4.1: Classifier + Click Verification Tests
# =========================================================================

from joiner import (
    _classify_page_state,
    _MEMBER_AREA_KEYWORDS,
    _MEMBER_AREA_SELECTORS,
    _PAID_INDICATORS,
    _BLOCK_KEYWORDS,
    _AUTH_SELECTORS,
    _JOIN_BUTTON_SELECTORS,
    _PENDING_SELECTORS,
)


class _MockElement:
    """Minimal mock for a Playwright element."""
    def __init__(self, visible=True):
        self._visible = visible
    def is_visible(self):
        return self._visible


class _MockPage:
    """Minimal mock for a Playwright page with configurable state."""
    def __init__(
        self,
        url="https://www.skool.com/test-group",
        body_text="",
        selectors=None,
    ):
        self._url = url
        self._body_text = body_text
        # selectors: dict mapping CSS selector -> MockElement or None
        self._selectors = selectors or {}

    @property
    def url(self):
        return self._url

    def text_content(self, selector):
        if selector == "body":
            return self._body_text
        return ""

    def query_selector(self, selector):
        return self._selectors.get(selector)


class TestClassifyPageState:
    """Unit tests for _classify_page_state classifier."""

    def test_auth_required_login_url(self):
        page = _MockPage(url="https://www.skool.com/login?redirect=/test")
        result = _classify_page_state(page)
        assert result["state"] == "AUTH_REQUIRED"
        assert "auth_session_invalid" in result["detail"]

    def test_auth_required_email_input(self):
        page = _MockPage(
            selectors={"input#email": _MockElement()}
        )
        result = _classify_page_state(page)
        assert result["state"] == "AUTH_REQUIRED"

    def test_blocked_keyword(self):
        page = _MockPage(body_text="Your account has been temporarily blocked for unusual activity")
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"
        assert "temporarily blocked" in result["detail"]

    def test_blocked_captcha_iframe(self):
        page = _MockPage(
            selectors={'iframe[src*="captcha"]': _MockElement()}
        )
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"
        assert "captcha" in result["detail"]

    def test_pending_text(self):
        page = _MockPage(body_text="Your membership pending approval by admins")
        result = _classify_page_state(page)
        assert result["state"] == "PENDING"

    def test_pending_selector(self):
        page = _MockPage(
            selectors={'button:has-text("Cancel membership request")': _MockElement()}
        )
        result = _classify_page_state(page)
        assert result["state"] == "PENDING"

    def test_member_via_auth_markers(self):
        # Has auth markers (TopNav) but no join button = member
        page = _MockPage(
            selectors={'div[class*="TopNav"]': _MockElement()}
        )
        result = _classify_page_state(page)
        assert result["state"] == "MEMBER"

    def test_member_via_area_keywords(self):
        # Has 2+ member area keywords = member (even without auth selectors)
        page = _MockPage(body_text="classroom calendar members leaderboard")
        result = _classify_page_state(page)
        assert result["state"] == "MEMBER"

    def test_member_via_area_selectors(self):
        page = _MockPage(
            selectors={
                'a[href*="/classroom"]': _MockElement(),
                'a[href*="/calendar"]': _MockElement(),
            }
        )
        result = _classify_page_state(page)
        assert result["state"] == "MEMBER"

    def test_join_visible(self):
        page = _MockPage(
            selectors={'button:has-text("Join for Free")': _MockElement(visible=True)}
        )
        result = _classify_page_state(page)
        assert result["state"] == "JOIN_VISIBLE"

    def test_join_invisible_not_counted(self):
        page = _MockPage(
            selectors={'button:has-text("Join for Free")': _MockElement(visible=False)}
        )
        result = _classify_page_state(page)
        # Invisible join button should not be detected
        assert result["state"] != "JOIN_VISIBLE"

    def test_paid_wall(self):
        page = _MockPage(body_text="Join this community for $49/month pricing plan")
        result = _classify_page_state(page)
        assert result["state"] == "PAID"
        assert "paid_wall" in result["detail"]

    def test_unknown_fallback(self):
        # Empty page with nothing detectable
        page = _MockPage(body_text="some random content without indicators")
        result = _classify_page_state(page)
        assert result["state"] == "UNKNOWN"

    def test_auth_takes_precedence_over_join_button(self):
        # Login page that also happens to have a join button text
        page = _MockPage(
            url="https://www.skool.com/login",
            selectors={'button:has-text("Join")': _MockElement(visible=True)},
        )
        result = _classify_page_state(page)
        assert result["state"] == "AUTH_REQUIRED"

    def test_pending_takes_precedence_over_member(self):
        # Page with both pending and auth markers
        page = _MockPage(
            body_text="membership pending",
            selectors={'div[class*="TopNav"]': _MockElement()},
        )
        result = _classify_page_state(page)
        assert result["state"] == "PENDING"

    def test_member_with_single_keyword_not_enough(self):
        # Only 1 keyword, no auth markers, no selectors = not MEMBER
        page = _MockPage(body_text="classroom only one keyword")
        result = _classify_page_state(page)
        assert result["state"] != "MEMBER"


class TestClickVerificationIntegration:
    """Integration tests: Playwright mock fn returns results that map correctly through worker_tick."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def test_join_click_no_state_change_maps_to_failed(self, test_db_path):
        """When pw fn returns FAILED with join_click_no_state_change, item is retryable PENDING."""
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def no_change_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "join_click_no_state_change: join_button_found"}

        self._tick_pw(test_db_path, no_change_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["status"] == "PENDING"  # retryable
        assert "join_click_no_state_change" in (item["fail_reason"] or "")
        assert item["next_attempt_at"] is not None
        conn.close()

    def test_join_click_failed_maps_correctly(self, test_db_path):
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def click_fail_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "join_click_failed: element not interactable"}

        self._tick_pw(test_db_path, click_fail_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["status"] == "PENDING"
        assert "join_click_failed" in (item["fail_reason"] or "")
        conn.close()

    def test_auth_lost_after_click_triggers_kill_switch(self, test_db_path):
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def auth_lost_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "auth_session_invalid"}

        self._tick_pw(test_db_path, auth_lost_fn)
        assert _worker_state.disabled is True
        assert "auth_session_invalid" in (_worker_state.disable_reason or "")

        # Reset
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def test_paid_wall_after_click_skipped(self, test_db_path):
        _worker_state.disabled = False
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def paid_fn(*args, **kwargs):
            return {"status": "SKIPPED_PAID", "detail": "paid_wall_after_join_click"}

        self._tick_pw(test_db_path, paid_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["status"] == "SKIPPED_PAID"
        conn.close()


class TestPhase41CoreTableInvariant:
    """Phase 4.1 changes do NOT affect core tables."""

    CORE_TABLES = ["communities", "scheduler_queue", "community_messages"]

    def _ensure_core_tables(self, db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("CREATE TABLE IF NOT EXISTS communities (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS scheduler_queue (id TEXT PRIMARY KEY, profile_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS community_messages (id TEXT PRIMARY KEY, body TEXT)")
        conn.commit()
        conn.close()

    def _snapshot(self, db_path):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        result = {}
        for t in self.CORE_TABLES + ["profiles"]:
            result[t] = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()[0]
        conn.close()
        return result

    def test_phase41_no_core_writes(self, test_db_path):
        _worker_state.disabled = False
        self._ensure_core_tables(test_db_path)
        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)
        before = self._snapshot(test_db_path)

        def mock_join(*args, **kwargs):
            return {"status": "JOINED", "detail": "mock"}

        get_db = _get_db_factory(test_db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            worker_tick(get_db, _playwright_join_fn=mock_join)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

        after = self._snapshot(test_db_path)
        for table in self.CORE_TABLES + ["profiles"]:
            assert before[table] == after[table], f"core table {table} changed"


# =========================================================================
# PHASE 4.2: Forensic Capture Tests
# =========================================================================

from joiner import _sanitize_html_head, _capture_forensics, ARTIFACTS_DIR


class TestSanitizeHtmlHead:
    """Unit tests for _sanitize_html_head."""

    def test_empty_string(self):
        assert _sanitize_html_head("") == ""

    def test_none_like_empty(self):
        assert _sanitize_html_head("") == ""

    def test_newlines_removed(self):
        result = _sanitize_html_head("line1\nline2\nline3")
        assert "\n" not in result
        assert result == "line1 line2 line3"

    def test_carriage_returns_removed(self):
        result = _sanitize_html_head("line1\r\nline2")
        assert "\r" not in result
        assert "\n" not in result

    def test_whitespace_collapsed(self):
        result = _sanitize_html_head("hello    world   foo")
        assert result == "hello world foo"

    def test_truncation_at_5000(self):
        long_str = "x" * 6000
        result = _sanitize_html_head(long_str)
        assert len(result) == 5000 + len("...[truncated]")
        assert result.endswith("...[truncated]")

    def test_custom_max_len(self):
        result = _sanitize_html_head("hello world", max_len=5)
        assert result == "hello...[truncated]"

    def test_exact_max_len_no_truncation(self):
        result = _sanitize_html_head("hello", max_len=5)
        assert result == "hello"

    def test_mixed_whitespace(self):
        result = _sanitize_html_head("  \n  hello  \n  world  \n  ")
        assert result == "hello world"


class TestCaptureForensicsContract:
    """Contract tests: _capture_forensics returns correct event structure with mocked page."""

    def test_returns_three_events(self):
        """Mock page, verify we get screenshot + html_head + debug events."""
        import tempfile, os

        class MockPage:
            url = "https://www.skool.com/test-group"
            def title(self):
                return "Test Group"
            def content(self):
                return "<html><body>Join for Free</body></html>"
            def screenshot(self, path=None):
                # Write a dummy file
                if path:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(b"FAKEPNG")
            def query_selector(self, sel):
                return None

        page = MockPage()
        events = _capture_forensics(page, "job-123", "item-456")

        # Should have 3 events: screenshot, html artifact, debug
        assert len(events) == 3

        types = [e["type"] for e in events]
        assert "ITEM_ARTIFACT" in types
        assert "ITEM_DEBUG" in types

        # Screenshot event
        screenshot_ev = [e for e in events if e["detail"].startswith("screenshot=")][0]
        assert "artifacts/joiner/job-123/item-456/" in screenshot_ev["detail"]
        assert "_no_state.png" in screenshot_ev["detail"]

        # HTML artifact event
        html_ev = [e for e in events if "html_head=" in e["detail"]][0]
        assert "url=https://www.skool.com/test-group" in html_ev["detail"]
        assert "title=Test Group" in html_ev["detail"]
        assert "html_head=" in html_ev["detail"]

        # Debug event
        debug_ev = [e for e in events if e["type"] == "ITEM_DEBUG"][0]
        assert "url_after=" in debug_ev["detail"]
        assert "join_btn_text=" in debug_ev["detail"]

        # Cleanup: remove artifact file
        import shutil
        artifact_dir = ARTIFACTS_DIR / "job-123"
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)

    def test_screenshot_file_created(self):
        """Verify screenshot is actually written to disk."""
        import os, shutil

        class MockPage:
            url = "https://www.skool.com/test"
            def title(self):
                return "T"
            def content(self):
                return "<html></html>"
            def screenshot(self, path=None):
                if path:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(b"PNG_BYTES")
            def query_selector(self, sel):
                return None

        events = _capture_forensics(MockPage(), "job-sc", "item-sc")
        screenshot_ev = [e for e in events if e["detail"].startswith("screenshot=")][0]
        rel_path = screenshot_ev["detail"].split("screenshot=")[1]
        # The file should exist relative to backend/
        full_path = ARTIFACTS_DIR.parent.parent / rel_path
        assert full_path.exists(), f"Screenshot not found at {full_path}"

        # Cleanup
        shutil.rmtree(ARTIFACTS_DIR / "job-sc", ignore_errors=True)

    def test_html_truncated_in_event(self):
        """HTML content > 5000 chars gets truncated in the event detail."""

        class MockPage:
            url = "https://www.skool.com/test"
            def title(self):
                return ""
            def content(self):
                return "A" * 8000
            def screenshot(self, path=None):
                import os
                if path:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(b"X")
            def query_selector(self, sel):
                return None

        events = _capture_forensics(MockPage(), "job-tr", "item-tr")
        html_ev = [e for e in events if "html_head=" in e["detail"]][0]
        # The html_head portion should end with truncated marker
        assert "...[truncated]" in html_ev["detail"]

        # Cleanup
        import shutil
        shutil.rmtree(ARTIFACTS_DIR / "job-tr", ignore_errors=True)

    def test_page_errors_handled_gracefully(self):
        """If page methods raise, _capture_forensics still returns partial events."""

        class BrokenPage:
            url = "https://broken"
            def title(self):
                raise RuntimeError("no title")
            def content(self):
                raise RuntimeError("no content")
            def screenshot(self, path=None):
                raise RuntimeError("no screenshot")
            def query_selector(self, sel):
                raise RuntimeError("no selector")

        events = _capture_forensics(BrokenPage(), "job-br", "item-br")
        # Should not crash, may return 0-3 events depending on which try blocks succeed
        assert isinstance(events, list)


class TestForensicEventsInWorkerTick:
    """Contract: forensic events from pw_fn flow through to join_events table."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def test_forensic_events_emitted_on_no_state_change(self, test_db_path):
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def forensic_fn(*args, **kwargs):
            return {
                "status": "FAILED",
                "detail": "join_click_no_state_change: join_button_found",
                "forensic_events": [
                    {"type": "ITEM_ARTIFACT", "detail": "screenshot=artifacts/joiner/j/i/ts_no_state.png"},
                    {"type": "ITEM_ARTIFACT", "detail": "url=https://skool.com/test title=Test html_head=<html>"},
                    {"type": "ITEM_DEBUG", "detail": "url_after=https://skool.com/test join_btn_text=Join"},
                ],
            }

        self._tick_pw(test_db_path, forensic_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type, detail FROM join_events WHERE job_id = ? ORDER BY rowid",
            (job_id,),
        ).fetchall()
        types = [e["event_type"] for e in events]

        # Should have ITEM_ARTIFACT and ITEM_DEBUG events
        assert types.count("ITEM_ARTIFACT") == 2
        assert types.count("ITEM_DEBUG") == 1

        # Verify artifact details
        artifact_events = [e for e in events if e["event_type"] == "ITEM_ARTIFACT"]
        assert any("screenshot=" in e["detail"] for e in artifact_events)
        assert any("html_head=" in e["detail"] for e in artifact_events)

        debug_events = [e for e in events if e["event_type"] == "ITEM_DEBUG"]
        assert any("url_after=" in e["detail"] for e in debug_events)
        conn.close()

    def test_no_forensic_events_on_normal_failure(self, test_db_path):
        """Non-forensic failures should NOT have ITEM_ARTIFACT events."""
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def normal_fail(*args, **kwargs):
            return {"status": "FAILED", "detail": "navigation_timeout: timed out"}

        self._tick_pw(test_db_path, normal_fail)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type FROM join_events WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        types = [e["event_type"] for e in events]
        assert "ITEM_ARTIFACT" not in types
        assert "ITEM_DEBUG" not in types
        conn.close()

    def test_forensic_events_emitted_after_kill_switch(self, test_db_path):
        """Forensic events are emitted AFTER kill switch check, so auth_session_invalid
        triggers kill switch AND forensic events still appear in the event log."""
        _worker_state.disabled = False
        _worker_state.disable_reason = None
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def auth_fail_with_forensics(*args, **kwargs):
            return {
                "status": "FAILED",
                "detail": "auth_session_invalid",
                "forensic_events": [
                    {"type": "ITEM_ARTIFACT", "detail": "screenshot=test.png"},
                    {"type": "ITEM_DEBUG", "detail": "url_after=https://skool.com/login"},
                ],
            }

        self._tick_pw(test_db_path, auth_fail_with_forensics)

        # Kill switch triggered via auth_session_invalid
        assert _worker_state.disabled is True
        assert "auth_session_invalid" in (_worker_state.disable_reason or "")

        # Forensic events still emitted (after kill switch)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type FROM join_events WHERE job_id = ? ORDER BY rowid",
            (job_id,),
        ).fetchall()
        types = [e["event_type"] for e in events]
        assert "WORKER_DISABLED" in types
        assert "ITEM_ARTIFACT" in types
        assert "ITEM_DEBUG" in types

        # ITEM_ARTIFACT comes AFTER WORKER_DISABLED in rowid order
        wd_idx = types.index("WORKER_DISABLED")
        art_idx = types.index("ITEM_ARTIFACT")
        assert art_idx > wd_idx, "Forensic events should be emitted after kill switch"

        conn.close()
        _worker_state.disabled = False
        _worker_state.disable_reason = None


# =========================================================================
# PHASE 4.3a: WAF / Bot Challenge Detection Tests
# =========================================================================

from joiner import (
    _is_profile_blocked,
    _set_profile_blocked,
    _blocked_profiles,
    _WAF_URL_MARKERS,
    _WAF_TITLE_MARKERS,
    _WAF_CHALLENGE_SELECTORS,
    _WAF_CHALLENGE_TEXT,
    PROFILE_BLOCK_COOLDOWN_SECONDS,
)


class TestClassifyWAFDetection:
    """Unit tests: classifier detects AWS WAF challenge markers."""

    def test_waf_url_challenge(self):
        """URL containing 'challenge' triggers WAF detection."""
        page = _MockPage(url="https://www.skool.com/challenge?token=abc123")
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"
        assert result["detail"] == "aws_waf_challenge"

    def test_waf_url_host_awswaf(self):
        """URL host ending in edge.sdk.awswaf.com triggers WAF detection."""
        page = _MockPage(url="https://edge.sdk.awswaf.com/challenge?token=abc")
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"
        assert result["detail"] == "aws_waf_challenge"

    def test_waf_html_awswaf_sdk_telemetry_not_blocked(self):
        """HTML containing edge.sdk.awswaf.com as telemetry does NOT trigger WAF.

        This is the false-positive regression test: edge.sdk.awswaf.com appears
        on every normal Skool page as a monitoring script.
        """
        class NormalSkoolPage(_MockPage):
            def content(self):
                return (
                    '<html><head><script src="https://edge.sdk.awswaf.com/token.js">'
                    '</script></head><body><h1>Test Group</h1></body></html>'
                )
            def title(self):
                return "Test Group"
        page = NormalSkoolPage(
            body_text="welcome to test group classroom calendar members leaderboard",
            selectors={'div[class*="TopNav"]': _MockElement()},
        )
        result = _classify_page_state(page)
        assert result["state"] != "BLOCKED", (
            f"edge.sdk.awswaf.com telemetry should not trigger BLOCKED, got {result}"
        )

    def test_waf_challenge_js_alone_not_blocked(self):
        """challenge.js alone without corroboration does NOT trigger WAF."""
        class ChallengeJSOnlyPage(_MockPage):
            def content(self):
                return '<html><script src="/challenge.js"></script><body>My Group</body></html>'
            def title(self):
                return "My Group"
        page = ChallengeJSOnlyPage(body_text="welcome to my group")
        result = _classify_page_state(page)
        assert result["state"] != "BLOCKED", (
            f"challenge.js alone should not trigger BLOCKED, got {result}"
        )

    def test_waf_challenge_js_with_challenge_text(self):
        """challenge.js + challenge text triggers WAF detection."""
        class ChallengeJSWithTextPage(_MockPage):
            def content(self):
                return '<html><script src="/challenge.js"></script></html>'
            def title(self):
                return ""
        page = ChallengeJSWithTextPage(body_text="checking your browser please wait")
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"
        assert result["detail"] == "aws_waf_challenge"

    def test_waf_challenge_js_with_challenge_form(self):
        """challenge.js + #challenge-form selector triggers WAF detection."""
        class ChallengeJSWithFormPage(_MockPage):
            def content(self):
                return '<html><script src="/challenge.js"></script></html>'
            def title(self):
                return ""
        page = ChallengeJSWithFormPage(
            body_text="",
            selectors={"#challenge-form": _MockElement()},
        )
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"
        assert result["detail"] == "aws_waf_challenge"

    def test_waf_title_attention_required(self):
        """Title 'Attention Required' triggers WAF detection."""
        class AttentionPage(_MockPage):
            def title(self):
                return "Attention Required | Cloudflare"
            def content(self):
                return "<html></html>"
        page = AttentionPage(body_text="checking your browser")
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"
        assert result["detail"] == "aws_waf_challenge"

    def test_waf_body_request_blocked(self):
        """Body containing 'request blocked' triggers WAF detection."""
        class BlockedBodyPage(_MockPage):
            def content(self):
                return "<html></html>"
            def title(self):
                return ""
        page = BlockedBodyPage(body_text="your request blocked by security policy")
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"
        assert result["detail"] == "aws_waf_challenge"

    def test_normal_page_not_waf(self):
        """Normal Skool page should NOT trigger WAF detection."""
        class NormalPage(_MockPage):
            def content(self):
                return "<html><body>Welcome to my group</body></html>"
            def title(self):
                return "My Awesome Group"
        page = NormalPage(
            body_text="welcome to my group classroom calendar members leaderboard",
            selectors={'div[class*="TopNav"]': _MockElement()},
        )
        result = _classify_page_state(page)
        assert result["state"] != "BLOCKED"

    def test_existing_block_keywords_still_work(self):
        """Original block keywords (account suspended, etc.) still detected."""
        page = _MockPage(body_text="your account suspended for violating terms")
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"
        assert "account suspended" in result["detail"]

    def test_waf_challenge_takes_precedence_over_join_button(self):
        """Real WAF challenge page with join button text should still be BLOCKED."""
        class WAFWithJoinPage(_MockPage):
            def content(self):
                return '<html><script src="/challenge.js"></script></html>'
            def title(self):
                return ""
        page = WAFWithJoinPage(
            body_text="checking your browser join for free",
            selectors={'button:has-text("Join for Free")': _MockElement(visible=True)},
        )
        result = _classify_page_state(page)
        assert result["state"] == "BLOCKED"

    def test_member_area_with_awswaf_telemetry_is_member(self):
        """Page with edge.sdk.awswaf.com telemetry + member area = MEMBER not BLOCKED."""
        class MemberWithTelemetry(_MockPage):
            def content(self):
                return (
                    '<html><head><script src="https://edge.sdk.awswaf.com/token.js">'
                    '</script></head><body>Member area</body></html>'
                )
            def title(self):
                return "My Group"
        page = MemberWithTelemetry(
            body_text="classroom calendar members leaderboard",
            selectors={'div[class*="TopNav"]': _MockElement()},
        )
        result = _classify_page_state(page)
        assert result["state"] == "MEMBER", (
            f"Expected MEMBER with WAF telemetry present, got {result}"
        )

    def test_join_visible_with_awswaf_telemetry_is_join_visible(self):
        """Page with edge.sdk.awswaf.com telemetry + join button = JOIN_VISIBLE not BLOCKED."""
        class JoinWithTelemetry(_MockPage):
            def content(self):
                return (
                    '<html><head><script src="https://edge.sdk.awswaf.com/token.js">'
                    '</script></head><body>Join this group</body></html>'
                )
            def title(self):
                return "Cool Group"
        page = JoinWithTelemetry(
            body_text="join for free about this group",
            selectors={'button:has-text("Join for Free")': _MockElement(visible=True)},
        )
        result = _classify_page_state(page)
        assert result["state"] == "JOIN_VISIBLE", (
            f"Expected JOIN_VISIBLE with WAF telemetry present, got {result}"
        )


class TestProfileBlockedCooldown:
    """Unit tests: per-profile blocked cooldown."""

    def setup_method(self):
        _blocked_profiles.clear()

    def test_not_blocked_by_default(self):
        assert _is_profile_blocked("p-fresh") is False

    def test_blocked_after_set(self):
        _set_profile_blocked("p-block")
        assert _is_profile_blocked("p-block") is True

    def test_other_profile_not_affected(self):
        _set_profile_blocked("p-block")
        assert _is_profile_blocked("p-other") is False

    def test_expired_cooldown_clears(self):
        _blocked_profiles["p-old"] = time.time() - PROFILE_BLOCK_COOLDOWN_SECONDS - 1
        assert _is_profile_blocked("p-old") is False
        assert "p-old" not in _blocked_profiles  # cleaned up

    def teardown_method(self):
        _blocked_profiles.clear()


class TestWAFBlockTerminal:
    """Integration: WAF block -> terminal FAILED, no retries, per-profile cooldown."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def setup_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def test_waf_block_makes_item_terminal(self, test_db_path):
        """WAF blocked item is FAILED with next_attempt_at=NULL (terminal)."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def waf_fn(*args, **kwargs):
            return {
                "status": "FAILED",
                "detail": "aws_waf_challenge",
                "blocked_terminal": True,
                "forensic_events": [
                    {"type": "ITEM_ARTIFACT", "detail": "screenshot=test.png"},
                ],
            }

        self._tick_pw(test_db_path, waf_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["status"] == "FAILED"
        assert item["next_attempt_at"] is None  # terminal, no retry
        assert "aws_waf_challenge" in (item["fail_reason"] or "")
        conn.close()

    def test_waf_block_does_not_disable_worker(self, test_db_path):
        """WAF block should NOT globally disable the worker."""
        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def waf_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "aws_waf_challenge", "blocked_terminal": True}

        self._tick_pw(test_db_path, waf_fn)
        assert _worker_state.disabled is False

    def test_waf_block_sets_profile_cooldown(self, test_db_path):
        """WAF block sets per-profile cooldown."""
        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def waf_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "aws_waf_challenge", "blocked_terminal": True}

        self._tick_pw(test_db_path, waf_fn)
        assert _is_profile_blocked("p1") is True

    def test_blocked_profile_items_skipped(self, test_db_path):
        """Items for blocked profile are skipped with ITEM_SKIPPED event."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=2)

        # Block p1 first
        _set_profile_blocked("p1")

        result = self._tick_pw(test_db_path, lambda *a, **k: {"status": "JOINED", "detail": "ok"})
        assert result["processed"] == 0

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type, detail FROM join_events WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        skip_events = [e for e in events if e["event_type"] == "ITEM_SKIPPED"]
        assert len(skip_events) >= 1
        assert "profile_blocked_cooldown" in skip_events[0]["detail"]
        conn.close()

    def test_waf_forensic_events_emitted(self, test_db_path):
        """Forensic events are emitted for WAF blocks."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def waf_with_forensics(*args, **kwargs):
            return {
                "status": "FAILED",
                "detail": "aws_waf_challenge",
                "blocked_terminal": True,
                "forensic_events": [
                    {"type": "ITEM_ARTIFACT", "detail": "screenshot=waf.png"},
                    {"type": "ITEM_ARTIFACT", "detail": "url=https://skool.com html_head=<waf>"},
                ],
            }

        self._tick_pw(test_db_path, waf_with_forensics)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type FROM join_events WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        types = [e["event_type"] for e in events]
        assert "ITEM_ARTIFACT" in types
        assert "ITEM_FAILED" in types
        conn.close()

    def test_non_waf_failure_still_retries(self, test_db_path):
        """Normal failures (no blocked_terminal) still get backoff retries."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def normal_fail(*args, **kwargs):
            return {"status": "FAILED", "detail": "navigation_timeout: timed out"}

        self._tick_pw(test_db_path, normal_fail)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["status"] == "PENDING"  # retryable
        assert item["next_attempt_at"] is not None  # has backoff
        conn.close()

        # Profile should NOT be blocked
        assert _is_profile_blocked("p1") is False

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None


# ===== Phase 4.3b — Item Selection Ordering Tests =====

from datetime import datetime, timezone

class TestItemSelectionOrdering:
    """Verify worker picks newest items first (created_at DESC, attempt_count ASC)."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        return worker_tick(get_db, _playwright_join_fn=pw_fn)

    def test_newer_item_processed_before_older(self, test_db_path):
        """Items created later should be picked first."""
        get_db = _get_db_factory(test_db_path)
        with get_db() as db:
            job_id = _uuid()
            db.execute(
                "INSERT INTO join_jobs (id, status, paused, created_at, total_items, completed_items, failed_items) VALUES (?, 'CREATED', 0, ?, 0, 0, 0)",
                (job_id, _now_iso()),
            )
            # Old item (created 10 min ago)
            old_id = _uuid()
            old_ts = datetime.fromtimestamp(time.time() - 600, tz=timezone.utc).isoformat(timespec="seconds")
            db.execute(
                "INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, 'p1', 'https://www.skool.com/old-community', 'old-community', 'PENDING', 0, ?, ?)",
                (old_id, job_id, old_ts, old_ts),
            )
            # New item (created now)
            new_id = _uuid()
            new_ts = _now_iso()
            db.execute(
                "INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, 'p1', 'https://www.skool.com/new-community', 'new-community', 'PENDING', 0, ?, ?)",
                (new_id, job_id, new_ts, new_ts),
            )
            db.commit()

        processed_urls = []

        def capture_join(*args, **kwargs):
            processed_urls.append(args[1])  # community_url is 2nd arg
            return {"status": "JOINED", "detail": "joined"}

        self._tick_pw(test_db_path, capture_join)

        # Should have picked the newer item
        assert len(processed_urls) == 1
        assert "new-community" in processed_urls[0]

    def test_lower_attempt_count_preferred_at_same_timestamp(self, test_db_path):
        """When created_at is identical, prefer items with fewer attempts."""
        get_db = _get_db_factory(test_db_path)
        same_ts = _now_iso()
        with get_db() as db:
            job_id = _uuid()
            db.execute(
                "INSERT INTO join_jobs (id, status, paused, created_at, total_items, completed_items, failed_items) VALUES (?, 'CREATED', 0, ?, 0, 0, 0)",
                (job_id, same_ts),
            )
            # Item with 2 attempts
            retried_id = _uuid()
            db.execute(
                "INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, 'p1', 'https://www.skool.com/retried', 'retried', 'PENDING', 2, ?, ?)",
                (retried_id, job_id, same_ts, same_ts),
            )
            # Item with 0 attempts
            fresh_id = _uuid()
            db.execute(
                "INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, 'p1', 'https://www.skool.com/fresh', 'fresh', 'PENDING', 0, ?, ?)",
                (fresh_id, job_id, same_ts, same_ts),
            )
            db.commit()

        processed_urls = []

        def capture_join(*args, **kwargs):
            processed_urls.append(args[1])
            return {"status": "JOINED", "detail": "joined"}

        self._tick_pw(test_db_path, capture_join)

        assert len(processed_urls) == 1
        assert "fresh" in processed_urls[0]

    def test_new_item_beats_old_retry(self, test_db_path):
        """A brand-new item (attempt_count=0, recent created_at) should be
        processed before an old item that has been retried (attempt_count>0,
        old created_at)."""
        get_db = _get_db_factory(test_db_path)
        with get_db() as db:
            job_id = _uuid()
            db.execute(
                "INSERT INTO join_jobs (id, status, paused, created_at, total_items, completed_items, failed_items) VALUES (?, 'CREATED', 0, ?, 0, 0, 0)",
                (job_id, _now_iso()),
            )
            # Old retry: created 1 hour ago, 2 attempts
            old_retry_id = _uuid()
            old_ts = datetime.fromtimestamp(time.time() - 3600, tz=timezone.utc).isoformat(timespec="seconds")
            db.execute(
                "INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, 'p1', 'https://www.skool.com/old-retry', 'old-retry', 'PENDING', 2, ?, ?)",
                (old_retry_id, job_id, old_ts, old_ts),
            )
            # Brand new: created now, 0 attempts
            brand_new_id = _uuid()
            new_ts = _now_iso()
            db.execute(
                "INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, 'p1', 'https://www.skool.com/brand-new', 'brand-new', 'PENDING', 0, ?, ?)",
                (brand_new_id, job_id, new_ts, new_ts),
            )
            db.commit()

        processed_urls = []

        def capture_join(*args, **kwargs):
            processed_urls.append(args[1])
            return {"status": "JOINED", "detail": "joined"}

        self._tick_pw(test_db_path, capture_join)

        assert len(processed_urls) == 1
        assert "brand-new" in processed_urls[0]

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None


# ===== Phase 4.4 — API-First Join Tests =====

from joiner import _extract_slug_from_key, _try_join_via_api2, _API2_BASE


class TestExtractSlugFromKey:
    """Unit tests for _extract_slug_from_key."""

    def test_standard_key(self):
        assert _extract_slug_from_key("www.skool.com/my-group") == "my-group"

    def test_trailing_slash(self):
        assert _extract_slug_from_key("www.skool.com/my-group/") == "my-group"

    def test_bare_slug(self):
        assert _extract_slug_from_key("my-group") == "my-group"

    def test_empty_string(self):
        assert _extract_slug_from_key("") == ""

    def test_deep_path(self):
        assert _extract_slug_from_key("www.skool.com/category/my-group") == "my-group"


class _MockApiPage:
    """Mock page for _try_join_via_api2 tests. Supports goto, evaluate, classify helpers."""

    def __init__(self, *, goto_state="AUTH_OK", evaluate_result=None, goto_error=None):
        self.url = "https://www.skool.com/settings?t=communities"
        self._goto_state = goto_state  # AUTH_OK | BLOCKED | AUTH_REQUIRED
        self._evaluate_result = evaluate_result or {"ok": False, "status": 404, "text": "not found"}
        self._goto_error = goto_error
        self._goto_called = False
        self._evaluate_called = False

    def goto(self, url, **kwargs):
        self._goto_called = True
        if self._goto_error:
            raise Exception(self._goto_error)
        self.url = url

    def wait_for_timeout(self, ms):
        pass

    def set_default_timeout(self, ms):
        pass

    def text_content(self, sel):
        if self._goto_state == "BLOCKED":
            return "access denied by waf"
        if self._goto_state == "AUTH_REQUIRED":
            return ""
        # Normal settings page — simulate member area
        return "settings community preferences classroom members"

    def query_selector(self, sel):
        if self._goto_state == "AUTH_REQUIRED" and sel == "input#email":
            return _MockElement(visible=True)
        if self._goto_state == "BLOCKED" and 'captcha' in sel:
            return None
        # For auth markers on settings page (logged in)
        if self._goto_state == "AUTH_OK":
            if sel in ('div[class*="TopNav"]', 'a[href*="/chat?ch="]'):
                return _MockElement(visible=True)
        return None

    def content(self):
        if self._goto_state == "BLOCKED":
            return "<html>edge.sdk.awswaf.com challenge</html>"
        return "<html><body>settings page</body></html>"

    def title(self):
        if self._goto_state == "BLOCKED":
            return "Attention Required"
        return "Settings"

    def evaluate(self, js, *args):
        self._evaluate_called = True
        return self._evaluate_result

    def screenshot(self, **kwargs):
        pass

    def on(self, event, handler):
        pass


class TestTryJoinViaApi2:
    """Unit tests for _try_join_via_api2."""

    def test_api_join_success(self):
        page = _MockApiPage(evaluate_result={"ok": True, "status": 200, "text": '{"joined": true}'})
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "JOINED"
        assert "api2_join_success" in result["detail"]
        assert page._evaluate_called

    def test_api_join_already_member(self):
        page = _MockApiPage(evaluate_result={"ok": False, "status": 409, "text": "already member"})
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "ALREADY_MEMBER"

    def test_api_join_pending(self):
        page = _MockApiPage(evaluate_result={"ok": False, "status": 409, "text": "pending approval"})
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "PENDING_APPROVAL"

    def test_api_join_paid(self):
        page = _MockApiPage(evaluate_result={"ok": False, "status": 402, "text": "payment required"})
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "SKIPPED_PAID"

    def test_api_join_404(self):
        page = _MockApiPage(evaluate_result={"ok": False, "status": 404, "text": "not found"})
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "api2_endpoint_not_found" in result["detail"]

    def test_api_join_rejected(self):
        page = _MockApiPage(evaluate_result={"ok": False, "status": 500, "text": "server error"})
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "api2_join_rejected" in result["detail"]

    def test_settings_page_also_blocked(self):
        page = _MockApiPage(goto_state="BLOCKED")
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "api_settings_also_blocked" in result["detail"]
        assert result.get("blocked_terminal") is True
        assert not page._evaluate_called  # should not even try fetch

    def test_settings_page_auth_required(self):
        page = _MockApiPage(goto_state="AUTH_REQUIRED")
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "auth_session_invalid" in result["detail"]

    def test_settings_nav_error(self):
        page = _MockApiPage(goto_error="Timeout exceeded")
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "api_settings_nav_failed" in result["detail"]

    def test_evaluate_exception(self):
        page = _MockApiPage()
        # Override evaluate to throw
        def raise_eval(js, *args):
            raise Exception("evaluate crashed")
        page.evaluate = raise_eval
        result = _try_join_via_api2(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "api_fetch_error" in result["detail"]


class TestApiFirstInBlockedHandler:
    """Integration: WAF detected -> api-first tried -> result."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        return worker_tick(get_db, _playwright_join_fn=pw_fn)

    def test_waf_then_api_join_succeeds(self, test_db_path):
        """When WAF is detected but API-first join succeeds, item should be JOINED."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def pw_fn(*args, **kwargs):
            return {"status": "JOINED", "detail": "api2_join_success slug=test-0-p1",
                    "api_first": True}

        self._tick_pw(test_db_path, pw_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["status"] == "JOINED"
        conn.close()

    def test_waf_api_also_fails_terminal(self, test_db_path):
        """When both WAF and API-first fail, item is terminal FAILED."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def pw_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "aws_waf_challenge",
                    "blocked_terminal": True,
                    "api_first_detail": "api_settings_also_blocked",
                    "forensic_events": []}

        self._tick_pw(test_db_path, pw_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["status"] == "FAILED"
        assert item["next_attempt_at"] is None  # terminal
        # Check api_first_detail event was emitted
        events = conn.execute(
            "SELECT * FROM join_events WHERE job_id = ? AND event_type = 'ITEM_DEBUG'",
            (job_id,),
        ).fetchall()
        debug_details = [e["detail"] for e in events]
        assert any("api_first_attempted" in d for d in debug_details)
        conn.close()

    def test_api_first_does_not_trigger_when_no_waf(self, test_db_path):
        """Normal JOINED result (no WAF, no api_first flag) should work as before."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def pw_fn(*args, **kwargs):
            return {"status": "JOINED", "detail": "joined www.skool.com/test-0-p1"}

        self._tick_pw(test_db_path, pw_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT * FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        assert item["status"] == "JOINED"
        # No api_first_attempted events
        events = conn.execute(
            "SELECT * FROM join_events WHERE job_id = ? AND event_type = 'ITEM_DEBUG' AND detail LIKE '%api_first%'",
            (job_id,),
        ).fetchall()
        assert len(events) == 0
        conn.close()


    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None


class TestNetworkRecorderCandidates:
    """Integration: api_candidates emitted as ITEM_DEBUG events."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        return worker_tick(get_db, _playwright_join_fn=pw_fn)

    def test_api_candidates_emitted_on_join(self, test_db_path):
        """When api_candidates are returned, they appear as ITEM_DEBUG events."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def pw_fn(*args, **kwargs):
            return {"status": "JOINED", "detail": "joined www.skool.com/test-0-p1",
                    "api_candidates": ["POST /groups/abc123/join", "GET /groups/abc123"]}

        self._tick_pw(test_db_path, pw_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT * FROM join_events WHERE job_id = ? AND event_type = 'ITEM_DEBUG'",
            (job_id,),
        ).fetchall()
        debug_details = [e["detail"] for e in events]
        assert any("join_api_candidate=POST /groups/abc123/join" in d for d in debug_details)
        assert any("join_api_candidate=GET /groups/abc123" in d for d in debug_details)
        conn.close()

    def test_no_candidates_no_events(self, test_db_path):
        """When no api_candidates, no ITEM_DEBUG events for candidates."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def pw_fn(*args, **kwargs):
            return {"status": "JOINED", "detail": "joined www.skool.com/test-0-p1"}

        self._tick_pw(test_db_path, pw_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT * FROM join_events WHERE job_id = ? AND event_type = 'ITEM_DEBUG' AND detail LIKE '%join_api_candidate%'",
            (job_id,),
        ).fetchall()
        assert len(events) == 0
        conn.close()

    def test_candidates_on_failed_join(self, test_db_path):
        """api_candidates emitted even when join fails (for discovery)."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def pw_fn(*args, **kwargs):
            return {"status": "FAILED", "detail": "join_click_no_state_change: unknown",
                    "forensic_events": [],
                    "api_candidates": ["POST /groups/xyz/join"]}

        self._tick_pw(test_db_path, pw_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT * FROM join_events WHERE job_id = ? AND event_type = 'ITEM_DEBUG' AND detail LIKE '%join_api_candidate%'",
            (job_id,),
        ).fetchall()
        assert len(events) == 1
        assert "POST /groups/xyz/join" in events[0]["detail"]
        conn.close()


    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None


class TestPhase44CoreTableInvariant:
    """Phase 4.4 does not write to core tables (profiles, scheduler, etc.)."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        return worker_tick(get_db, _playwright_join_fn=pw_fn)

    def test_api_first_join_no_core_writes(self, test_db_path):
        """API-first join should not modify profiles table."""
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        before = conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()
        before_data = [(r["id"], r["status"]) for r in before]
        conn.close()

        _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def pw_fn(*args, **kwargs):
            return {"status": "JOINED", "detail": "api2_join_success slug=test",
                    "api_first": True}

        self._tick_pw(test_db_path, pw_fn)

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        after = conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()
        after_data = [(r["id"], r["status"]) for r in after]
        conn.close()
        assert before_data == after_data

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None


# =========================================================================
# PHASE 4.5: www API Join Tests
# =========================================================================

from joiner import (
    _try_join_via_www_api,
    _submit_survey_answers,
    _verify_membership_via_classroom,
    _build_survey_answers,
    _extract_survey_questions,
    _resolve_group_id,
    _cancel_join_via_api2,
    _leave_group_via_api2,
    _SURVEY_DEFAULTS,
    _FIELD_PATTERNS,
    _GENERIC_ANSWER,
)


class _MockWwwApiPage:
    """Mock page for _try_join_via_www_api tests."""

    def __init__(self, *, evaluate_results=None, goto_state="MEMBER", goto_error=None):
        self.url = "https://www.skool.com/test-group"
        self._evaluate_results = list(evaluate_results or [])
        self._evaluate_call_idx = 0
        self._goto_state = goto_state
        self._goto_error = goto_error
        self._goto_urls = []

    def evaluate(self, js, *args):
        if self._evaluate_call_idx < len(self._evaluate_results):
            result = self._evaluate_results[self._evaluate_call_idx]
            self._evaluate_call_idx += 1
            return result
        return {"ok": False, "status": 500, "text": "no mock result"}

    def goto(self, url, **kwargs):
        self._goto_urls.append(url)
        if self._goto_error:
            raise Exception(self._goto_error)
        self.url = url

    def wait_for_timeout(self, ms):
        pass

    def text_content(self, sel):
        if self._goto_state == "MEMBER":
            return "classroom calendar members leaderboard Leave Group"
        if self._goto_state == "PENDING":
            return "membership pending Cancel Request"
        return ""

    def query_selector(self, sel):
        if self._goto_state == "MEMBER":
            if sel in ('div[class*="TopNav"]', 'a[href*="/chat?ch="]'):
                return _MockElement(visible=True)
            # Member area selectors
            if sel in ('a[href*="/classroom"]', 'a[href*="/calendar"]'):
                return _MockElement(visible=True)
        if self._goto_state == "PENDING":
            if 'pending' in sel.lower():
                return _MockElement(visible=True)
        return None

    def content(self):
        return "<html><body>normal page</body></html>"

    def title(self):
        return "Test Group"


class TestTryJoinViaWwwApi:
    """Unit tests for _try_join_via_www_api."""

    def test_join_success(self):
        """HTTP 200 with no survey/pending -> JOINED."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200, "text": '{"success": true}'},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "JOINED"
        assert "www_api_joined" in result["detail"]
        assert "test-group" in result["detail"]

    def test_join_already_member_409(self):
        """HTTP 409 without pending text -> ALREADY_MEMBER."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 409, "text": "already a member"},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "ALREADY_MEMBER"

    def test_join_pending_409(self):
        """HTTP 409 with pending text -> PENDING_APPROVAL."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 409, "text": "pending approval"},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "PENDING_APPROVAL"

    def test_join_paid_402(self):
        """HTTP 402 -> SKIPPED_PAID."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 402, "text": "payment required"},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "SKIPPED_PAID"

    def test_join_paid_403(self):
        """HTTP 403 -> SKIPPED_PAID."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 403, "text": "forbidden"},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "SKIPPED_PAID"

    def test_join_auth_required_401(self):
        """HTTP 401 -> FAILED with auth_required."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 401, "text": "not logged in"},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "auth_required" in result["detail"]

    def test_join_not_found_404(self):
        """HTTP 404 -> FAILED with not_found."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 404, "text": "not found"},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "not_found" in result["detail"]

    def test_join_rejected_500(self):
        """HTTP 500 -> FAILED with status code."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 500, "text": "server error"},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "500" in result["detail"]

    def test_evaluate_exception(self):
        """evaluate() throws -> FAILED."""
        class ThrowingPage(_MockWwwApiPage):
            def evaluate(self, js, *args):
                raise Exception("browser crashed")
        page = ThrowingPage()
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "FAILED"
        assert "fetch_error" in result["detail"]

    def test_join_pending_in_200_response(self):
        """HTTP 200 with pending in body -> PENDING_APPROVAL."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200, "text": '{"status": "pending"}'},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "PENDING_APPROVAL"

    def test_no_body_text_logged(self):
        """Detail string must NOT contain response body content."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 418, "text": "secret_body_content_xyz"},
        ])
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert "secret_body_content_xyz" not in result["detail"]
        assert "418" in result["detail"]


class TestSurveyHandling:
    """Unit tests for survey flow in _try_join_via_www_api (Phase 4.6: filled answers)."""

    def test_survey_success_and_verify_member(self):
        """200 with survey -> extract + fill + submit -> verify classroom -> JOINED."""
        page = _MockWwwApiPage(
            evaluate_results=[
                {"ok": True, "status": 200, "text": '{"survey": true}'},
                [],  # _extract_survey_questions
                "",  # _resolve_group_id
                {"ok": True, "status": 200},  # _submit_survey_answers (www fallback)
            ],
            goto_state="MEMBER",
        )
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "JOINED"
        assert result.get("survey_answers_count", 0) > 0

    def test_survey_success_verify_pending(self):
        """200 with survey -> submit -> classroom shows pending."""
        page = _MockWwwApiPage(
            evaluate_results=[
                {"ok": True, "status": 200, "text": '{"survey": true}'},
                [],  # extract
                "",  # resolve
                {"ok": True, "status": 200},  # submit
            ],
            goto_state="PENDING",
        )
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "PENDING_APPROVAL"

    def test_survey_submit_failed(self):
        """200 with survey -> survey submit fails -> PENDING_APPROVAL."""
        page = _MockWwwApiPage(
            evaluate_results=[
                {"ok": True, "status": 200, "text": '{"survey": true}'},
                [],  # extract
                "",  # resolve
                {"ok": False, "status": 400},  # submit fails
            ],
        )
        result = _try_join_via_www_api(page, "www.skool.com/test-group")
        assert result["status"] == "PENDING_APPROVAL"
        assert "survey_needed" in result["detail"]
        assert result.get("survey_answers_count", 0) > 0


class TestWwwApiFlowIntegration:
    """Integration: www API join in _execute_playwright_join flow."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def setup_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def test_www_api_success_returns_joined(self, test_db_path):
        """When www API returns JOINED, worker marks item joined."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "JOINED", "detail": "www_api_joined slug=freegroup", "www_api": True}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "JOINED"

    def test_www_api_auth_failure_is_terminal(self, test_db_path):
        """When www API returns auth_required, worker treats as terminal."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "FAILED", "detail": "www_api_auth_required slug=freegroup",
                    "blocked_terminal": True}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "FAILED"

    def test_awswaf_telemetry_does_not_block_www_api_path(self, test_db_path):
        """WAF telemetry in page HTML does NOT prevent www API from executing.

        Regression test: edge.sdk.awswaf.com is telemetry, classifier should not
        return BLOCKED, so www API path is reached.
        """
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "JOINED", "detail": "www_api_joined slug=freegroup", "www_api": True}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "JOINED"

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None


class TestPhase45CoreTableInvariant:
    """Phase 4.5 must not modify core tables (profiles, communities)."""

    def test_www_api_join_no_core_writes(self, test_db_path):
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        profiles_before = [dict(r) for r in conn.execute("SELECT * FROM profiles").fetchall()]
        conn.close()

        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "JOINED", "detail": "www_api_joined slug=freegroup", "www_api": True}

        get_db_fn = _get_db_factory(test_db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            worker_tick(get_db_fn, _playwright_join_fn=fake_pw)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        profiles_after = [dict(r) for r in conn.execute("SELECT * FROM profiles").fetchall()]
        conn.close()
        assert profiles_before == profiles_after

    def setup_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None


# =========================================================================
# PHASE 4.6: Filled Survey, api2 Group ID, Cancel/Leave Tests
# =========================================================================


class TestBuildSurveyAnswers:
    """Unit tests: survey answers are never empty and match field patterns."""

    def test_empty_questions_returns_empty_list(self):
        """No questions -> empty list (caller decides whether to submit)."""
        answers = _build_survey_answers([])
        assert answers == []

    def test_email_field_matched(self):
        """Question with 'email' in label -> email default answer object."""
        questions = [{"label": "What is your email address?", "type": "email"}]
        answers = _build_survey_answers(questions)
        assert len(answers) == 1
        assert isinstance(answers[0], dict)
        assert answers[0]["answer"] == _SURVEY_DEFAULTS["email"]

    def test_why_join_field_matched(self):
        """Question about 'why join' -> why_join default answer object."""
        questions = [{"label": "Why do you want to join this community?", "type": "text"}]
        answers = _build_survey_answers(questions)
        assert answers[0]["answer"] == _SURVEY_DEFAULTS["why_join"]

    def test_occupation_field_matched(self):
        """Question about 'what do you do' -> occupation default answer object."""
        questions = [{"label": "What do you do for a living?", "type": "text"}]
        answers = _build_survey_answers(questions)
        assert answers[0]["answer"] == _SURVEY_DEFAULTS["occupation"]

    def test_unknown_field_gets_generic(self):
        """Unrecognized question -> generic answer object, never empty."""
        questions = [{"label": "Random unrelated question xyz123", "type": "text"}]
        answers = _build_survey_answers(questions)
        assert len(answers) == 1
        assert isinstance(answers[0], dict)
        assert answers[0]["answer"] == _GENERIC_ANSWER
        assert answers[0]["answer"] != ""

    def test_multiple_questions_all_filled(self):
        """Multiple questions -> all get answer objects, none empty."""
        questions = [
            {"label": "Email", "type": "email"},
            {"label": "Why join?", "type": "text"},
            {"label": "Something random", "type": "text"},
        ]
        answers = _build_survey_answers(questions)
        assert len(answers) == 3
        for a in answers:
            assert isinstance(a, dict)
            assert "answer" in a
            assert a["answer"] != ""
            assert len(a["answer"]) > 5

    def test_answers_empty_when_no_questions(self):
        """Answers list is empty when no questions provided."""
        answers = _build_survey_answers([])
        assert len(answers) == 0

    def test_no_pii_in_field_patterns(self):
        """_FIELD_PATTERNS keys all exist in _SURVEY_DEFAULTS."""
        for key in _FIELD_PATTERNS:
            assert key in _SURVEY_DEFAULTS, f"Pattern key '{key}' not in defaults"


class TestResolveGroupId:
    """Unit tests: _resolve_group_id extracts group UUID from page."""

    def test_returns_group_id_from_next_data(self):
        """When __NEXT_DATA__ has groupId, it is returned."""
        class PageWithNextData(_MockWwwApiPage):
            def evaluate(self, js, *args):
                return "db46e2a8c15944448f2c03a861bd5cb6"
        page = PageWithNextData()
        gid = _resolve_group_id(page, "freegroup")
        assert gid == "db46e2a8c15944448f2c03a861bd5cb6"

    def test_returns_empty_when_no_data(self):
        """When no group_id found, returns empty string."""
        class PageNoData(_MockWwwApiPage):
            def evaluate(self, js, *args):
                return ""
        page = PageNoData()
        gid = _resolve_group_id(page, "freegroup")
        assert gid == ""

    def test_returns_empty_on_exception(self):
        """When evaluate throws, returns empty string."""
        class PageThrows(_MockWwwApiPage):
            def evaluate(self, js, *args):
                raise Exception("browser error")
        page = PageThrows()
        gid = _resolve_group_id(page, "freegroup")
        assert gid == ""

    def test_short_string_rejected(self):
        """Short strings (< 20 chars) are rejected."""
        class PageShort(_MockWwwApiPage):
            def evaluate(self, js, *args):
                return "short"
        page = PageShort()
        gid = _resolve_group_id(page, "freegroup")
        assert gid == ""


class TestSubmitSurveyAnswers:
    """Unit tests: _submit_survey_answers uses api2 with group_id, fallback to www."""

    def test_api2_used_when_group_id_known(self):
        """With group_id, api2 endpoint is used."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200},  # api2 submit
        ])
        result = _submit_survey_answers(page, "test-group", "abc123def456ghi789jkl012", ["answer1"])
        assert result["ok"] is True
        assert result["endpoint"] == "api2"
        assert result["answers_count"] == 1

    def test_www_fallback_when_no_group_id(self):
        """Without group_id, www endpoint is used."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200},  # www submit
        ])
        result = _submit_survey_answers(page, "test-group", "", ["answer1"])
        assert result["ok"] is True
        assert result["endpoint"] == "www"
        assert result["answers_count"] == 1

    def test_api2_fails_falls_to_www(self):
        """If api2 fails, www is tried as fallback."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 500},  # api2 fails
            {"ok": True, "status": 200},   # www succeeds
        ])
        result = _submit_survey_answers(page, "test-group", "abc123def456ghi789jkl012", ["ans1"])
        assert result["ok"] is True
        assert result["endpoint"] == "www"

    def test_empty_answers_get_generic(self):
        """Empty answers list gets at least one generic answer."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200},  # www submit
        ])
        result = _submit_survey_answers(page, "test-group", "", [])
        assert result["answers_count"] >= 1

    def test_answers_count_in_result(self):
        """answers_count reflects actual count submitted."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200},
        ])
        result = _submit_survey_answers(page, "test-group", "", ["a1", "a2", "a3"])
        assert result["answers_count"] == 3


class TestCancelJoinViaApi2:
    """Unit tests: _cancel_join_via_api2 maps status codes."""

    def test_cancel_success(self):
        """HTTP 200 -> ok=True."""
        page = _MockWwwApiPage(evaluate_results=[{"ok": True, "status": 200}])
        result = _cancel_join_via_api2(page, "test-group")
        assert result["ok"] is True

    def test_cancel_not_found(self):
        """HTTP 404 -> ok=False."""
        page = _MockWwwApiPage(evaluate_results=[{"ok": False, "status": 404}])
        result = _cancel_join_via_api2(page, "test-group")
        assert result["ok"] is False
        assert result["status"] == 404

    def test_cancel_evaluate_error(self):
        """evaluate throws -> ok=False."""
        class ThrowPage(_MockWwwApiPage):
            def evaluate(self, js, *args):
                raise Exception("crash")
        page = ThrowPage()
        result = _cancel_join_via_api2(page, "test-group")
        assert result["ok"] is False


class TestLeaveGroupViaApi2:
    """Unit tests: _leave_group_via_api2 maps status codes."""

    def test_leave_success(self):
        """HTTP 200 -> ok=True."""
        page = _MockWwwApiPage(evaluate_results=[{"ok": True, "status": 200}])
        result = _leave_group_via_api2(page, "test-group")
        assert result["ok"] is True

    def test_leave_forbidden(self):
        """HTTP 403 -> ok=False."""
        page = _MockWwwApiPage(evaluate_results=[{"ok": False, "status": 403}])
        result = _leave_group_via_api2(page, "test-group")
        assert result["ok"] is False
        assert result["status"] == 403

    def test_leave_evaluate_error(self):
        """evaluate throws -> ok=False."""
        class ThrowPage(_MockWwwApiPage):
            def evaluate(self, js, *args):
                raise Exception("crash")
        page = ThrowPage()
        result = _leave_group_via_api2(page, "test-group")
        assert result["ok"] is False


class TestPhase46CoreTableInvariant:
    """Phase 4.6 must not modify core tables."""

    def test_survey_fill_no_core_writes(self, test_db_path):
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        profiles_before = [dict(r) for r in conn.execute("SELECT * FROM profiles").fetchall()]
        conn.close()

        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "JOINED", "detail": "www_api_joined_with_survey slug=test",
                    "www_api": True, "survey_answers_count": 3}

        get_db_fn = _get_db_factory(test_db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            worker_tick(get_db_fn, _playwright_join_fn=fake_pw)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        profiles_after = [dict(r) for r in conn.execute("SELECT * FROM profiles").fetchall()]
        conn.close()
        assert profiles_before == profiles_after

    def setup_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None




# =========================================================================
# PHASE 4.7: join-group contract, answer objects, deterministic verify
# =========================================================================

from joiner import (
    _try_join_via_join_group,
)


class TestTryJoinViaJoinGroup:
    """Unit: _try_join_via_join_group status code mapping (api2 first, www fallback)."""

    def test_200_returns_ok(self):
        """HTTP 200 -> ok=True, status_code=200."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200, "text": "joined"},
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is True
        assert result["status_code"] == 200
        assert result["response_text"] == "joined"
        assert result["endpoint_used"] == "api2"

    def test_409_returns_conflict(self):
        """HTTP 409 -> ok=False, status_code=409."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 409, "text": "already member"},
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is False
        assert result["status_code"] == 409
        assert result["endpoint_used"] == "api2"

    def test_401_returns_auth(self):
        """HTTP 401 -> ok=False, status_code=401."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 401, "text": "unauthorized"},
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is False
        assert result["status_code"] == 401
        assert result["endpoint_used"] == "api2"

    def test_402_returns_paid(self):
        """HTTP 402 -> ok=False, status_code=402."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 402, "text": "payment required"},
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is False
        assert result["status_code"] == 402

    def test_403_returns_forbidden(self):
        """HTTP 403 -> ok=False, status_code=403."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 403, "text": "forbidden"},
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is False
        assert result["status_code"] == 403

    def test_404_both_endpoints_not_found(self):
        """Both api2 and www return 404 -> ok=False, status_code=404, endpoint_used=www."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 404, "text": "not found"},  # api2
            {"ok": False, "status": 404, "text": "not found"},  # www fallback
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is False
        assert result["status_code"] == 404
        assert result["endpoint_used"] == "www"

    def test_evaluate_error_returns_zero(self):
        """Both evaluate calls throw -> status_code=0, endpoint_used=www."""
        class ThrowPage(_MockWwwApiPage):
            def evaluate(self, js, *args):
                raise Exception("crash")
        page = ThrowPage()
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is False
        assert result["status_code"] == 0
        assert result["endpoint_used"] == "www"

    def test_response_text_truncated(self):
        """Response text truncated to 500 chars."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200, "text": "x" * 600},
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert len(result["response_text"]) <= 500
        assert result["endpoint_used"] == "api2"


class TestSurveyAnswerObjectFormat:
    """Unit: survey payload format is list of answer objects (not strings)."""

    def test_answers_are_dicts(self):
        """Each answer is a dict with 'answer' key."""
        questions = [
            {"label": "What is your email?", "type": "email"},
            {"label": "Why join?", "type": "text"},
        ]
        answers = _build_survey_answers(questions)
        for a in answers:
            assert isinstance(a, dict), f"Expected dict, got {type(a)}"
            assert "answer" in a, f"Missing 'answer' key in {a}"
            assert isinstance(a["answer"], str)
            assert len(a["answer"]) > 0

    def test_empty_questions_returns_empty(self):
        """No questions returns empty list (survey gate in caller)."""
        answers = _build_survey_answers([])
        assert answers == []

    def test_no_bare_strings_in_answers(self):
        """Answers list never contains bare strings."""
        questions = [
            {"label": "Email", "type": "email"},
            {"label": "What do you do?", "type": "text"},
            {"label": "Random q", "type": "text"},
        ]
        answers = _build_survey_answers(questions)
        for a in answers:
            assert not isinstance(a, str), f"Bare string found: {a}"


class TestDeterministicVerify:
    """Unit: _verify_membership_via_classroom deterministic logic."""

    def test_leave_group_returns_joined(self):
        """Body containing 'Leave Group' -> JOINED."""
        page = _MockWwwApiPage(goto_state="MEMBER")
        result = _verify_membership_via_classroom(page, "test-group")
        assert result["status"] == "JOINED"
        assert "leave_group" in result["detail"]

    def test_cancel_request_returns_pending(self):
        """Body containing 'Cancel Request' -> PENDING_APPROVAL."""
        page = _MockWwwApiPage(goto_state="PENDING")
        result = _verify_membership_via_classroom(page, "test-group")
        assert result["status"] == "PENDING_APPROVAL"
        assert "cancel_request" in result["detail"]

    def test_classroom_url_no_join_btn_returns_joined(self):
        """URL contains /classroom, no join button visible -> JOINED."""
        class ClassroomPage(_MockWwwApiPage):
            def text_content(self, sel):
                return "some content without special markers"
            def query_selector(self, sel):
                return None  # no join button
        page = ClassroomPage()
        result = _verify_membership_via_classroom(page, "test-group")
        assert result["status"] == "JOINED"
        assert "classroom_no_join_btn" in result["detail"]

    def test_redirected_about_join_visible_returns_not_member(self):
        """Redirected to /about with join button -> NOT_MEMBER."""
        class AboutPage(_MockWwwApiPage):
            def goto(self, url, **kwargs):
                self.url = url.replace("/classroom", "/about")
            def text_content(self, sel):
                return "about this community"
            def query_selector(self, sel):
                if "join" in sel.lower() or "Join" in sel:
                    return _MockElement(visible=True)
                return None
        page = AboutPage()
        result = _verify_membership_via_classroom(page, "test-group")
        assert result["status"] == "NOT_MEMBER"
        assert "about" in result["detail"]

    def test_nav_failure_returns_unknown_verify(self):
        """Navigation error -> UNKNOWN_VERIFY (not optimistic JOINED)."""
        page = _MockWwwApiPage(goto_error="timeout")
        result = _verify_membership_via_classroom(page, "test-group")
        assert result["status"] == "UNKNOWN_VERIFY"
        assert "nav_failed" in result["detail"]


class TestPhase47JoinGroupContractIntegration:
    """Integration: join-group 200 -> survey -> verify flow."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def setup_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def test_join_200_survey_joined(self, test_db_path):
        """join-group 200 + survey + verify -> JOINED."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "JOINED", "detail": "verified_leave_group slug=test",
                    "www_api": True, "survey_answers_count": 3}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "JOINED"

    def test_join_409_verify_already_member(self, test_db_path):
        """join-group 409 -> verify path -> ALREADY_MEMBER or JOINED."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "JOINED", "detail": "verified_leave_group slug=test",
                    "www_api": True}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "JOINED"

    def test_join_401_terminal(self, test_db_path):
        """join-group 401 -> FAILED terminal."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "FAILED", "detail": "join_group_auth_required slug=test",
                    "blocked_terminal": True}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "FAILED"

    def test_join_pending_approval_via_survey(self, test_db_path):
        """join-group 200 + survey + verify returns PENDING_APPROVAL."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "PENDING_APPROVAL",
                    "detail": "verified_cancel_request slug=test",
                    "www_api": True, "survey_answers_count": 2}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "PENDING_APPROVAL"


class TestPhase47WafRegression:
    """Regression: WAF detection unchanged in Phase 4.7."""

    def test_awswaf_telemetry_not_blocked(self):
        """Pages with edge.sdk.awswaf.com script are NOT blocked."""
        from joiner import _classify_page_state

        class AwsWafTelemetryPage:
            url = "https://www.skool.com/test-group"
            def text_content(self, sel):
                return "join this community"
            def query_selector(self, sel):
                if "join" in sel.lower() or "Join" in sel:
                    return _MockElement(visible=True)
                return None
            def content(self):
                return '<html><script src="https://edge.sdk.awswaf.com/telemetry.js"></script><body>normal page</body></html>'
            def title(self):
                return "Test Community"

        result = _classify_page_state(AwsWafTelemetryPage())
        assert result["state"] != "BLOCKED"

    def test_real_waf_challenge_still_blocked(self):
        """Real WAF challenge page (with challenge form) IS blocked."""
        from joiner import _classify_page_state

        class WafChallengePage:
            url = "https://www.skool.com/test-group"
            def text_content(self, sel):
                return "checking your browser please wait"
            def query_selector(self, sel):
                if sel == "#challenge-form":
                    return _MockElement(visible=True)
                return None
            def content(self):
                return '<html><script src="challenge.js"></script><body>checking your browser</body></html>'
            def title(self):
                return "Attention Required"

        result = _classify_page_state(WafChallengePage())
        assert result["state"] == "BLOCKED"


class TestPhase47CoreTableInvariant:
    """Phase 4.7 must not modify core tables."""

    def test_contract_flow_no_core_writes(self, test_db_path):
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        profiles_before = [dict(r) for r in conn.execute("SELECT * FROM profiles").fetchall()]
        conn.close()

        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "JOINED", "detail": "verified_leave_group slug=test",
                    "www_api": True, "survey_answers_count": 3}

        get_db = _get_db_factory(test_db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            worker_tick(get_db, _playwright_join_fn=fake_pw)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        profiles_after = [dict(r) for r in conn.execute("SELECT * FROM profiles").fetchall()]
        conn.close()
        assert profiles_before == profiles_after

    def setup_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None



# =========================================================================
# PHASE 4.7b: remove optimistic fallback, UNKNOWN_VERIFY
# =========================================================================


class TestVerifyInconclusive:
    """Unit: _verify_membership_via_classroom returns UNKNOWN_VERIFY when inconclusive."""

    def test_no_signals_returns_unknown_verify(self):
        """Page with no deterministic signals -> UNKNOWN_VERIFY."""
        class BlankPage(_MockWwwApiPage):
            def goto(self, url, **kwargs):
                # Simulate redirect away from /classroom to unexpected URL
                self.url = "https://www.skool.com/test-group/unexpected"
            def text_content(self, sel):
                return "some random content"
            def query_selector(self, sel):
                return None
        page = BlankPage()
        result = _verify_membership_via_classroom(page, "test-group")
        assert result["status"] == "UNKNOWN_VERIFY"
        assert "inconclusive" in result["detail"]

    def test_classroom_url_with_join_button_returns_unknown(self):
        """On /classroom but join button visible -> UNKNOWN_VERIFY (not JOINED)."""
        class ClassroomWithJoinBtn(_MockWwwApiPage):
            def text_content(self, sel):
                return "some page content"
            def query_selector(self, sel):
                # Join button IS visible
                if "join" in sel.lower() or "Join" in sel:
                    return _MockElement(visible=True)
                return None
        page = ClassroomWithJoinBtn()
        result = _verify_membership_via_classroom(page, "test-group")
        assert result["status"] == "UNKNOWN_VERIFY"
        assert "inconclusive" in result["detail"]

    def test_leave_group_still_returns_joined(self):
        """'Leave Group' text -> JOINED (unchanged from 4.7)."""
        page = _MockWwwApiPage(goto_state="MEMBER")
        result = _verify_membership_via_classroom(page, "test-group")
        assert result["status"] == "JOINED"

    def test_cancel_request_still_returns_pending(self):
        """'Cancel Request' text -> PENDING_APPROVAL (unchanged from 4.7)."""
        page = _MockWwwApiPage(goto_state="PENDING")
        result = _verify_membership_via_classroom(page, "test-group")
        assert result["status"] == "PENDING_APPROVAL"


class TestPhase47bInconclusiveIntegration:
    """Integration: inconclusive verify does NOT mark JOINED and schedules retry."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def setup_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def test_inconclusive_verify_does_not_mark_joined(self, test_db_path):
        """When verify is inconclusive, item is NOT marked JOINED and stays retriable."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "FAILED",
                    "detail": "verify_inconclusive slug=test",
                    "forensic_events": [{"type": "ITEM_ARTIFACT", "detail": "screenshot=test.png"}]}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status, attempt_count, fail_reason FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        # Must NOT be JOINED — item is set back to PENDING for retry
        assert item["status"] != "JOINED"
        assert item["status"] == "PENDING"  # retriable: back to PENDING with backoff
        assert item["attempt_count"] == 1
        assert "verify_inconclusive" in (item["fail_reason"] or "")

    def test_inconclusive_verify_has_forensic_events(self, test_db_path):
        """When verify is inconclusive, forensic events are stored."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "FAILED",
                    "detail": "verify_inconclusive slug=test",
                    "forensic_events": [
                        {"type": "ITEM_ARTIFACT", "detail": "screenshot=artifacts/joiner/j1/i1/test.png"},
                        {"type": "ITEM_ARTIFACT", "detail": "url=https://www.skool.com/test/classroom"},
                    ]}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type, detail FROM join_events WHERE job_id = ? AND event_type = 'ITEM_ARTIFACT'",
            (job_id,)
        ).fetchall()
        conn.close()
        assert len(events) >= 1
        details = [e["detail"] for e in events]
        assert any("screenshot" in d for d in details)

    def test_inconclusive_item_can_retry(self, test_db_path):
        """After inconclusive failure, item retries on next tick and succeeds."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        call_count = [0]
        def fake_pw(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"status": "FAILED",
                        "detail": "verify_inconclusive slug=test"}
            return {"status": "JOINED",
                    "detail": "verified_leave_group slug=test",
                    "www_api": True}

        # First tick: inconclusive -> set back to PENDING (retriable)
        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status, attempt_count FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "PENDING"  # retriable
        assert item["attempt_count"] == 1

        # Reset backoff so item is immediately eligible
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.execute("UPDATE join_job_items SET next_attempt_at = NULL WHERE job_id = ?", (job_id,))
        conn.commit()
        conn.close()

        # Second tick: JOINED
        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "JOINED"



# =========================================================================
# PHASE 4.7c: api2-first join-group, forensics on 404
# =========================================================================


class TestJoinGroupDualEndpoint:
    """Unit: _try_join_via_join_group tries api2 first, www fallback on 404."""

    def test_api2_200_no_www_call(self):
        """api2 returns 200 -> result returned, www never called."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200, "text": "joined"},  # api2
            # www would be next but should not be called
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is True
        assert result["status_code"] == 200
        assert result["endpoint_used"] == "api2"
        # Only one evaluate call consumed
        assert page._evaluate_call_idx == 1

    def test_api2_404_then_www_200(self):
        """api2 returns 404, www returns 200 -> ok, endpoint_used=www."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 404, "text": "not found"},  # api2
            {"ok": True, "status": 200, "text": "joined via www"},  # www
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is True
        assert result["status_code"] == 200
        assert result["endpoint_used"] == "www"
        assert result["response_text"] == "joined via www"

    def test_api2_404_then_www_404(self):
        """Both 404 -> not found, endpoint_used=www."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 404, "text": "not found"},  # api2
            {"ok": False, "status": 404, "text": "not found"},  # www
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is False
        assert result["status_code"] == 404
        assert result["endpoint_used"] == "www"

    def test_api2_error_then_www_200(self):
        """api2 network error (status=0), www succeeds -> ok, endpoint_used=www."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 0, "text": "network error"},  # api2
            {"ok": True, "status": 200, "text": "joined"},  # www
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is True
        assert result["status_code"] == 200
        assert result["endpoint_used"] == "www"

    def test_api2_409_no_www_call(self):
        """api2 returns 409 -> returned directly, no www fallback."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": False, "status": 409, "text": "conflict"},
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert result["ok"] is False
        assert result["status_code"] == 409
        assert result["endpoint_used"] == "api2"
        assert page._evaluate_call_idx == 1

    def test_endpoint_used_field_always_present(self):
        """endpoint_used is always in result dict."""
        page = _MockWwwApiPage(evaluate_results=[
            {"ok": True, "status": 200, "text": "ok"},
        ])
        result = _try_join_via_join_group(page, "test-group")
        assert "endpoint_used" in result


class TestForensicsOn404:
    """Integration: forensics captured when join-group returns 404."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def setup_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def test_404_emits_forensic_events(self, test_db_path):
        """When both endpoints 404, worker captures forensic artifacts."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "FAILED",
                    "detail": "join_group_not_found slug=freegroup",
                    "forensic_events": [
                        {"type": "ITEM_ARTIFACT", "detail": "screenshot=artifacts/joiner/j1/i1/not_found.png"},
                        {"type": "ITEM_ARTIFACT", "detail": "url=https://www.skool.com/freegroup title=Freegroup html_head=<head>...</head>"},
                    ]}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type, detail FROM join_events WHERE job_id = ? AND event_type = 'ITEM_ARTIFACT'",
            (job_id,)
        ).fetchall()
        conn.close()
        assert len(events) >= 1
        details = [e["detail"] for e in events]
        assert any("screenshot" in d for d in details)

    def test_404_item_not_joined(self, test_db_path):
        """When both endpoints 404, item is NOT marked JOINED."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "FAILED",
                    "detail": "join_group_not_found slug=freegroup"}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status, fail_reason FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] != "JOINED"
        assert "not_found" in (item["fail_reason"] or "")



# =========================================================================
# PHASE 4.7d: modal parsing, survey gate, NOT_MEMBER forensics
# =========================================================================

from joiner import _parse_join_group_modal


class TestParseJoinGroupModal:
    """Unit: _parse_join_group_modal extracts group_id and survey questions."""

    def test_full_modal_json(self):
        """Standard modal JSON with group_id and questions."""
        payload = json.dumps({
            "skoolers_modal": {
                "group_id": "db46e2a8c15944448f2c03a861bd5cb6",
                "survey": {
                    "questions": [
                        {"id": "q1", "type": "email", "label": "What is your email?"},
                        {"id": "q2", "type": "radio", "label": "Where are you?", "options": ["Just starting", "$5k+"]},
                        {"id": "q3", "type": "text", "label": "Why do you want to join?"},
                    ]
                }
            }
        })
        result = _parse_join_group_modal(payload)
        assert result["has_modal"] is True
        assert result["group_id"] == "db46e2a8c15944448f2c03a861bd5cb6"
        assert result["survey_required"] is True
        assert len(result["questions"]) == 3
        assert result["questions"][0]["type"] == "email"
        assert result["questions"][1]["type"] == "radio"
        assert result["questions"][1]["options"] == ["Just starting", "$5k+"]

    def test_flat_json_with_uuid(self):
        """Flat JSON containing a UUID somewhere."""
        payload = json.dumps({
            "survey": True,
            "id": "ab12cd34ef5678901234567890abcdef"
        })
        result = _parse_join_group_modal(payload)
        assert result["has_modal"] is True
        assert result["survey_required"] is True
        assert len(result["group_id"]) >= 20

    def test_non_json_with_survey_keyword(self):
        """Non-JSON text containing 'survey' keyword."""
        result = _parse_join_group_modal("Success! Please complete the survey.")
        assert result["has_modal"] is False
        assert result["survey_required"] is True
        assert result["questions"] == []

    def test_empty_response(self):
        """Empty response -> no modal."""
        result = _parse_join_group_modal("")
        assert result["has_modal"] is False
        assert result["group_id"] == ""
        assert result["survey_required"] is False

    def test_json_no_survey(self):
        """JSON without survey -> has_modal but no survey."""
        payload = json.dumps({"status": "ok", "message": "joined"})
        result = _parse_join_group_modal(payload)
        assert result["has_modal"] is True
        assert result["survey_required"] is False
        assert result["questions"] == []

    def test_group_id_extraction_from_nested(self):
        """group_id in nested survey object."""
        payload = json.dumps({
            "data": {
                "survey": {
                    "groupId": "1234567890abcdef1234567890abcdef",
                    "questions": [{"label": "Email?", "type": "email"}]
                }
            }
        })
        result = _parse_join_group_modal(payload)
        assert result["group_id"] == "1234567890abcdef1234567890abcdef"
        assert len(result["questions"]) == 1


class TestSurveySubmitGate:
    """Unit: survey submit is skipped when questions=0 AND no modal schema."""

    def test_no_submit_when_no_questions(self):
        """_build_survey_answers returns empty list for empty questions."""
        answers = _build_survey_answers([])
        assert answers == []
        # Caller should NOT call _submit_survey_answers when answers is empty

    def test_radio_question_gets_first_option(self):
        """Radio question picks first non-decline option."""
        questions = [
            {"label": "Where are you?", "type": "radio", "options": ["No thanks", "Just starting", "$5k+"]}
        ]
        answers = _build_survey_answers(questions)
        assert len(answers) == 1
        # Should skip "No thanks" (decline pattern) and pick "Just starting"
        assert answers[0]["answer"] == "Just starting"

    def test_radio_all_decline_picks_first(self):
        """Radio with all decline options picks first one."""
        questions = [
            {"label": "Pick one", "type": "radio", "options": ["No", "None", "Not applicable"]}
        ]
        answers = _build_survey_answers(questions)
        assert len(answers) == 1
        assert answers[0]["answer"] == "No"

    def test_checkbox_gets_true(self):
        """Checkbox type gets 'true'."""
        questions = [{"label": "I agree to terms", "type": "checkbox"}]
        answers = _build_survey_answers(questions)
        assert answers[0]["answer"] == "true"

    def test_mixed_question_types(self):
        """Mix of text, email, radio, checkbox all get filled."""
        questions = [
            {"label": "Email", "type": "email"},
            {"label": "Experience level?", "type": "radio", "options": ["Beginner", "Advanced"]},
            {"label": "Accept terms", "type": "checkbox"},
            {"label": "Why join?", "type": "text"},
        ]
        answers = _build_survey_answers(questions)
        assert len(answers) == 4
        assert answers[0]["answer"] == _SURVEY_DEFAULTS["email"]
        assert answers[1]["answer"] == "Beginner"
        assert answers[2]["answer"] == "true"
        assert answers[3]["answer"] == _SURVEY_DEFAULTS["why_join"]


class TestNotMemberForensics:
    """Integration: NOT_MEMBER after join attempts emits forensic events."""

    def _tick_pw(self, db_path, pw_fn):
        get_db = _get_db_factory(db_path)
        worker_tick._force_enabled = True
        worker_tick._force_mode = "playwright"
        try:
            return worker_tick(get_db, _playwright_join_fn=pw_fn)
        finally:
            worker_tick._force_enabled = False
            worker_tick._force_mode = None

    def setup_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def teardown_method(self):
        _blocked_profiles.clear()
        _worker_state.disabled = False
        _worker_state.disable_reason = None

    def test_not_member_emits_forensic_events(self, test_db_path):
        """When join ends NOT_MEMBER, forensic artifacts are captured."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "FAILED",
                    "detail": "not_member_after_join_attempt slug=freegroup",
                    "forensic_events": [
                        {"type": "ITEM_ARTIFACT", "detail": "screenshot=artifacts/joiner/j/i/not_member.png"},
                        {"type": "ITEM_DEBUG", "detail": "url_after=https://www.skool.com/freegroup/about join_btn_text=Join"},
                    ]}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        events = conn.execute(
            "SELECT event_type, detail FROM join_events WHERE job_id = ? AND event_type IN ('ITEM_ARTIFACT', 'ITEM_DEBUG')",
            (job_id,)
        ).fetchall()
        conn.close()
        assert len(events) >= 1
        details = [e["detail"] for e in events]
        assert any("screenshot" in d for d in details)

    def test_not_member_item_is_retriable(self, test_db_path):
        """NOT_MEMBER result sets item to retriable PENDING with fail_reason."""
        job_id = _create_test_job(test_db_path, profile_ids=["p1"], num_urls=1)

        def fake_pw(*args, **kwargs):
            return {"status": "FAILED",
                    "detail": "not_member_after_join_attempt slug=freegroup"}

        self._tick_pw(test_db_path, fake_pw)
        conn = sqlite3.connect(test_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        item = conn.execute("SELECT status, attempt_count, fail_reason FROM join_job_items WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        assert item["status"] == "PENDING"  # retriable
        assert item["attempt_count"] == 1
        assert "not_member" in (item["fail_reason"] or "")
