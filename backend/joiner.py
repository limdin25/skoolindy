"""
EngageFlow Community Joiner — Phase 2+3+4+4.1+4.2+4.3a
DB tables, API routes, normalization, event audit, background worker,
Playwright join execution (canary), post-click verification (4.1), forensic capture (4.2), WAF detection (4.3a).
No mutation of core tables. Self-contained Playwright — no imports from automation/.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

LOGGER = logging.getLogger("engageflow.joiner")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JOINER_ENABLED = os.environ.get("JOINER_ENABLED", "false").lower() in ("1", "true", "yes")
JOINER_MODE = os.environ.get("JOINER_MODE", "simulate").lower()  # "simulate" | "playwright"
MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR = 1  # CANARY: was 6 in Phase 3
MAX_GLOBAL_JOINS_PER_HOUR = 2               # CANARY: system-wide ceiling
MAX_CONCURRENT_PROFILES = 1
WORKER_INTERVAL_SECONDS = 60                # was 30 in Phase 3
ITEMS_PER_CYCLE = 1                         # canary safety: 1 item per tick
MAX_ITEM_ATTEMPTS = 3                       # retry ceiling per item
BACKOFF_DELAYS = [900, 3600, 21600]         # 15m, 60m, 6h in seconds
ACCOUNTS_DIR = Path(__file__).parent / "skool_accounts"
ARTIFACTS_DIR = Path(__file__).parent / "artifacts" / "joiner"

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
    # Phase 4 migration: add next_attempt_at column
    try:
        db.execute("ALTER TABLE join_job_items ADD COLUMN next_attempt_at TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists
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
        self._hourly_counts: list[float] = []

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
# Playwright join execution (Phase 4 — canary, self-contained)
# ---------------------------------------------------------------------------

# Block/captcha keywords (replicated from automation patterns, NOT imported)
_BLOCK_KEYWORDS = [
    "account suspended", "temporarily blocked", "access denied",
    "unusual activity", "verify you are human",
]

# AWS WAF / bot challenge markers
_WAF_URL_MARKERS = ["challenge"]
_WAF_HTML_MARKERS = ["edge.sdk.awswaf.com", "challenge.js"]
_WAF_TITLE_MARKERS = ["attention required", "request blocked"]

# Per-profile cooldown: blocked_profiles[profile_id] = timestamp (6h TTL)
PROFILE_BLOCK_COOLDOWN_SECONDS = 6 * 3600  # 6 hours
_blocked_profiles: Dict[str, float] = {}

# Auth markers — presence indicates logged-in state
_AUTH_SELECTORS = [
    'button[class*="ChatNotificationsIconButton"]',
    'a[href*="/chat?ch="]',
    'a[href^="/@"]',
    'div[class*="TopNav"]',
]

# Join button selectors (tried in order)
_JOIN_BUTTON_SELECTORS = [
    'button:has-text("Join for Free")',
    'button:has-text("Join Group")',
    'button:has-text("Join")',
]

# Membership pending selectors
_PENDING_SELECTORS = [
    'h2:has-text("Membership pending")',
    'button:has-text("Membership Pending")',
    'button:has-text("Cancel membership request")',
]

# Member-area indicators (visible only when inside the community)
_MEMBER_AREA_KEYWORDS = ["classroom", "calendar", "members", "leaderboard"]
_MEMBER_AREA_SELECTORS = [
    'a[href*="/classroom"]',
    'a[href*="/calendar"]',
    'a[href*="/members"]',
    'a[href*="/leaderboard"]',
    'div[class*="PostComposer"]',
    'textarea[placeholder*="Write"]',
]

# Paid wall indicators (near join area)
_PAID_INDICATORS = ["$", "pricing", "payment", "subscribe", "buy now", "upgrade"]


def _parse_proxy_for_joiner(proxy_str: Optional[str]) -> Optional[dict]:
    """Parse proxy string to Playwright proxy config. Replicated from engine pattern."""
    if not proxy_str or not proxy_str.strip():
        return None
    proxy_str = proxy_str.strip()
    # Format: protocol://user:pass@host:port or host:port:user:pass
    if "://" in proxy_str:
        parsed = urlparse(proxy_str)
        result: dict = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            result["username"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        return result
    parts = proxy_str.split(":")
    if len(parts) == 4:
        return {
            "server": f"http://{parts[0]}:{parts[1]}",
            "username": parts[2],
            "password": parts[3],
        }
    if len(parts) == 2:
        return {"server": f"http://{parts[0]}:{parts[1]}"}
    return None


def _sanitize_html_head(raw: str, max_len: int = 5000) -> str:
    """Sanitize HTML for forensic logging: remove newlines, collapse whitespace, truncate."""
    if not raw:
        return ""
    # Remove newlines and carriage returns
    s = raw.replace("\n", " ").replace("\r", " ")
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Truncate
    if len(s) > max_len:
        s = s[:max_len] + "...[truncated]"
    return s


def _capture_forensics(page, job_id: str, item_id: str) -> list:
    """Capture forensic artifacts when join_click_no_state_change occurs.

    Returns list of event dicts: [{"type": str, "detail": str}, ...]
    Saves screenshot to disk. Does NOT emit DB events (caller does that).
    """
    events = []
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # --- Screenshot ---
    try:
        artifact_dir = ARTIFACTS_DIR / job_id / item_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = artifact_dir / f"{ts}_no_state.png"
        page.screenshot(path=str(screenshot_path))
        rel_path = f"artifacts/joiner/{job_id}/{item_id}/{ts}_no_state.png"
        events.append({"type": "ITEM_ARTIFACT", "detail": f"screenshot={rel_path}"})
        LOGGER.info("Forensic screenshot saved: %s", rel_path)
    except Exception as e:
        LOGGER.warning("Forensic screenshot failed: %s", str(e)[:200])

    # --- HTML head + URL + title ---
    try:
        page_url = page.url
        page_title = ""
        try:
            page_title = page.title() or ""
        except Exception:
            pass
        html_raw = ""
        try:
            html_raw = page.content() or ""
        except Exception:
            pass
        html_head = _sanitize_html_head(html_raw)
        events.append({
            "type": "ITEM_ARTIFACT",
            "detail": f"url={page_url} title={page_title} html_head={html_head}",
        })
    except Exception as e:
        LOGGER.warning("Forensic HTML capture failed: %s", str(e)[:200])

    # --- Post-click URL + button text ---
    try:
        url_after = page.url
        btn_text = ""
        for sel in _JOIN_BUTTON_SELECTORS:
            btn = page.query_selector(sel)
            if btn:
                try:
                    if btn.is_visible():
                        btn_text = (btn.text_content() or "").strip()[:100]
                        break
                except Exception:
                    pass
        events.append({
            "type": "ITEM_DEBUG",
            "detail": f"url_after={url_after} join_btn_text={btn_text}",
        })
    except Exception as e:
        LOGGER.warning("Forensic debug capture failed: %s", str(e)[:200])

    return events


def _classify_page_state(page) -> dict:
    """Classify current page state into a deterministic result.

    Returns: {"state": str, "detail": str}
      state: MEMBER | PENDING | PAID | JOIN_VISIBLE | AUTH_REQUIRED | BLOCKED | UNKNOWN
    """
    try:
        page_text = (page.text_content("body") or "").lower()
    except Exception:
        page_text = ""

    page_url = page.url.lower()

    # 1. Auth check
    if "/login" in page_url or page.query_selector("input#email"):
        return {"state": "AUTH_REQUIRED", "detail": "auth_session_invalid"}

    # 2. Block/captcha check
    for kw in _BLOCK_KEYWORDS:
        if kw in page_text:
            return {"state": "BLOCKED", "detail": f"blocked_or_captcha: {kw}"}
    if page.query_selector('iframe[src*="captcha"]'):
        return {"state": "BLOCKED", "detail": "blocked_or_captcha: captcha_iframe"}

    # 2b. AWS WAF / bot challenge detection
    for marker in _WAF_URL_MARKERS:
        if marker in page_url:
            return {"state": "BLOCKED", "detail": "aws_waf_challenge"}
    try:
        html_src = page.content() or ""
    except Exception:
        html_src = ""
    html_src_lower = html_src.lower()
    for marker in _WAF_HTML_MARKERS:
        if marker in html_src_lower:
            return {"state": "BLOCKED", "detail": "aws_waf_challenge"}
    page_title = ""
    try:
        page_title = (page.title() or "").lower()
    except Exception:
        pass
    for marker in _WAF_TITLE_MARKERS:
        if marker in page_title or marker in page_text:
            return {"state": "BLOCKED", "detail": "aws_waf_challenge"}

    # 3. Auth markers
    has_auth = any(page.query_selector(sel) for sel in _AUTH_SELECTORS)

    # 4. Membership pending
    is_pending = (
        ("membership pending" in page_text)
        or any(page.query_selector(sel) for sel in _PENDING_SELECTORS)
    )
    if is_pending:
        return {"state": "PENDING", "detail": "membership_pending_detected"}

    # 5. Member area indicators (Classroom, Calendar, Members, Leaderboard, post composer)
    member_signals = sum(1 for kw in _MEMBER_AREA_KEYWORDS if kw in page_text)
    member_selectors = sum(1 for sel in _MEMBER_AREA_SELECTORS if page.query_selector(sel))
    is_member_area = member_signals >= 2 or member_selectors >= 2

    # 6. Join button visible?
    join_btn = None
    for sel in _JOIN_BUTTON_SELECTORS:
        btn = page.query_selector(sel)
        if btn and btn.is_visible():
            join_btn = btn
            break

    # 7. Already a member: auth markers + member area signals + no join button
    if (has_auth or is_member_area) and join_btn is None:
        return {"state": "MEMBER", "detail": "member_ui_detected"}

    # 8. Paid wall: no join button, no auth, pricing text
    if join_btn is None and not has_auth and not is_member_area:
        for indicator in _PAID_INDICATORS:
            if indicator in page_text:
                return {"state": "PAID", "detail": f"paid_wall_detected: {indicator}"}

    # 9. Join button visible
    if join_btn is not None:
        return {"state": "JOIN_VISIBLE", "detail": "join_button_found"}

    # 10. Fallback
    return {"state": "UNKNOWN", "detail": "unknown_page_state"}


def _execute_playwright_join(
    profile_id: str,
    community_url: str,
    community_key: str,
    db_path: str,
    *,
    job_id: str = "",
    item_id: str = "",
) -> dict:
    """Execute real Playwright join for one community. Returns result dict.

    Self-contained: creates own Playwright instance, does NOT import from automation/.
    Uses existing persistent browser profile at skool_accounts/<profile_id>/browser/.

    Returns: {"status": str, "detail": str}
      status: JOINED | ALREADY_MEMBER | PENDING_APPROVAL | SKIPPED_PAID | FAILED
      detail: human-readable explanation
    """
    from playwright.sync_api import sync_playwright

    profile_path = ACCOUNTS_DIR / profile_id / "browser"
    if not profile_path.exists():
        return {"status": "FAILED", "detail": f"profile browser dir not found: {profile_path}"}

    # Look up proxy from profiles table
    proxy_str = None
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT proxy FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if row:
            proxy_str = row["proxy"]
        conn.close()
    except Exception:
        pass  # proceed without proxy

    pw = None
    context = None
    try:
        pw = sync_playwright().start()

        launch_kwargs: Dict[str, Any] = {
            "user_data_dir": str(profile_path),
            "headless": True,
            "viewport": {"width": 1600, "height": 1100},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        proxy_cfg = _parse_proxy_for_joiner(proxy_str)
        if proxy_cfg:
            launch_kwargs["proxy"] = proxy_cfg

        context = pw.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(30000)

        # Navigate to community
        try:
            page.goto(community_url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)  # let JS settle
        except Exception as nav_err:
            return {"status": "FAILED", "detail": f"navigation_timeout: {str(nav_err)[:200]}"}

        # --- Use classifier for initial state detection ---
        pre_state = _classify_page_state(page)
        LOGGER.debug("Pre-click classify: %s", pre_state)

        state = pre_state["state"]
        detail = pre_state["detail"]

        if state == "AUTH_REQUIRED":
            return {"status": "FAILED", "detail": detail}
        if state == "BLOCKED":
            # Capture forensics for blocked pages (WAF, captcha, etc.)
            forensic_events = []
            if job_id and item_id:
                try:
                    forensic_events = _capture_forensics(page, job_id, item_id)
                except Exception as fe:
                    LOGGER.warning("Forensic capture on BLOCKED: %s", str(fe)[:200])
            return {"status": "FAILED", "detail": detail, "blocked_terminal": True,
                    "forensic_events": forensic_events}
        if state == "PENDING":
            return {"status": "PENDING_APPROVAL", "detail": detail}
        if state == "MEMBER":
            return {"status": "ALREADY_MEMBER", "detail": detail}
        if state == "PAID":
            return {"status": "SKIPPED_PAID", "detail": detail}
        if state == "UNKNOWN":
            return {"status": "FAILED", "detail": detail}

        # --- JOIN_VISIBLE: click with robustness ---
        join_btn = None
        for sel in _JOIN_BUTTON_SELECTORS:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                join_btn = btn
                break

        if join_btn is None:
            return {"status": "FAILED", "detail": "join_button_lost_before_click"}

        click_success = False
        for click_attempt in range(1, 3):  # max 2 attempts
            try:
                # Scroll into view
                join_btn.scroll_into_view_if_needed()
                page.wait_for_timeout(300)

                if click_attempt == 1:
                    join_btn.click()
                else:
                    # Force click fallback on retry
                    join_btn.click(force=True)

                click_success = True
                break
            except Exception as click_err:
                LOGGER.warning("Join click attempt %d failed: %s", click_attempt, str(click_err)[:200])
                if click_attempt >= 2:
                    return {"status": "FAILED", "detail": f"join_click_failed: {str(click_err)[:200]}"}
                # Re-find the button for retry
                join_btn = None
                for sel in _JOIN_BUTTON_SELECTORS:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        join_btn = btn
                        break
                if join_btn is None:
                    return {"status": "FAILED", "detail": "join_button_lost_during_retry"}

        if not click_success:
            return {"status": "FAILED", "detail": "join_click_failed_all_attempts"}

        # --- Post-click: 15s polling loop (500ms intervals) ---
        poll_end = time.time() + 15
        last_state = None
        while time.time() < poll_end:
            page.wait_for_timeout(500)
            post_state = _classify_page_state(page)
            last_state = post_state

            s = post_state["state"]
            if s == "MEMBER":
                return {"status": "JOINED", "detail": f"joined {community_key}"}
            if s == "PENDING":
                return {"status": "PENDING_APPROVAL", "detail": "pending_after_join_click"}
            if s == "PAID":
                return {"status": "SKIPPED_PAID", "detail": "paid_wall_after_join_click"}
            if s == "AUTH_REQUIRED":
                return {"status": "FAILED", "detail": "auth_lost_after_click"}
            if s == "BLOCKED":
                return {"status": "FAILED", "detail": post_state["detail"]}
            # JOIN_VISIBLE or UNKNOWN -- keep polling
            continue

        # Polling exhausted — capture forensics before returning
        final_detail = last_state["detail"] if last_state else "polling_timeout"
        forensic_events = []
        if job_id and item_id:
            try:
                forensic_events = _capture_forensics(page, job_id, item_id)
            except Exception as fe:
                LOGGER.warning("Forensic capture error: %s", str(fe)[:200])
        return {
            "status": "FAILED",
            "detail": f"join_click_no_state_change: {final_detail}",
            "forensic_events": forensic_events,
        }

    except Exception as exc:
        return {"status": "FAILED", "detail": f"playwright_error: {str(exc)[:300]}"}
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Rate limiting helpers (Phase 4 — canary limits)
# ---------------------------------------------------------------------------

def _is_profile_blocked(profile_id: str) -> bool:
    """Check if profile is in WAF/block cooldown (6h in-memory TTL)."""
    blocked_ts = _blocked_profiles.get(profile_id)
    if blocked_ts is None:
        return False
    if time.time() - blocked_ts > PROFILE_BLOCK_COOLDOWN_SECONDS:
        del _blocked_profiles[profile_id]
        return False
    return True


def _set_profile_blocked(profile_id: str) -> None:
    """Mark profile as blocked with current timestamp."""
    _blocked_profiles[profile_id] = time.time()


def _get_rate_limited_profile(db: sqlite3.Connection, job_id: str) -> Optional[str]:
    """Pick a profile that hasn't exceeded hourly rate limit.

    Phase 4 canary: MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR = 1
    """
    cutoff_iso = datetime.fromtimestamp(
        time.time() - 3600, tz=timezone.utc
    ).isoformat(timespec="seconds")

    now_iso = _now_iso()
    candidates = db.execute(
        "SELECT DISTINCT profile_id FROM join_job_items "
        "WHERE job_id = ? AND status = 'PENDING' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
        "ORDER BY rowid",
        (job_id, now_iso),
    ).fetchall()

    for row in candidates:
        pid = row["profile_id"]
        count = db.execute(
            "SELECT COUNT(*) as c FROM join_events WHERE profile_id = ? AND event_type = 'ITEM_JOINED' AND created_at > ?",
            (pid, cutoff_iso),
        ).fetchone()["c"]
        if count < MAX_JOIN_ATTEMPTS_PER_PROFILE_PER_HOUR:
            return pid
    return None


def _check_global_rate_limit(db: sqlite3.Connection) -> bool:
    """Return True if global rate limit exceeded (>= MAX_GLOBAL_JOINS_PER_HOUR)."""
    cutoff_iso = datetime.fromtimestamp(
        time.time() - 3600, tz=timezone.utc
    ).isoformat(timespec="seconds")
    count = db.execute(
        "SELECT COUNT(*) as c FROM join_events WHERE event_type IN ('ITEM_JOINED', 'ITEM_COMPLETED') AND created_at > ?",
        (cutoff_iso,),
    ).fetchone()["c"]
    return count >= MAX_GLOBAL_JOINS_PER_HOUR


def _check_kill_switch(db: sqlite3.Connection, latest_detail: Optional[str] = None) -> Optional[str]:
    """Check if kill switch should trigger. Returns reason or None."""
    # Immediate kill: auth/session invalid
    if latest_detail and "auth_session_invalid" in latest_detail:
        return f"auth_session_invalid: {latest_detail}"

    # 3 consecutive failures in last hour
    cutoff_iso = datetime.fromtimestamp(
        time.time() - 3600, tz=timezone.utc
    ).isoformat(timespec="seconds")
    recent = db.execute(
        "SELECT event_type FROM join_events WHERE created_at > ? ORDER BY rowid DESC LIMIT 3",
        (cutoff_iso,),
    ).fetchall()
    if len(recent) >= 3 and all(r["event_type"] == "ITEM_FAILED" for r in recent):
        return "3_consecutive_failures_in_last_hour"

    return None


def _compute_next_attempt_at(attempt_count: int) -> Optional[str]:
    """Compute next_attempt_at based on attempt count. None if max reached."""
    if attempt_count >= MAX_ITEM_ATTEMPTS:
        return None  # terminal
    idx = min(attempt_count - 1, len(BACKOFF_DELAYS) - 1)
    if idx < 0:
        idx = 0
    delay = BACKOFF_DELAYS[idx]
    next_ts = datetime.fromtimestamp(
        time.time() + delay, tz=timezone.utc
    ).isoformat(timespec="seconds")
    return next_ts


# ---------------------------------------------------------------------------
# Worker loop (Phase 4 — simulate or playwright mode)
# ---------------------------------------------------------------------------

def worker_tick(get_db_func, *, _playwright_join_fn=None) -> dict:
    """Execute one worker cycle. Returns summary dict for testing/logging.

    Processes at most ITEMS_PER_CYCLE items. DB is sole source of truth.
    In playwright mode, executes real browser joins.
    In simulate mode, performs Phase 3 simulation (PENDING->READY->JOINED).

    _playwright_join_fn: injectable for testing (default: _execute_playwright_join)
    """
    result = {
        "processed": 0,
        "skipped_rate_limit": False,
        "skipped_no_work": False,
        "skipped_global_limit": False,
        "error": None,
    }

    if not JOINER_ENABLED and not getattr(worker_tick, "_force_enabled", False):
        result["skipped_no_work"] = True
        return result

    pw_join_fn = _playwright_join_fn or _execute_playwright_join
    effective_mode = getattr(worker_tick, "_force_mode", None) or JOINER_MODE

    try:
        with get_db_func() as db:
            # Global rate limit check
            if _check_global_rate_limit(db):
                result["skipped_global_limit"] = True
                return result

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

                # Per-profile blocked cooldown check
                if _is_profile_blocked(profile_id):
                    _emit_event(db, job_id, "ITEM_SKIPPED", profile_id=profile_id,
                               detail="profile_blocked_cooldown")
                    db.commit()
                    result["skipped_rate_limit"] = True
                    continue

                # Pick one PENDING item for this profile (respecting backoff)
                now_iso = _now_iso()
                item = db.execute(
                    "SELECT * FROM join_job_items WHERE job_id = ? AND profile_id = ? AND status = 'PENDING' "
                    "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY rowid LIMIT 1",
                    (job_id, profile_id, now_iso),
                ).fetchone()

                if not item:
                    continue

                item_id = item["id"]
                now = _now_iso()

                if effective_mode == "playwright":
                    # ----- PLAYWRIGHT MODE -----
                    # PENDING -> READY
                    db.execute(
                        "UPDATE join_job_items SET status = 'READY', updated_at = ? WHERE id = ? AND status = 'PENDING'",
                        (now, item_id),
                    )
                    _emit_event(db, job_id, "ITEM_STARTED", item_id=item_id, profile_id=profile_id,
                               detail=f"community={item['community_key']}")
                    db.commit()

                    # Get DB path for Playwright function
                    db_path = db.execute("PRAGMA database_list").fetchone()[2]

                    # Execute Playwright join (runs outside DB transaction)
                    _emit_event(db, job_id, "ITEM_NAVIGATED", item_id=item_id, profile_id=profile_id,
                               detail=community_url_str(item))
                    db.commit()

                    pw_result = pw_join_fn(profile_id, item["community_url"], item["community_key"], db_path, job_id=job_id, item_id=item_id)
                    pw_status = pw_result.get("status", "FAILED")
                    pw_detail = pw_result.get("detail", "")

                    _emit_event(db, job_id, "ITEM_DETECTED_STATE", item_id=item_id, profile_id=profile_id,
                               detail=f"status={pw_status} detail={pw_detail}")

                    now = _now_iso()
                    new_attempt_count = item["attempt_count"] + 1

                    if pw_status == "FAILED":
                        is_terminal_block = pw_result.get("blocked_terminal", False)
                        if is_terminal_block:
                            # Terminal block (WAF/captcha): no retries
                            db.execute(
                                "UPDATE join_job_items SET status = 'FAILED', attempt_count = ?, "
                                "last_attempt_at = ?, fail_reason = ?, next_attempt_at = NULL, updated_at = ? "
                                "WHERE id = ?",
                                (new_attempt_count, now, pw_detail, now, item_id),
                            )
                            # Set per-profile cooldown (6h)
                            _set_profile_blocked(profile_id)
                            LOGGER.warning("Profile %s blocked (WAF/captcha), cooldown 6h", profile_id)
                        else:
                            next_at = _compute_next_attempt_at(new_attempt_count)
                            db.execute(
                                "UPDATE join_job_items SET status = 'FAILED', attempt_count = ?, "
                                "last_attempt_at = ?, fail_reason = ?, next_attempt_at = ?, updated_at = ? "
                                "WHERE id = ?",
                                (new_attempt_count, now, pw_detail, next_at, now, item_id),
                            )
                            # If retryable, set back to PENDING for next attempt
                            if next_at is not None:
                                db.execute(
                                    "UPDATE join_job_items SET status = 'PENDING' WHERE id = ? AND attempt_count < ?",
                                    (item_id, MAX_ITEM_ATTEMPTS),
                                )
                        _emit_event(db, job_id, "ITEM_FAILED", item_id=item_id, profile_id=profile_id,
                                   detail=pw_detail)

                        # Kill switch check (before forensic events to preserve consecutive-fail detection)
                        kill_reason = _check_kill_switch(db, pw_detail)
                        if kill_reason:
                            _worker_state.disabled = True
                            _worker_state.disable_reason = kill_reason
                            _emit_event(db, job_id, "WORKER_DISABLED", profile_id=profile_id,
                                       detail=kill_reason)
                            LOGGER.error("Joiner worker kill switch triggered: %s", kill_reason)

                        # Emit forensic events if present (after kill switch check)
                        for fe in pw_result.get("forensic_events", []):
                            _emit_event(db, job_id, fe["type"], item_id=item_id, profile_id=profile_id,
                                       detail=fe["detail"])

                    else:
                        # Success states: JOINED, ALREADY_MEMBER, PENDING_APPROVAL, SKIPPED_PAID
                        db.execute(
                            "UPDATE join_job_items SET status = ?, attempt_count = ?, "
                            "last_attempt_at = ?, updated_at = ?, next_attempt_at = NULL WHERE id = ?",
                            (pw_status, new_attempt_count, now, now, item_id),
                        )
                        event_type = "ITEM_COMPLETED" if pw_status == "JOINED" else f"ITEM_{pw_status}"
                        _emit_event(db, job_id, event_type, item_id=item_id, profile_id=profile_id,
                                   detail=pw_detail)
                        if pw_status == "JOINED":
                            _emit_event(db, job_id, "ITEM_JOINED", item_id=item_id, profile_id=profile_id,
                                       detail=pw_detail)

                else:
                    # ----- SIMULATE MODE (Phase 3 behavior) -----
                    db.execute(
                        "UPDATE join_job_items SET status = 'READY', updated_at = ? WHERE id = ? AND status = 'PENDING'",
                        (now, item_id),
                    )
                    _emit_event(db, job_id, "ITEM_READY", item_id=item_id, profile_id=profile_id,
                               detail=f"community={item['community_key']}")

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
            if processed == 0 and not result["skipped_rate_limit"] and not result["skipped_global_limit"]:
                result["skipped_no_work"] = True

    except Exception as e:
        result["error"] = str(e)
        LOGGER.exception("Joiner worker tick failed")

    return result


def community_url_str(item) -> str:
    """Extract community_url from item row safely."""
    try:
        return str(item["community_url"])
    except Exception:
        return "unknown"


async def joiner_worker_loop(get_db_func) -> None:
    """Async background loop. Calls worker_tick every WORKER_INTERVAL_SECONDS."""
    _worker_state.running = True
    LOGGER.info(
        "Joiner worker loop started (enabled=%s, mode=%s, interval=%ds)",
        JOINER_ENABLED, JOINER_MODE, WORKER_INTERVAL_SECONDS,
    )

    while True:
        try:
            await asyncio.sleep(WORKER_INTERVAL_SECONDS)

            if _worker_state.disabled:
                continue

            _worker_state.last_tick_ts = time.time()
            _worker_state.refresh_hourly_count()

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

            # 5. Global joins last hour (Phase 4)
            cutoff_iso = datetime.fromtimestamp(
                time.time() - 3600, tz=timezone.utc
            ).isoformat(timespec="seconds")
            global_joins = db.execute(
                "SELECT COUNT(*) as c FROM join_events WHERE event_type IN ('ITEM_JOINED','ITEM_COMPLETED') AND created_at > ?",
                (cutoff_iso,),
            ).fetchone()["c"]
            checks.append({"check": "global_joins_last_hour", "ok": True, "detail": str(global_joins)})

            # 6. Consecutive failures (Phase 4)
            recent_events = db.execute(
                "SELECT event_type FROM join_events WHERE created_at > ? ORDER BY rowid DESC LIMIT 10",
                (cutoff_iso,),
            ).fetchall()
            consec_fails = 0
            for ev in recent_events:
                if ev["event_type"] == "ITEM_FAILED":
                    consec_fails += 1
                else:
                    break
            checks.append({"check": "consecutive_failures", "ok": consec_fails < 3, "detail": str(consec_fails)})

        # 7. Worker status
        checks.append({"check": "joiner_enabled", "ok": True, "detail": str(JOINER_ENABLED)})
        checks.append({"check": "joiner_mode", "ok": True, "detail": JOINER_MODE})
        checks.append({"check": "worker_running", "ok": _worker_state.running, "detail": f"disabled={_worker_state.disabled}, reason={_worker_state.disable_reason}"})
        checks.append({"check": "last_worker_tick", "ok": True, "detail": f"ts={_worker_state.last_tick_ts:.0f}"})
        checks.append({"check": "processed_last_hour", "ok": True, "detail": str(_worker_state.processed_last_hour)})

        informational = {"joiner_enabled", "joiner_mode", "worker_running", "last_worker_tick", "processed_last_hour", "global_joins_last_hour"}
        all_ok = all(c["ok"] for c in checks if c["check"] not in informational)
        return {"ok": all_ok, "checks": checks}

    return router
