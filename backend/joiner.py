"""
EngageFlow Community Joiner — Phase 2+3
DB tables, API routes, normalization, event audit, background worker.
No Playwright, no browser automation, no mutation of core tables.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

LOGGER = logging.getLogger("engageflow.joiner")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JOINER_ENABLED = os.environ.get("JOINER_ENABLED", "false").lower() in ("1", "true", "yes")
MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR = 6
MAX_CONCURRENT_PROFILES = 1
WORKER_INTERVAL_SECONDS = 30
ITEMS_PER_CYCLE = 1  # canary safety: process at most 1 item per loop tick

# ---------------------------------------------------------------------------
# URL Normalization
# ---------------------------------------------------------------------------

def normalize_community_url(url: str) -> tuple[str, str]:
    """Return (canonical_url, community_key) from a raw community URL."""
    url = url.strip()
    if not url:
        raise ValueError("empty URL")
    if not re.match(r"https?://", url, re.IGNORECASE):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"no host in URL: {url}")
    path = parsed.path.rstrip("/") or ""
    canonical = urlunparse((
        parsed.scheme.lower(),
        host + (f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""),
        path, "", "", "",
    ))
    community_key = f"{host}{path}".lower()
    return canonical, community_key


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

JOB_STATUSES = {"CREATED", "RUNNING", "PAUSED", "COMPLETED", "CANCELLED"}
ITEM_STATUSES = {
    "PENDING", "READY", "RUNNING", "JOINED", "ALREADY_MEMBER",
    "PENDING_APPROVAL", "SKIPPED_PAID", "FAILED", "CANCELLED",
}
ITEM_TERMINAL = {"JOINED", "ALREADY_MEMBER", "PENDING_APPROVAL", "SKIPPED_PAID", "FAILED", "CANCELLED"}

JOB_TRANSITIONS: Dict[str, set[str]] = {
    "CREATED":   {"RUNNING", "PAUSED", "CANCELLED"},
    "RUNNING":   {"PAUSED", "COMPLETED", "CANCELLED"},
    "PAUSED":    {"CREATED", "RUNNING", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}

ITEM_TRANSITIONS: Dict[str, set[str]] = {
    "PENDING":          {"READY", "RUNNING", "CANCELLED"},
    "READY":            {"RUNNING", "JOINED", "FAILED", "CANCELLED"},
    "RUNNING":          {"JOINED", "ALREADY_MEMBER", "PENDING_APPROVAL", "SKIPPED_PAID", "FAILED", "CANCELLED"},
    "JOINED":           set(),
    "ALREADY_MEMBER":   set(),
    "PENDING_APPROVAL": set(),
    "SKIPPED_PAID":     set(),
    "FAILED":           {"PENDING"},  # allow retry
    "CANCELLED":        set(),
}

def validate_job_transition(old: str, new: str) -> None:
    if new not in JOB_TRANSITIONS.get(old, set()):
        raise ValueError(f"invalid job transition {old} -> {new}")

def validate_item_transition(old: str, new: str) -> None:
    if new not in ITEM_TRANSITIONS.get(old, set()):
        raise ValueError(f"invalid item transition {old} -> {new}")


# ---------------------------------------------------------------------------
# DB Schema (additive)
# ---------------------------------------------------------------------------

def ensure_joiner_tables(db: sqlite3.Connection) -> None:
    """Create joiner tables. Idempotent — safe to call every startup."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS join_jobs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            created_by TEXT,
            status TEXT NOT NULL DEFAULT 'CREATED',
            paused INTEGER NOT NULL DEFAULT 0,
            total_items INTEGER NOT NULL DEFAULT 0,
            completed_items INTEGER NOT NULL DEFAULT 0,
            failed_items INTEGER NOT NULL DEFAULT 0,
            last_updated_at TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS join_job_items (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES join_jobs(id) ON DELETE CASCADE,
            profile_id TEXT NOT NULL REFERENCES profiles(id),
            community_url TEXT NOT NULL,
            community_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            fail_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_jji_job_id ON join_job_items(job_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_jji_profile_id ON join_job_items(profile_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_jji_status ON join_job_items(status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_jji_community_key ON join_job_items(community_key)")
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_jji_unique_per_job
        ON join_job_items(job_id, profile_id, community_key)
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS join_events (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES join_jobs(id) ON DELETE CASCADE,
            item_id TEXT,
            profile_id TEXT,
            event_type TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        )
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _uuid() -> str:
    return str(uuid.uuid4())

def _emit_event(
    db: sqlite3.Connection,
    job_id: str,
    event_type: str,
    *,
    item_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    db.execute(
        "INSERT INTO join_events (id, job_id, item_id, profile_id, event_type, detail, created_at) VALUES (?,?,?,?,?,?,?)",
        (_uuid(), job_id, item_id, profile_id, event_type, detail, _now_iso()),
    )

def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)

def _update_job_counters(db: sqlite3.Connection, job_id: str) -> None:
    """Recompute job counters from items."""
    row = db.execute(
        "SELECT COUNT(*) as total, "
        "SUM(CASE WHEN status IN ('JOINED','ALREADY_MEMBER','PENDING_APPROVAL','SKIPPED_PAID') THEN 1 ELSE 0 END) as completed, "
        "SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed "
        "FROM join_job_items WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    now = _now_iso()
    db.execute(
        "UPDATE join_jobs SET total_items = ?, completed_items = ?, failed_items = ?, last_updated_at = ? WHERE id = ?",
        (row["total"], row["completed"], row["failed"], now, job_id),
    )


# ---------------------------------------------------------------------------
# Worker state (module-level, read by integrity endpoint)
# ---------------------------------------------------------------------------

class _WorkerState:
    """Tracks worker health. Not used for job logic — DB is source of truth."""
    def __init__(self) -> None:
        self.running = False
        self.disabled = False
        self.disable_reason: Optional[str] = None
        self.last_tick_ts: float = 0.0
        self.processed_last_hour: int = 0
        self._hourly_counts: list[float] = []  # timestamps of processed items

    def record_processed(self) -> None:
        now = time.time()
        self._hourly_counts.append(now)
        cutoff = now - 3600
        self._hourly_counts = [t for t in self._hourly_counts if t > cutoff]
        self.processed_last_hour = len(self._hourly_counts)

    def refresh_hourly_count(self) -> None:
        cutoff = time.time() - 3600
        self._hourly_counts = [t for t in self._hourly_counts if t > cutoff]
        self.processed_last_hour = len(self._hourly_counts)

_worker_state = _WorkerState()


# ---------------------------------------------------------------------------
# Worker loop (Phase 3 — simulation only, no Playwright)
# ---------------------------------------------------------------------------

def _get_rate_limited_profile(db: sqlite3.Connection, job_id: str) -> Optional[str]:
    """Pick a profile that hasn't exceeded hourly rate limit.

    Checks join_events for ITEM_JOINED events in the last hour per profile.
    Returns the first eligible profile_id or None.
    """
    cutoff_iso = datetime.fromtimestamp(
        time.time() - 3600, tz=timezone.utc
    ).isoformat(timespec="seconds")

    # Get profiles with PENDING items for this job
    candidates = db.execute(
        "SELECT DISTINCT profile_id FROM join_job_items WHERE job_id = ? AND status = 'PENDING' ORDER BY rowid",
        (job_id,),
    ).fetchall()

    for row in candidates:
        pid = row["profile_id"]
        # Count joins in last hour across ALL jobs for this profile
        count = db.execute(
            "SELECT COUNT(*) as c FROM join_events WHERE profile_id = ? AND event_type = 'ITEM_JOINED' AND created_at > ?",
            (pid, cutoff_iso),
        ).fetchone()["c"]
        if count < MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR:
            return pid
    return None


def worker_tick(get_db_func) -> dict:
    """Execute one worker cycle. Returns summary dict for testing/logging.

    Processes at most ITEMS_PER_CYCLE items. DB is sole source of truth.
    No Playwright. No browser automation.
    """
    result = {"processed": 0, "skipped_rate_limit": False, "skipped_no_work": False, "error": None}

    if not JOINER_ENABLED and not getattr(worker_tick, "_force_enabled", False):
        result["skipped_no_work"] = True
        return result

    try:
        with get_db_func() as db:
            # Find active jobs (CREATED and not paused)
            jobs = db.execute(
                "SELECT id FROM join_jobs WHERE status = 'CREATED' AND paused = 0 ORDER BY rowid"
            ).fetchall()

            if not jobs:
                result["skipped_no_work"] = True
                return result

            processed = 0
            for job_row in jobs:
                if processed >= ITEMS_PER_CYCLE:
                    break

                job_id = job_row["id"]

                # Rate-limit check: pick eligible profile
                profile_id = _get_rate_limited_profile(db, job_id)
                if profile_id is None:
                    result["skipped_rate_limit"] = True
                    continue

                # Pick one PENDING item for this profile in this job
                item = db.execute(
                    "SELECT * FROM join_job_items WHERE job_id = ? AND profile_id = ? AND status = 'PENDING' ORDER BY rowid LIMIT 1",
                    (job_id, profile_id),
                ).fetchone()

                if not item:
                    continue

                item_id = item["id"]
                now = _now_iso()

                # PENDING -> READY
                db.execute(
                    "UPDATE join_job_items SET status = 'READY', updated_at = ? WHERE id = ? AND status = 'PENDING'",
                    (now, item_id),
                )
                _emit_event(db, job_id, "ITEM_READY", item_id=item_id, profile_id=profile_id,
                           detail=f"community={item['community_key']}")

                # READY -> JOINED (Phase 3 simulation — no actual browser action)
                db.execute(
                    "UPDATE join_job_items SET status = 'JOINED', attempt_count = attempt_count + 1, "
                    "last_attempt_at = ?, updated_at = ? WHERE id = ? AND status = 'READY'",
                    (now, now, item_id),
                )
                _emit_event(db, job_id, "ITEM_JOINED", item_id=item_id, profile_id=profile_id,
                           detail=f"simulated join for {item['community_key']}")

                _update_job_counters(db, job_id)

                # Check if job is now complete
                remaining = db.execute(
                    "SELECT COUNT(*) as c FROM join_job_items WHERE job_id = ? AND status IN ('PENDING','READY','RUNNING')",
                    (job_id,),
                ).fetchone()["c"]
                if remaining == 0:
                    db.execute("UPDATE join_jobs SET status = 'COMPLETED', last_updated_at = ? WHERE id = ?", (now, job_id))
                    _emit_event(db, job_id, "JOB_COMPLETED")

                db.commit()
                processed += 1
                _worker_state.record_processed()

            result["processed"] = processed
            if processed == 0 and not result["skipped_rate_limit"]:
                result["skipped_no_work"] = True

    except Exception as e:
        result["error"] = str(e)
        LOGGER.exception("Joiner worker tick failed")

    return result


async def joiner_worker_loop(get_db_func) -> None:
    """Async background loop. Calls worker_tick every WORKER_INTERVAL_SECONDS."""
    _worker_state.running = True
    LOGGER.info("Joiner worker loop started (enabled=%s, interval=%ds)", JOINER_ENABLED, WORKER_INTERVAL_SECONDS)

    while True:
        try:
            await asyncio.sleep(WORKER_INTERVAL_SECONDS)

            if _worker_state.disabled:
                continue

            _worker_state.last_tick_ts = time.time()
            _worker_state.refresh_hourly_count()

            # Run synchronous DB work in thread pool to avoid blocking event loop
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, worker_tick, get_db_func)

            if result.get("error"):
                _worker_state.disabled = True
                _worker_state.disable_reason = result["error"]
                LOGGER.error("Joiner worker self-disabled: %s", result["error"])

            if result.get("processed", 0) > 0:
                LOGGER.info("Joiner worker processed %d item(s)", result["processed"])

        except asyncio.CancelledError:
            LOGGER.info("Joiner worker loop cancelled")
            break
        except Exception:
            _worker_state.disabled = True
            _worker_state.disable_reason = "unhandled exception in loop"
            LOGGER.exception("Joiner worker loop unhandled exception — self-disabled")

    _worker_state.running = False


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CreateJobRequest(BaseModel):
    community_urls: List[str]
    profile_ids: List[str] = []


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_joiner_router(get_db_func) -> APIRouter:
    """Build /joiner router. get_db_func is the app-level get_db context manager."""
    router = APIRouter(prefix="/joiner", tags=["joiner"])

    # ---- POST /joiner/jobs ----
    @router.post("/jobs")
    def create_job(body: CreateJobRequest):
        now = _now_iso()
        job_id = _uuid()

        with get_db_func() as db:
            if body.profile_ids:
                placeholders = ",".join("?" for _ in body.profile_ids)
                profiles = db.execute(
                    f"SELECT id, name FROM profiles WHERE id IN ({placeholders})",
                    body.profile_ids,
                ).fetchall()
                if not profiles:
                    raise HTTPException(400, "no valid profile_ids found")
            else:
                profiles = db.execute(
                    "SELECT id, name FROM profiles WHERE status IN ('ready','running','idle')"
                ).fetchall()
                if not profiles:
                    raise HTTPException(400, "no active profiles found")

            profile_ids = [str(r["id"]) for r in profiles]

            normalized: list[tuple[str, str]] = []
            for raw_url in body.community_urls:
                raw_url = raw_url.strip()
                if not raw_url:
                    continue
                try:
                    canonical, key = normalize_community_url(raw_url)
                    normalized.append((canonical, key))
                except ValueError:
                    continue

            if not normalized:
                raise HTTPException(400, "no valid community URLs provided")

            db.execute(
                "INSERT INTO join_jobs (id, created_at, status, paused, total_items, completed_items, failed_items, last_updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, now, "CREATED", 0, 0, 0, 0, now),
            )
            _emit_event(db, job_id, "JOB_CREATED")

            items_created = 0
            seen_keys: set[tuple[str, str]] = set()
            for pid in profile_ids:
                for canonical, key in normalized:
                    dedupe_tuple = (pid, key)
                    if dedupe_tuple in seen_keys:
                        continue
                    seen_keys.add(dedupe_tuple)
                    item_id = _uuid()
                    try:
                        db.execute(
                            "INSERT INTO join_job_items (id, job_id, profile_id, community_url, community_key, status, attempt_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (item_id, job_id, pid, canonical, key, "PENDING", 0, now, now),
                        )
                        items_created += 1
                    except sqlite3.IntegrityError:
                        continue

            _emit_event(db, job_id, "ITEMS_CREATED", detail=f"{items_created} items")
            _update_job_counters(db, job_id)
            db.commit()
            job_row = db.execute("SELECT * FROM join_jobs WHERE id = ?", (job_id,)).fetchone()

        return {"job": _row_to_dict(job_row), "items_created": items_created}

    # ---- GET /joiner/jobs ----
    @router.get("/jobs")
    def list_jobs(limit: int = 50, status: Optional[str] = None):
        limit = max(1, min(200, limit))
        with get_db_func() as db:
            if status:
                rows = db.execute(
                    "SELECT * FROM join_jobs WHERE status = ? ORDER BY rowid DESC LIMIT ?",
                    (status.upper(), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM join_jobs ORDER BY rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ---- GET /joiner/jobs/{job_id} ----
    @router.get("/jobs/{job_id}")
    def get_job(job_id: str):
        with get_db_func() as db:
            row = db.execute("SELECT * FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "job not found")
        return _row_to_dict(row)

    # ---- GET /joiner/jobs/{job_id}/items ----
    @router.get("/jobs/{job_id}/items")
    def list_items(job_id: str, limit: int = 200, status: Optional[str] = None):
        limit = max(1, min(500, limit))
        with get_db_func() as db:
            job = db.execute("SELECT id FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
            if not job:
                raise HTTPException(404, "job not found")
            if status:
                rows = db.execute(
                    "SELECT * FROM join_job_items WHERE job_id = ? AND status = ? ORDER BY created_at LIMIT ?",
                    (job_id, status.upper(), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM join_job_items WHERE job_id = ? ORDER BY created_at LIMIT ?",
                    (job_id, limit),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ---- POST /joiner/jobs/{job_id}/pause ----
    @router.post("/jobs/{job_id}/pause")
    def pause_job(job_id: str):
        with get_db_func() as db:
            row = db.execute("SELECT * FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "job not found")
            try:
                validate_job_transition(row["status"], "PAUSED")
            except ValueError as e:
                raise HTTPException(409, str(e))
            now = _now_iso()
            db.execute("UPDATE join_jobs SET status = 'PAUSED', paused = 1, last_updated_at = ? WHERE id = ?", (now, job_id))
            _emit_event(db, job_id, "JOB_PAUSED")
            db.commit()
            updated = db.execute("SELECT * FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_dict(updated)

    # ---- POST /joiner/jobs/{job_id}/resume ----
    @router.post("/jobs/{job_id}/resume")
    def resume_job(job_id: str):
        with get_db_func() as db:
            row = db.execute("SELECT * FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "job not found")
            try:
                validate_job_transition(row["status"], "CREATED")
            except ValueError as e:
                raise HTTPException(409, str(e))
            now = _now_iso()
            db.execute("UPDATE join_jobs SET status = 'CREATED', paused = 0, last_updated_at = ? WHERE id = ?", (now, job_id))
            _emit_event(db, job_id, "JOB_RESUMED")
            db.commit()
            updated = db.execute("SELECT * FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_dict(updated)

    # ---- POST /joiner/jobs/{job_id}/cancel ----
    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        with get_db_func() as db:
            row = db.execute("SELECT * FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "job not found")
            try:
                validate_job_transition(row["status"], "CANCELLED")
            except ValueError as e:
                raise HTTPException(409, str(e))
            now = _now_iso()
            db.execute("UPDATE join_jobs SET status = 'CANCELLED', last_updated_at = ? WHERE id = ?", (now, job_id))
            db.execute(
                "UPDATE join_job_items SET status = 'CANCELLED', updated_at = ? WHERE job_id = ? AND status NOT IN ('JOINED','ALREADY_MEMBER','PENDING_APPROVAL','SKIPPED_PAID','FAILED','CANCELLED')",
                (now, job_id),
            )
            _emit_event(db, job_id, "JOB_CANCELLED")
            _update_job_counters(db, job_id)
            db.commit()
            updated = db.execute("SELECT * FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_dict(updated)

    # ---- GET /joiner/jobs/{job_id}/events ----
    @router.get("/jobs/{job_id}/events")
    def list_events(job_id: str, limit: int = 200):
        limit = max(1, min(500, limit))
        with get_db_func() as db:
            job = db.execute("SELECT id FROM join_jobs WHERE id = ?", (job_id,)).fetchone()
            if not job:
                raise HTTPException(404, "job not found")
            rows = db.execute(
                "SELECT * FROM join_events WHERE job_id = ? ORDER BY rowid DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ---- GET /joiner/integrity ----
    @router.get("/integrity")
    def joiner_integrity():
        checks: list[dict[str, Any]] = []
        with get_db_func() as db:
            # 1. Tables exist
            tables = {str(r["name"]) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            for t in ("join_jobs", "join_job_items", "join_events"):
                checks.append({"check": f"table_{t}_exists", "ok": t in tables})

            if not all(c["ok"] for c in checks):
                return {"ok": False, "checks": checks}

            # 2. Reachable
            try:
                db.execute("SELECT 1 FROM join_job_items LIMIT 0")
                checks.append({"check": "join_job_items_reachable", "ok": True})
            except Exception as e:
                checks.append({"check": "join_job_items_reachable", "ok": False, "detail": str(e)})

            # 3. Job counters match
            jobs = db.execute("SELECT id, total_items, completed_items, failed_items FROM join_jobs").fetchall()
            counter_ok = True
            for job in jobs:
                jid = job["id"]
                row = db.execute(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN status IN ('JOINED','ALREADY_MEMBER','PENDING_APPROVAL','SKIPPED_PAID') THEN 1 ELSE 0 END) as completed, "
                    "SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed "
                    "FROM join_job_items WHERE job_id = ?",
                    (jid,),
                ).fetchone()
                if row["total"] != job["total_items"] or row["completed"] != job["completed_items"] or row["failed"] != job["failed_items"]:
                    counter_ok = False
                    break
            checks.append({"check": "job_counters_match", "ok": counter_ok})

            # 4. No orphan profiles
            orphan_count = db.execute(
                "SELECT COUNT(*) as c FROM join_job_items jji LEFT JOIN profiles p ON jji.profile_id = p.id WHERE p.id IS NULL"
            ).fetchone()["c"]
            checks.append({"check": "no_orphan_profile_refs", "ok": orphan_count == 0, "detail": f"orphans={orphan_count}"})

        # 5. Worker status (Phase 3)
        checks.append({"check": "joiner_enabled", "ok": True, "detail": str(JOINER_ENABLED)})
        checks.append({"check": "worker_running", "ok": _worker_state.running, "detail": f"disabled={_worker_state.disabled}, reason={_worker_state.disable_reason}"})
        checks.append({"check": "last_worker_tick", "ok": True, "detail": f"ts={_worker_state.last_tick_ts:.0f}"})
        checks.append({"check": "processed_last_hour", "ok": True, "detail": str(_worker_state.processed_last_hour)})

        all_ok = all(c["ok"] for c in checks if c["check"] not in ("joiner_enabled", "worker_running", "last_worker_tick", "processed_last_hour"))
        return {"ok": all_ok, "checks": checks}

    return router
