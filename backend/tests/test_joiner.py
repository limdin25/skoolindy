"""
EngageFlow Joiner — Phase 2 Test Suite
Unit + Contract + Behavioral + Invariant tests.
"""
from __future__ import annotations
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
        def mock_pw_join(profile_id, community_url, community_key, db_path):
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
