from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from security_utils import decrypt_secret
from proxy_slots import acquire_proxy_slot, release_proxy_slot

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

SESSION_TTL_SECONDS = 3600
_PLAYWRIGHT_SYNC_LOCK = threading.Lock()
SESSION_MONITOR_ENABLED = str(os.environ.get("AUTOMATION_SESSION_MONITOR_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
LOGIN_CHECK_NAV_TIMEOUT_MS = max(8000, int(os.environ.get("SKOOL_LOGIN_CHECK_NAV_TIMEOUT_MS", "14000")))
LOGIN_CHECK_POST_LOAD_WAIT_MS = max(300, int(os.environ.get("SKOOL_LOGIN_CHECK_POST_LOAD_WAIT_MS", "900")))
LOGIN_CHECK_RESULT_WAIT_MS = max(500, int(os.environ.get("SKOOL_LOGIN_CHECK_RESULT_WAIT_MS", "1800")))
# Legacy env fallback when setting is missing in DB/UI.
QUEUE_PREFILL_MAX_PER_PROFILE_PER_PASS = max(1, int(os.environ.get("AUTOMATION_QUEUE_PREFILL_MAX_PER_PROFILE", "2")))
# Run queue items strictly at scheduled time (no early-start lead window).
QUEUE_EXECUTION_LEAD_SECONDS = 0
# Keep retry budget small so one bad post/proxy does not stall progress for long.
QUEUE_NETWORK_MAX_RETRIES_PER_POST = max(1, int(os.environ.get("QUEUE_NETWORK_MAX_RETRIES_PER_POST", "3")))
QUEUE_NETWORK_RETRY_BASE_SECONDS = max(15, int(os.environ.get("QUEUE_NETWORK_RETRY_BASE_SECONDS", "45")))
QUEUE_NETWORK_RETRY_MAX_DELAY_SECONDS = max(60, int(os.environ.get("QUEUE_NETWORK_RETRY_MAX_DELAY_SECONDS", "900")))
QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS = max(60, int(os.environ.get("QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS", "600")))
QUEUE_NETWORK_MAX_BUDGET_EXHAUST_CYCLES = max(1, int(os.environ.get("QUEUE_NETWORK_MAX_BUDGET_EXHAUST_CYCLES", "3")))
QUEUE_NETWORK_PERSISTENT_FAIL_COOLDOWN_SECONDS = max(300, int(os.environ.get("QUEUE_NETWORK_PERSISTENT_FAIL_COOLDOWN_SECONDS", "300")))
QUEUE_COMMUNITY_NETWORK_COOLDOWN_SECONDS = max(60, int(os.environ.get("QUEUE_COMMUNITY_NETWORK_COOLDOWN_SECONDS", "600")))
QUEUE_EDITOR_NOT_VISIBLE_COOLDOWN_SECONDS = max(300, int(os.environ.get("QUEUE_EDITOR_NOT_VISIBLE_COOLDOWN_SECONDS", "1800")))
AUTOMATION_NO_POST_WAIT_SECONDS = max(15, int(os.environ.get("AUTOMATION_NO_POST_WAIT_SECONDS", "75")))
AUTOMATION_POSTED_WAIT_MIN_SECONDS = max(30, int(os.environ.get("AUTOMATION_POSTED_WAIT_MIN_SECONDS", "75")))
PREFILL_SKIP_LOG_COOLDOWN_SECONDS = max(60, int(os.environ.get("PREFILL_SKIP_LOG_COOLDOWN_SECONDS", "900")))
PREFILL_SKIP_DEBUG_EVERY = max(5, int(os.environ.get("PREFILL_SKIP_DEBUG_EVERY", "20")))
AUTOMATION_DUE_DEFER_MIN_SECONDS = max(5, int(os.environ.get("AUTOMATION_DUE_DEFER_MIN_SECONDS", "45")))
AUTOMATION_DUE_DEFER_MAX_SECONDS = max(
    AUTOMATION_DUE_DEFER_MIN_SECONDS,
    int(os.environ.get("AUTOMATION_DUE_DEFER_MAX_SECONDS", "120")),
)
AUTOMATION_OUTSIDE_SCHEDULE_POLL_SECONDS = max(
    10,
    int(os.environ.get("AUTOMATION_OUTSIDE_SCHEDULE_POLL_SECONDS", "60")),
)
AUTOMATION_FAILURE_DIAGNOSTICS_ENABLED = str(
    os.environ.get("AUTOMATION_FAILURE_DIAGNOSTICS_ENABLED", "0")
).strip().lower() in {"1", "true", "yes", "on"}
LOGGER = logging.getLogger("engageflow.automation")

# Heartbeat / watchdog tuning
_HEARTBEAT_WRITE_INTERVAL = 60      # write heartbeat file every N seconds
_HEARTBEAT_LOG_INTERVAL = 300       # log "HEARTBEAT ok" every N seconds
_HEARTBEAT_MAX_AGE_SECONDS = 300    # watchdog exit threshold


def _write_heartbeat(path: Path) -> None:
    """Atomically write a heartbeat timestamp to *path*."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"ts": time.time(), "pid": os.getpid()}),
            encoding="utf-8",
        )
    except Exception:
        pass


SKOOL_SELECTORS = {
    "post_items": 'div[class*="PostItemWrapper"]',
    "post_content": 'div[class*="ContentPreviewWrapper"]',
    "post_links": 'a[class*="__ChildrenLink"]',
    "post_title": 'div[class*="__TitleWrapper"]',
    "comment_editor": 'div[contenteditable="true"].tiptap.ProseMirror',
    "reply_button": 'button[class*="__ReplyButton"]',
    "comment_button_text": "button:has-text('Comment')",
    "login_email": "input#email",
    "login_password": "input#password",
    "login_submit": "button[type='submit']",
}


def _start_playwright_safe(max_attempts: int = 3):
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright not installed")
    last_error: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            return sync_playwright().start()
        except AttributeError as exc:
            if "_playwright" not in str(exc):
                raise
            last_error = exc
            try:
                import importlib
                import playwright.sync_api as sync_api

                importlib.reload(sync_api)
                globals()["sync_playwright"] = sync_api.sync_playwright
            except Exception:
                pass
            time.sleep(0.25 * (attempt + 1))
        except Exception as exc:
            last_error = exc
            break
    if last_error:
        raise last_error
    raise RuntimeError("Failed to start Playwright")


@dataclass
class EngineState:
    is_running: bool = False
    is_paused: bool = False
    current_profile_index: int = 0
    countdown_seconds: int = 0
    run_state: str = "idle"
    connection_rest_active: bool = False
    connection_rest_remaining_seconds: int = 0
    connection_rest_rounds_before: int = 0
    connection_rest_rounds_completed: int = 0
    connection_rest_minutes: int = 0
    profiles: List[Dict[str, Any]] = field(default_factory=list)
    global_settings: Dict[str, Any] = field(default_factory=dict)
    activity_rows: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=lambda: {
        "total_comments": 0,
        "total_skipped": 0,
        "total_blacklisted": 0,
    })
    last_scheduler_tick_ts: float = 0.0
    idle_mode: bool = False
    idle_reason: str = "unknown"


@dataclass
class ProfileRunResult:
    profile: Dict[str, Any]
    activity_rows: List[Dict[str, Any]] = field(default_factory=list)
    comments_posted: int = 0
    skipped_count: int = 0
    blacklisted_count: int = 0
    network_error_count: int = 0
    due_queue_items_seen: int = 0


class SkoolSessionManager:
    BLOCK_KEYWORDS = [
        "account suspended",
        "temporarily blocked",
        "access denied",
        "unusual activity",
        "verify you are human",
    ]
    LOGIN_URL = "https://www.skool.com/login"
    ENTRY_URLS = [
        "https://www.skool.com/",
    ]

    def __init__(
        self,
        account_id: str,
        email: str,
        password: str,
        base_dir: Path,
        proxy: Optional[str] = None,
        headless: bool = True,
    ) -> None:
        self.account_id = account_id
        self.email = email
        self.password = password
        self.proxy = proxy
        self.headless = headless

        self.base_path = base_dir / account_id
        self.profile_path = self.base_path / "browser"
        self.state_path = self.base_path / "state.json"
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.profile_path.mkdir(parents=True, exist_ok=True)

        self.playwright = None
        self.context = None
        self.page = None
        self._proxy_slot_handle: Optional[Tuple[str, str]] = None

    def launch(self) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")

        self._proxy_slot_handle = acquire_proxy_slot("queue", self.proxy)
        self.playwright = _start_playwright_safe()
        launch_kwargs: Dict[str, Any] = {
            "user_data_dir": str(self.profile_path),
            "headless": self.headless,
            "viewport": {"width": 1600, "height": 1100},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if self.proxy:
            proxy_cfg = _parse_proxy_to_playwright(self.proxy)
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg

        try:
            self.context = self.playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as launch_exc:
            launch_err = str(launch_exc).lower()
            if "connection closed while reading from the driver" in launch_err:
                try:
                    self.playwright.stop()
                except Exception:
                    pass
                self.playwright = _start_playwright_safe()
                self.context = self.playwright.chromium.launch_persistent_context(**launch_kwargs)
            else:
                raise
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(30000)

    def close(self) -> None:
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        finally:
            self.context = None
            self.playwright = None
            self.page = None
            release_proxy_slot(self._proxy_slot_handle)
            self._proxy_slot_handle = None

    def update_state(self, status: str, extra: Optional[Dict[str, Any]] = None) -> None:
        data = {
            "status": status,
            "last_check": int(time.time()),
            "progress_today": 0,
            "last_comment_url": None,
            "next_run_at": None,
            "error": None,
        }
        if extra:
            data.update(extra)
        with self.state_path.open("w", encoding="utf-8") as f:
            json.dump(data, f)

    def read_state(self) -> Optional[Dict[str, Any]]:
        if not self.state_path.exists():
            return None
        with self.state_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def get_cookies_json(self) -> Optional[str]:
        """Extract cookies from context for API auth. Returns JSON string or None.
        Uses sync Playwright API: context.cookies() (no await). Stores full cookie fields."""
        try:
            if not self.context:
                return None
            cookies = self.context.cookies()
            arr = []
            for c in cookies:
                if not c.get("name"):
                    continue
                arr.append({
                    "name": str(c.get("name", "")),
                    "value": str(c.get("value", "")),
                    "domain": str(c.get("domain", "")) if c.get("domain") is not None else "",
                    "path": str(c.get("path", "")),
                    "expires": c.get("expires", -1) if c.get("expires") is not None else -1,
                    "httpOnly": bool(c.get("httpOnly", False)),
                    "secure": bool(c.get("secure", False)),
                    "sameSite": str(c.get("sameSite", "Lax")) if c.get("sameSite") else "Lax",
                })
            return json.dumps(arr) if arr else None
        except Exception:
            return None

    def detect_blocked(self) -> bool:
        try:
            content = self.page.content().lower()
        except Exception:
            return False
        return any(k in content for k in self.BLOCK_KEYWORDS)

    def detect_captcha(self) -> bool:
        try:
            if self.page.query_selector('iframe[src*="captcha"]'):
                return True
            if "captcha" in self.page.content().lower():
                return True
        except Exception:
            pass
        return False

    def has_authenticated_markers(self) -> bool:
        markers = [
            'button[class*="ChatNotificationsIconButton"]',
            'a[href*="/chat?ch="]',
            'a[href^="/@"]',
            'div[class*="TopNav"]',
        ]
        try:
            for selector in markers:
                if self.page.query_selector(selector):
                    return True
            content = self.page.content().lower()
            if "log out" in content or "logout" in content:
                return True
        except Exception:
            return False
        return False

    def validate_session(self) -> str:
        navigated = False
        for entry_url in self.ENTRY_URLS:
            try:
                self.page.goto(entry_url, timeout=LOGIN_CHECK_NAV_TIMEOUT_MS)
                self.page.wait_for_load_state("domcontentloaded")
                try:
                    # Keep this short to avoid long hangs on unstable/proxied networks.
                    self.page.wait_for_load_state("networkidle", timeout=2000)
                except Exception:
                    pass
                self.page.wait_for_timeout(LOGIN_CHECK_POST_LOAD_WAIT_MS)
                navigated = True
                break
            except Exception:
                continue
        if not navigated:
            return "error"

        current_url = self.page.url.lower()
        if "/login" not in current_url and self.has_authenticated_markers():
            return "valid"
        if self.detect_blocked():
            return "blocked"
        if self.detect_captcha():
            return "captcha"
        if "/login" not in current_url:
            return "error"
        return "login_required"

    def perform_login(self) -> str:
        try:
            self.page.goto(self.LOGIN_URL, timeout=LOGIN_CHECK_NAV_TIMEOUT_MS)
            self.page.wait_for_load_state("domcontentloaded")
            try:
                self.page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass
            self.page.wait_for_selector(SKOOL_SELECTORS["login_email"], timeout=min(8000, LOGIN_CHECK_NAV_TIMEOUT_MS))
            self.page.fill(SKOOL_SELECTORS["login_email"], self.email)
            self.page.fill(SKOOL_SELECTORS["login_password"], self.password)
            self.page.click(SKOOL_SELECTORS["login_submit"])
            self.page.wait_for_timeout(LOGIN_CHECK_RESULT_WAIT_MS)
            post_login_url = self.page.url.lower()
        except Exception:
            return "error"

        if "/login" in post_login_url:
            return "failed"
        if self.detect_blocked():
            return "blocked"
        if self.detect_captcha():
            return "captcha"
        return "success"

    def ensure_session(self) -> bool:
        try:
            state = self.read_state()
            now = int(time.time())
            if state and state.get("status") == "ready":
                if now - int(state.get("last_check", 0)) < SESSION_TTL_SECONDS:
                    return True

            self.launch()
            status = self.validate_session()
            if status == "valid":
                self.update_state("ready")
                return True
            if status == "blocked":
                self.update_state("blocked")
                return False
            if status == "captcha":
                self.update_state("captcha")
                return False

            if status == "login_required":
                result = self.perform_login()
                if result == "success":
                    self.update_state("ready")
                    return True
                if result == "blocked":
                    self.update_state("blocked")
                    return False
                if result == "captcha":
                    self.update_state("captcha")
                    return False

            self.update_state("error", {"error": "Login failed"})
            return False
        except Exception as exc:
            self.update_state("error", {"error": str(exc)})
            return False
        finally:
            self.close()


class AutomationEngine:
    def __init__(self, db_path: Path, base_dir: Optional[Path] = None) -> None:
        self.db_path = Path(db_path)
        self.base_dir = Path(base_dir or self.db_path.parent)
        self.accounts_dir = self.base_dir / "skool_accounts"
        self.accounts_dir.mkdir(parents=True, exist_ok=True)

        self.run_state_file = self.base_dir / "skool_run_state.json"
        self.blacklist_file = self.base_dir / "skool_global_blacklist.json"
        self.daily_counters_state_file = self.base_dir / "skool_daily_counters_state.json"
        self.config_file = self.base_dir / "config.local.json"
        self.heartbeat_file = self.base_dir / ".claude_state" / "heartbeat.json"

        self._state = EngineState()
        self._lock = asyncio.Lock()
        self._session_check_lock = asyncio.Lock()
        self._task: Optional[asyncio.Task[None]] = None
        self._session_task: Optional[asyncio.Task[None]] = None
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._subscribers: Set[asyncio.Queue[Dict[str, Any]]] = set()
        self._queue_network_fail_streak: Dict[str, int] = {}
        self._queue_submit_fail_streak: Dict[str, int] = {}
        self._queue_budget_exhaust_streak: Dict[str, int] = {}
        self._queue_post_cooldown_until: Dict[str, float] = {}
        self._queue_community_cooldown_until: Dict[str, float] = {}
        self._prefill_skip_log_state: Dict[str, Dict[str, float]] = {}
        self._ensure_profile_auth_fn: Optional[Callable[[str], dict]] = None
        self._settings_etag: str = ""
        self._debug_snapshot_cache: Dict[str, Any] = {}
        self._debug_snapshot_ts: float = 0.0
        self._last_action: Dict[str, Any] = {}
        self._last_error: Dict[str, Any] = {}
        self._last_recover_reason: Optional[str] = None

        self._hydrate_state_from_disk()
    async def subscribe(self) -> asyncio.Queue[Dict[str, Any]]:
        q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def publish_log(self, message: str, profile: str = "SYSTEM", status: str = "info") -> None:
        event = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "profile": profile,
            "status": status,
            "message": message,
        }
        self._insert_log(event)

        async with self._lock:
            dead: List[asyncio.Queue[Dict[str, Any]]] = []
            for sub in self._subscribers:
                try:
                    sub.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(sub)
            for sub in dead:
                self._subscribers.discard(sub)

    async def get_status(self) -> Dict[str, Any]:
        async with self._lock:
            profiles_safe: List[Dict[str, Any]] = []
            for profile in self._state.profiles:
                item = dict(profile)
                if "password" in item:
                    item["password"] = ""
                profiles_safe.append(item)
            return {
                "success": True,
                "isRunning": self._state.is_running,
                "isPaused": self._state.is_paused,
                "state": self._state.run_state,
                "runState": self._state.run_state,
                "countdownSeconds": self._state.countdown_seconds,
                "connectionRest": {
                    "active": bool(self._state.connection_rest_active),
                    "remainingSeconds": int(self._state.connection_rest_remaining_seconds or 0),
                    "roundsBefore": int(self._state.connection_rest_rounds_before or 0),
                    "roundsCompleted": int(self._state.connection_rest_rounds_completed or 0),
                    "restMinutes": int(self._state.connection_rest_minutes or 0),
                },
                "currentProfileIndex": self._state.current_profile_index,
                "profiles": profiles_safe,
                "stats": dict(self._state.stats),
                "activity": list(self._state.activity_rows),
            }

    async def start(self, profiles: Optional[List[Dict[str, Any]]] = None, global_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        shifted_on_start = 0
        async with self._lock:
            if self._state.is_running and self._task and not self._task.done():
                raise RuntimeError("Automation engine already running")

            db_profiles, db_settings = self._load_runtime_config_from_db()
            settings = {**db_settings, **(global_settings or {})}
            run_profiles = self._merge_profiles(db_profiles, profiles or [])
            persisted = self._load_run_state_file()
            persisted_day = (persisted.get("state_day") or (persisted.get("last_updated", "")[:10] or None)) if persisted else None
            same_day = (persisted_day == datetime.now().strftime("%Y-%m-%d")) if persisted_day else True
            should_restore = bool(persisted and persisted.get("run_state") not in {"completed", "idle", None} and same_day)
            if should_restore:
                run_profiles = self._apply_persisted_counters(run_profiles, persisted)
            elif persisted:
                # Rotation pointers survive day boundaries even when counters don't
                by_id = {str(p.get("id") or ""): p for p in persisted.get("profiles", [])}
                for profile in run_profiles:
                    saved = by_id.get(str(profile.get("id") or ""))
                    if saved and "_current_community_index" in saved:
                        profile["_current_community_index"] = int(saved.get("_current_community_index", 0) or 0)

            self._validate_start_payload(run_profiles, settings)

            if not run_profiles:
                raise RuntimeError("No runnable profiles found")

            self._state.is_running = True
            self._state.is_paused = False
            self._state.run_state = "running"
            self._state.current_profile_index = int(persisted.get("current_profile_index", 0)) if should_restore else 0
            self._state.profiles = run_profiles
            self._state.global_settings = settings
            self._state.connection_rest_active = False
            self._state.connection_rest_remaining_seconds = 0
            self._state.connection_rest_rounds_before = max(1, int(settings.get("roundsBeforeConnectionRest", 5) or 5))
            self._state.connection_rest_rounds_completed = 0
            self._state.connection_rest_minutes = max(1, int(settings.get("connectionRestMinutes", 5) or 5))
            if should_restore:
                self._state.stats = dict(persisted.get("stats", self._state.stats))
            else:
                self._state.stats = {
                    "total_comments": 0,
                    "total_skipped": 0,
                    "total_blacklisted": 0,
                }
            if self._task and not self._task.done():
                self._task.cancel()
            shifted_on_start = self._reschedule_overdue_queue_items()
            self._task = asyncio.create_task(self._scheduler_loop(), name="automation-scheduler")
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_task_loop(), name="automation-heartbeat")
            if self._session_task and not self._session_task.done():
                self._session_task.cancel()
            self._session_task = (
                asyncio.create_task(self._session_monitor_loop(), name="automation-session-monitor")
                if SESSION_MONITOR_ENABLED
                else None
            )
            self._save_run_state_locked()

        await self.publish_log("[SKOOL] Scheduler started", status="success")
        if shifted_on_start > 0:
            await self.publish_log(f"[SKOOL] Rescheduled {shifted_on_start} overdue queue task(s) after start", status="info")
        return await self.get_status()

    async def stop(self) -> Dict[str, Any]:
        task_to_wait: Optional[asyncio.Task[None]] = None
        session_task_to_wait: Optional[asyncio.Task[None]] = None
        watchdog_task_to_cancel: Optional[asyncio.Task[None]] = None
        should_log_stopped = False
        due_pending_before_stop = await asyncio.to_thread(self._count_due_queue_actions)
        async with self._lock:
            should_log_stopped = bool(self._state.is_running or self._state.run_state != "idle")
            self._state.is_running = False
            self._state.is_paused = False
            self._state.run_state = "idle"
            self._state.countdown_seconds = 0
            self._state.connection_rest_active = False
            self._state.connection_rest_remaining_seconds = 0
            self._state.connection_rest_rounds_completed = 0
            task_to_wait = self._task
            session_task_to_wait = self._session_task
            watchdog_task_to_cancel = self._heartbeat_task
            self._save_run_state_locked()
        if watchdog_task_to_cancel and not watchdog_task_to_cancel.done():
            watchdog_task_to_cancel.cancel()
        async with self._lock:
            if self._heartbeat_task is watchdog_task_to_cancel:
                self._heartbeat_task = None
        if task_to_wait and not task_to_wait.done():
            try:
                await asyncio.wait_for(task_to_wait, timeout=5)
            except asyncio.TimeoutError:
                task_to_wait.cancel()
            except Exception:
                pass
        async with self._lock:
            if self._task is task_to_wait:
                self._task = None
        if session_task_to_wait and not session_task_to_wait.done():
            try:
                await asyncio.wait_for(session_task_to_wait, timeout=5)
            except asyncio.TimeoutError:
                session_task_to_wait.cancel()
            except Exception:
                pass
        async with self._lock:
            if self._session_task is session_task_to_wait:
                self._session_task = None
        if due_pending_before_stop > 0:
            await self.publish_log(
                (
                    "[SKOOL] Pending due tasks left unprocessed: "
                    f"{due_pending_before_stop} reason=scheduler_stopped_before_due_execution"
                ),
                status="retry",
            )
        if should_log_stopped:
            await self.publish_log("[SKOOL] Scheduler stopped", status="info")
        return await self.get_status()

    async def shutdown(self, preserve_run_state: bool = True) -> None:
        task_to_wait: Optional[asyncio.Task[None]] = None
        session_task_to_wait: Optional[asyncio.Task[None]] = None
        async with self._lock:
            self._state.is_running = False
            self._state.countdown_seconds = 0
            self._state.connection_rest_active = False
            self._state.connection_rest_remaining_seconds = 0
            self._state.connection_rest_rounds_completed = 0
            if not preserve_run_state:
                self._state.is_paused = False
                self._state.run_state = "idle"
                self._save_run_state_locked()
            task_to_wait = self._task
            session_task_to_wait = self._session_task
            watchdog_task_to_cancel = self._heartbeat_task
        if watchdog_task_to_cancel and not watchdog_task_to_cancel.done():
            watchdog_task_to_cancel.cancel()
        async with self._lock:
            if self._heartbeat_task is watchdog_task_to_cancel:
                self._heartbeat_task = None
        if task_to_wait and not task_to_wait.done():
            try:
                await asyncio.wait_for(task_to_wait, timeout=5)
            except asyncio.TimeoutError:
                task_to_wait.cancel()
            except Exception:
                pass
        async with self._lock:
            if self._task is task_to_wait:
                self._task = None
        if session_task_to_wait and not session_task_to_wait.done():
            try:
                await asyncio.wait_for(session_task_to_wait, timeout=5)
            except asyncio.TimeoutError:
                session_task_to_wait.cancel()
            except Exception:
                pass
        async with self._lock:
            if self._session_task is session_task_to_wait:
                self._session_task = None

    async def pause(self) -> Dict[str, Any]:
        due_pending_on_pause = await asyncio.to_thread(self._count_due_queue_actions)
        async with self._lock:
            if not self._state.is_running:
                raise RuntimeError("Automation engine is not running")
            self._state.is_paused = True
            self._state.run_state = "paused"
            self._state.connection_rest_active = False
            self._state.connection_rest_remaining_seconds = 0
            self._save_run_state_locked()
        if due_pending_on_pause > 0:
            await self.publish_log(
                (
                    "[SKOOL] Pending due tasks while paused: "
                    f"{due_pending_on_pause} reason=scheduler_paused_with_due_tasks"
                ),
                status="retry",
            )
        await self.publish_log("[SKOOL] Scheduler paused", status="info")
        return await self.get_status()

    async def resume(self) -> Dict[str, Any]:
        shifted_on_resume = 0
        async with self._lock:
            if not self._state.is_running:
                raise RuntimeError("Automation engine is not running")
            self._state.is_paused = False
            self._state.run_state = "running"
            self._state.connection_rest_active = False
            self._state.connection_rest_remaining_seconds = 0
            self._save_run_state_locked()
            shifted_on_resume = self._reschedule_overdue_queue_items()
        await self.publish_log("[SKOOL] Scheduler resumed", status="success")
        if shifted_on_resume > 0:
            await self.publish_log(f"[SKOOL] Rescheduled {shifted_on_resume} overdue queue task(s) after resume", status="info")
        return await self.get_status()

    async def recover_after_restart(self) -> None:
        async with self._lock:
            if self._task and not self._task.done():
                return

        persisted = self._load_run_state_file()
        if not persisted:
            return
        if persisted.get("run_state") not in {"running", "paused"}:
            return

        # Discard stale per-profile counters from a previous day to prevent
        # restoring "finished" statuses across daily boundaries.
        today = datetime.now().strftime("%Y-%m-%d")
        persisted_day = persisted.get("state_day") or (persisted.get("last_updated", "")[:10] or None)
        if persisted_day and persisted_day != today:
            LOGGER.info(
                "[SKOOL] Discarded stale run_state from previous day (%s); rebuilt from DB",
                persisted_day,
            )
            self._last_recover_reason = f"stale_day:{persisted_day}"
            # Preserve rotation pointers (not daily counters) across day boundary
            stale_run_state = persisted.get("run_state", "running")
            rotation_profiles = [
                {"id": p.get("id"), "_current_community_index": int(p.get("_current_community_index", 0) or 0)}
                for p in persisted.get("profiles", [])
            ]
            persisted = {"run_state": stale_run_state, "profiles": rotation_profiles, "stats": {"total_comments": 0, "total_skipped": 0, "total_blacklisted": 0}, "current_profile_index": 0}

        db_profiles, db_settings = self._load_runtime_config_from_db()
        restored = self._apply_persisted_counters(db_profiles, persisted)

        async with self._lock:
            self._state.is_running = True
            self._state.is_paused = persisted.get("run_state") == "paused"
            self._state.run_state = persisted.get("run_state", "running")
            self._state.current_profile_index = int(persisted.get("current_profile_index", 0))
            self._state.stats = persisted.get("stats", self._state.stats)
            self._state.profiles = restored
            self._state.global_settings = db_settings
            self._task = asyncio.create_task(self._scheduler_loop(), name="automation-scheduler")
            self._heartbeat_task = asyncio.create_task(self._heartbeat_task_loop(), name="automation-heartbeat")
            self._session_task = (
                asyncio.create_task(self._session_monitor_loop(), name="automation-session-monitor")
                if SESSION_MONITOR_ENABLED
                else None
            )

        await self.publish_log("[SKOOL] Engine state recovered after restart", status="info")
    async def check_login(self, profile_id: str) -> Dict[str, Any]:
        profile = self._load_profile_for_session(profile_id)
        if not profile:
            raise RuntimeError(f"Profile '{profile_id}' not found")
        profile_name = (profile.get("name") or profile.get("label") or "Unknown profile").strip()
        if self._session_check_lock.locked():
            self._update_profile_status_in_db(profile_id, "queued")
            await self.publish_log(
                "[SKOOL] Login check queued: waiting for previous profile check",
                profile=profile_name,
                status="info",
            )

        async with self._session_check_lock:
            # Persist in DB immediately so page refresh/tab switch does not revert to stale status.
            self._update_profile_status_in_db(profile_id, "checking")

            def _run() -> Dict[str, Any]:
                manager = SkoolSessionManager(
                    account_id=profile_id,
                    email=profile.get("email", ""),
                    password=profile.get("password", ""),
                    proxy=profile.get("proxy"),
                    base_dir=self.accounts_dir,
                    headless=True,
                )
                try:
                    with _PLAYWRIGHT_SYNC_LOCK:
                        manager.launch()
                        session_status = manager.validate_session()
                        if session_status == "valid":
                            manager.update_state("ready")
                            cookie_json = manager.get_cookies_json()
                            return {"success": True, "status": "ready", "message": "Session is active", "cookie_json": cookie_json}
                        if session_status == "blocked":
                            manager.update_state("blocked")
                            return {"success": False, "status": "network_error", "message": "Account is blocked or access denied"}
                        if session_status == "captcha":
                            manager.update_state("captcha")
                            return {"success": False, "status": "captcha", "message": "Captcha required"}

                        if not profile.get("email") or not profile.get("password"):
                            manager.update_state("logged_out")
                            return {"success": False, "status": "invalid_credentials", "message": "Missing email or password"}

                        login_result = manager.perform_login()
                        manager.page.wait_for_timeout(2500)

                        if login_result == "success":
                            recheck = manager.validate_session()
                            if recheck == "valid":
                                manager.update_state("ready")
                                cookie_json = manager.get_cookies_json()
                                return {"success": True, "status": "ready", "message": "Session is active", "cookie_json": cookie_json}
                        if login_result == "failed":
                            manager.update_state("logged_out")
                            return {"success": False, "status": "invalid_credentials", "message": "Credentials are invalid"}
                        if login_result == "captcha" or manager.detect_captcha():
                            manager.update_state("captcha")
                            return {"success": False, "status": "captcha", "message": "Captcha required"}
                        if login_result == "blocked" or manager.detect_blocked():
                            manager.update_state("blocked")
                            return {"success": False, "status": "network_error", "message": "Account is blocked or access denied"}

                        current_url = str(getattr(manager.page, "url", "") or "").lower()
                        # Fallback: if chat page has auth markers, treat as valid instead of generic failure.
                        if "/login" not in current_url and manager.has_authenticated_markers():
                            manager.update_state("ready")
                            cookie_json = manager.get_cookies_json()
                            return {"success": True, "status": "ready", "message": "Session is active", "cookie_json": cookie_json}
                        if "/login" in current_url:
                            manager.update_state("logged_out")
                            return {
                                "success": False,
                                "status": "invalid_credentials",
                                "message": "Still on login page after login attempt",
                            }

                        manager.update_state("error")
                        return {
                            "success": False,
                            "status": "network_error",
                            "message": "Could not validate chat session (chat page/auth markers not detected)",
                        }
                except Exception as exc:
                    err = str(exc)
                    low = err.lower()
                    if "err_proxy_connection_failed" in low or "proxy" in low:
                        manager.update_state("proxy_error", {"error": err})
                        return {"success": False, "status": "proxy_error", "message": "Proxy connection failed"}
                    if "timeout" in low:
                        manager.update_state("error", {"error": err})
                        return {"success": False, "status": "network_error", "message": "Network timeout while checking login"}
                    if "net::" in low or "network" in low or "dns" in low:
                        manager.update_state("error", {"error": err})
                        return {"success": False, "status": "network_error", "message": "Network error while checking login"}
                    manager.update_state("error", {"error": err})
                    return {"success": False, "status": "network_error", "message": err[:300] or "Unknown login check error"}
                finally:
                    manager.close()

            try:
                result = await asyncio.to_thread(_run)
            except Exception as exc:
                result = {"success": False, "status": "network_error", "message": f"Login check failed: {str(exc)[:250]}"}

            if result.get("success") and result.get("cookie_json"):
                self._save_profile_cookie_json(profile_id, result["cookie_json"])

            profile_status = {
                "ready": "ready",
                "invalid_credentials": "logged_out",
                "captcha": "captcha",
                # Persist real connectivity failures so UI does not flip back to stale "ready" after refresh.
                "proxy_error": "disconnected",
                "network_error": "disconnected",
            }.get(result.get("status", ""), "disconnected")
            self._update_profile_status_in_db(profile_id, profile_status)
            public_login_message = _humanize_login_check_message(
                result.get("status", ""),
                result.get("message", ""),
            )
            await self.publish_log(
                f"[SKOOL] {public_login_message}",
                profile=profile_name,
                status=_session_check_log_status(
                    bool(result.get("success")),
                    str(result.get("status") or ""),
                ),
            )
            return result

    async def check_proxy(self, profile_id: str) -> Dict[str, Any]:
        async with self._session_check_lock:
            profile = self._load_profile_for_session(profile_id)
            if not profile:
                raise RuntimeError(f"Profile '{profile_id}' not found")
            profile_name = (profile.get("name") or profile.get("label") or "Unknown profile").strip()

            proxy_raw = (profile.get("proxy") or "").strip()
            if not proxy_raw:
                return {"success": False, "status": "no_proxy", "message": "Proxy is not configured"}

            proxy_url = _normalize_proxy_url(proxy_raw)

            def _run() -> Dict[str, Any]:
                try:
                    response = requests.get(
                        "https://api.ipify.org?format=json",
                        proxies={"http": proxy_url, "https": proxy_url},
                        timeout=15,
                    )
                    if response.status_code != 200:
                        return {"success": False, "status": "proxy_error", "message": f"Proxy test failed: HTTP {response.status_code}"}
                    ip = ""
                    try:
                        ip = (response.json() or {}).get("ip", "")
                    except Exception:
                        ip = ""
                    return {"success": True, "status": "connected", "message": f"Proxy is working{f' ({ip})' if ip else ''}"}
                except requests.Timeout:
                    return {"success": False, "status": "network_error", "message": "Proxy request timed out"}
                except requests.RequestException as exc:
                    low = str(exc).lower()
                    if "proxy" in low:
                        return {"success": False, "status": "proxy_error", "message": "Proxy connection failed"}
                    return {"success": False, "status": "network_error", "message": "Proxy network issue"}

            result = await asyncio.to_thread(_run)
            public_proxy_message = _humanize_proxy_check_message(
                result.get("status", ""),
                result.get("message", ""),
            )
            await self.publish_log(
                f"[SKOOL] {public_proxy_message}",
                profile=profile_name,
                status=_session_check_log_status(
                    bool(result.get("success")),
                    str(result.get("status") or ""),
                ),
            )
            return result

    async def run_test_comment(self, profile_id: str, community_url: str, prompt: str, api_key: Optional[str] = None) -> Dict[str, Any]:
        async with self._lock:
            if self._state.is_running:
                raise RuntimeError("Scheduler is running. Pause first.")

        profile = self._load_profile_for_session(profile_id)
        if not profile:
            raise RuntimeError(f"Profile '{profile_id}' not found")
        profile_name = (profile.get("name") or profile.get("label") or "Unknown profile").strip()

        def _run() -> Dict[str, Any]:
            manager = SkoolSessionManager(
                account_id=profile_id,
                email=profile.get("email", ""),
                password=profile.get("password", ""),
                proxy=profile.get("proxy"),
                base_dir=self.accounts_dir,
                headless=True,
            )
            try:
                manager.launch()
                page = manager.page
                page.goto(community_url, timeout=45000)
                page.wait_for_timeout(5000)

                if "login" in page.url.lower():
                    login_result = manager.perform_login()
                    if login_result != "success":
                        raise RuntimeError(f"Login failed: {login_result}")
                    page.goto(community_url, timeout=45000)
                    page.wait_for_timeout(5000)

                posts = page.query_selector_all(SKOOL_SELECTORS["post_items"])
                if not posts:
                    raise RuntimeError("No posts found")

                selected = posts[0]
                content_elem = selected.query_selector(SKOOL_SELECTORS["post_content"]) or selected
                post_text = selected.inner_text().strip()
                content_elem.click()
                page.wait_for_timeout(4000)
                post_url = page.url

                comment = _openai_generate_comment_rest(api_key or self._get_openai_key(), prompt, post_text[:4000])

                editor = page.wait_for_selector(SKOOL_SELECTORS["comment_editor"], timeout=20000)
                editor.click()
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                editor.type(comment, delay=25)
                sent, send_reason = self._submit_comment_with_fallback(page, comment)
                if not sent:
                    raise RuntimeError(f"send_or_dom_error:{send_reason}")
                page.wait_for_timeout(3000)

                return {"success": True, "postUrl": post_url, "aiReply": comment}
            finally:
                manager.close()

        result = await asyncio.to_thread(_run)
        await self.publish_log("[SKOOL] Test comment finished", profile=profile_name, status="success")
        return result

    async def proof_run(self, profile_id: str, community_url: str) -> Dict[str, Any]:
        async with self._lock:
            if self._state.is_running:
                raise RuntimeError("Scheduler is running. Pause first.")

        profile = self._load_profile_for_session(profile_id)
        if not profile:
            raise RuntimeError(f"Profile '{profile_id}' not found")
        profile_name = (profile.get("name") or profile.get("label") or "Unknown profile").strip()

        def _run() -> Dict[str, Any]:
            manager = SkoolSessionManager(
                account_id=profile_id,
                email=profile.get("email", ""),
                password=profile.get("password", ""),
                proxy=profile.get("proxy"),
                base_dir=self.accounts_dir,
                headless=True,
            )
            try:
                manager.launch()
                page = manager.page
                page.goto("https://www.skool.com/login", timeout=30000)
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(4000)

                if "/login" in page.url.lower():
                    if not profile.get("email") or not profile.get("password"):
                        manager.update_state("no_credentials")
                        raise RuntimeError("No credentials configured")
                    login_result = manager.perform_login()
                    if login_result != "success":
                        manager.update_state(login_result if login_result in {"blocked", "captcha"} else "error")
                        raise RuntimeError(f"Session init failed: {login_result}")

                page.goto(community_url, timeout=45000)
                page.wait_for_timeout(5000)

                if "login" in page.url.lower():
                    manager.update_state("error", {"error": "Redirected to login"})
                    raise RuntimeError("Still on login page")

                post_items = page.query_selector_all(SKOOL_SELECTORS["post_items"])
                if not post_items:
                    manager.update_state("error", {"error": "No posts found"})
                    raise RuntimeError("No posts found")

                blacklist = self._load_blacklist()
                selected_post = None
                post_url = None

                for post_item in post_items[:10]:
                    try:
                        links = post_item.query_selector_all(SKOOL_SELECTORS["post_links"])
                        if not links:
                            continue
                        href = links[-1].get_attribute("href")
                        if not href:
                            continue
                        if href.startswith("/"):
                            href = f"https://www.skool.com{href}"
                        norm_href = self._normalize_url(href)
                        preview_elem = post_item.query_selector(SKOOL_SELECTORS["post_content"])
                        preview_text = preview_elem.inner_text() if preview_elem else ""
                        if self._is_url_blacklisted(norm_href, blacklist, preview_text):
                            continue
                        selected_post = post_item
                        post_url = norm_href
                        break
                    except Exception:
                        continue

                if not selected_post or not post_url:
                    manager.update_state("error", {"error": "All posts blacklisted"})
                    raise RuntimeError("All posts blacklisted")

                title_elem = selected_post.query_selector(SKOOL_SELECTORS["post_title"])
                content_elem = selected_post.query_selector(SKOOL_SELECTORS["post_content"])
                post_text = f"{title_elem.inner_text() if title_elem else ''} {content_elem.inner_text() if content_elem else ''}".strip()

                if content_elem:
                    content_elem.click()
                else:
                    selected_post.click()
                page.wait_for_timeout(5000)

                comment_text = "Great insights here! What specific strategies have worked best for you so far?"
                api_key = self._get_openai_key()
                if api_key and post_text:
                    try:
                        ai_comment = _openai_generate_comment_rest(
                            api_key,
                            "Write a short, helpful comment under 40 words. Reference one point from the post. No emojis.",
                            post_text[:2000],
                        )
                        if ai_comment:
                            comment_text = ai_comment
                    except Exception:
                        pass

                editor = page.wait_for_selector(SKOOL_SELECTORS["comment_editor"], timeout=15000)
                editor.click()
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                editor.type(comment_text, delay=30)

                sent, send_reason = self._submit_comment_with_fallback(page, comment_text)
                if not sent:
                    raise RuntimeError(f"send_or_dom_error:{send_reason}")

                page.wait_for_timeout(8000)
                page.reload()
                page.wait_for_timeout(5000)
                verified = comment_text[:30] in page.content()

                self._add_to_blacklist(post_url, blacklist, post_text[:80] if post_text else None)
                self._save_blacklist(blacklist)

                current_state = manager.read_state() or {}
                manager.update_state(
                    "ready",
                    {
                        "progress_today": int(current_state.get("progress_today", 0)) + 1,
                        "last_comment_url": post_url,
                        "next_run_at": int(time.time()) + random.randint(
                            int(profile.get("delay_min", 30)),
                            int(profile.get("delay_max", 90)),
                        ),
                        "error": None,
                    },
                )

                return {
                    "success": True,
                    "postUrl": post_url,
                    "commentText": comment_text,
                    "verified": verified,
                }
            finally:
                manager.close()

        await self.publish_log("[SKOOL] ========== PROOF RUN STARTED ==========", profile=profile_name)
        result = await asyncio.to_thread(_run)
        await self.publish_log("[SKOOL] ========== PROOF RUN COMPLETED ==========", profile=profile_name, status="success")
        return result

    async def _heartbeat_task_loop(self) -> None:
        """Dedicated background task: write heartbeat every 30s regardless of scheduler state."""
        _log_ts = 0.0
        while True:
            _write_heartbeat(self.heartbeat_file)
            _now = time.time()
            if _now - _log_ts >= _HEARTBEAT_LOG_INTERVAL:
                LOGGER.info("HEARTBEAT ok ts=%d", int(_now))
                _log_ts = _now
            await asyncio.sleep(_HEARTBEAT_WRITE_INTERVAL)

    async def _scheduler_loop(self) -> None:
        await self.publish_log("[SKOOL] ===== SCHEDULER LOOP STARTED =====")
        await self.publish_log("[SKOOL] Round-robin mode: one profile action pass, then switch to next profile", status="info")
        # Do not force prefill on every scheduler start if queue already has tasks.
        bootstrap_queue_fill_required = False
        outside_schedule_log_ts: Dict[str, float] = {}
        outside_schedule_wait_log_ts = 0.0
        paused_wait_log_ts = 0.0
        due_backlog_log_ts = 0.0
        daily_exhausted_wait_log_ts = 0.0
        prefill_backoff_until: Dict[str, float] = {}
        profile_network_fail_streak: Dict[str, int] = {}
        profile_network_backoff_until: Dict[str, float] = {}
        profile_network_backoff_log_ts: Dict[str, float] = {}
        completed_rounds_since_rest = 0
        round_seen_profile_ids: Set[str] = set()
        connection_rest_until_ts = 0.0
        connection_rest_log_ts = 0.0
        rest_wait_due_drain_log_ts = 0.0
        async with self._lock:
            self._state.run_state = "running" if not self._state.is_paused else "paused"
            self._state.connection_rest_active = False
            self._state.connection_rest_remaining_seconds = 0
            self._state.connection_rest_rounds_before = 0
            self._state.connection_rest_rounds_completed = 0
            self._state.connection_rest_minutes = 0
            self._save_run_state_locked()

        while True:
            db_profiles, db_settings = await asyncio.to_thread(self._load_runtime_config_from_db)
            try:
                await asyncio.to_thread(self._reset_daily_counters_if_needed)
            except Exception:
                LOGGER.error(
                    "Daily counter reset failed (scheduler continues):\n%s",
                    traceback.format_exc(),
                )
            # IMPORTANT: reset may change daily counters in DB.
            # Reload runtime config so in-memory state reflects the post-reset truth.
            db_profiles, db_settings = await asyncio.to_thread(self._load_runtime_config_from_db)
            async with self._lock:
                if not self._state.is_running:
                    break
                self._state.last_scheduler_tick_ts = time.time()
                self._state.idle_mode = False
                if db_settings:
                    self._state.global_settings = dict(db_settings)
                # Apply profile/community changes from DB immediately on next scheduler loop.
                # This includes community enable/disable, limits, urls and profile paused status.
                if db_profiles:
                    self._refresh_runtime_profiles_locked(db_profiles)
                paused = self._state.is_paused
                if paused:
                    self._state.idle_mode = True
                    self._state.idle_reason = "paused"
                settings = dict(self._state.global_settings)
                profile, next_index = self._get_next_profile_locked()
                self._state.current_profile_index = next_index

            if paused:
                now_ts = time.time()
                if now_ts - paused_wait_log_ts >= 60:
                    paused_wait_log_ts = now_ts
                    await self.publish_log("[SKOOL] Scheduler paused: queue execution suspended", status="info")
                await asyncio.sleep(1)
                continue

            if settings.get("masterEnabled") is False:
                await self.publish_log("[SKOOL] Master automation disabled in settings; waiting", status="info")
                await self._countdown(10)
                continue

            rounds_before_rest = max(1, int(settings.get("roundsBeforeConnectionRest", 5) or 5))
            rest_minutes = max(1, int(settings.get("connectionRestMinutes", 5) or 5))
            rest_seconds = rest_minutes * 60

            now_rest_ts = time.time()
            if connection_rest_until_ts > now_rest_ts:
                remaining_rest = max(1, int(connection_rest_until_ts - now_rest_ts))
                async with self._lock:
                    self._state.run_state = "resting_connections"
                    self._state.connection_rest_active = True
                    self._state.connection_rest_remaining_seconds = remaining_rest
                    self._state.connection_rest_rounds_before = rounds_before_rest
                    self._state.connection_rest_rounds_completed = completed_rounds_since_rest
                    self._state.connection_rest_minutes = rest_minutes
                    self._save_run_state_locked()
                if (now_rest_ts - connection_rest_log_ts) >= 30:
                    connection_rest_log_ts = now_rest_ts
                    await self.publish_log(
                        (
                            "[SKOOL] Connection rest active: "
                            f"{remaining_rest}s remaining "
                            f"(completed rounds: {completed_rounds_since_rest}/{rounds_before_rest})"
                        ),
                        status="info",
                    )
                await self._countdown(remaining_rest)
                async with self._lock:
                    if not self._state.is_running:
                        break
                    self._state.run_state = "running" if not self._state.is_paused else "paused"
                    self._state.connection_rest_active = False
                    self._state.connection_rest_remaining_seconds = 0
                    self._state.connection_rest_rounds_before = rounds_before_rest
                    self._state.connection_rest_rounds_completed = 0
                    self._state.connection_rest_minutes = rest_minutes
                    self._save_run_state_locked()
                if time.time() >= connection_rest_until_ts:
                    connection_rest_until_ts = 0.0
                    completed_rounds_since_rest = 0
                    round_seen_profile_ids.clear()
                    await self.publish_log("[SKOOL] Connection rest finished, resuming automation", status="success")
                continue

            queue_total = await asyncio.to_thread(self._count_all_queue_actions)
            enabled_profiles_for_prefill = [
                p for p in self._state.profiles if p.get("enabled", True)
            ]
            per_profile_prefill_limit = max(
                1,
                int(settings.get("queuePrefillMaxPerProfilePerPass", QUEUE_PREFILL_MAX_PER_PROFILE_PER_PASS)),
            )
            prefill_target_total = max(1, len(enabled_profiles_for_prefill) * per_profile_prefill_limit)
            # Refill only when queue is fully drained; then prefill all profiles in one pass.
            should_prefill = bootstrap_queue_fill_required or queue_total == 0
            if should_prefill:
                async with self._lock:
                    scan_profiles = [dict(p) for p in self._state.profiles if p.get("enabled", True)]
                in_schedule_profiles: List[Dict[str, Any]] = []
                capacity_profiles: List[Dict[str, Any]] = []
                per_profile_prefill_limit = max(
                    1,
                    int(settings.get("queuePrefillMaxPerProfilePerPass", QUEUE_PREFILL_MAX_PER_PROFILE_PER_PASS)),
                )
                prefill_target_total = max(1, len(scan_profiles) * per_profile_prefill_limit)
                now_prefill_ts = time.time()
                pending_prefill: List[Dict[str, Any]] = []
                for p in scan_profiles:
                    if not self._check_schedule(p):
                        continue
                    in_schedule_profiles.append(p)
                    if not await asyncio.to_thread(self._profile_has_prefill_capacity_today, p):
                        continue
                    capacity_profiles.append(p)
                    p_key = str(p.get("id") or p.get("label") or p.get("name") or "")
                    retry_after = float(prefill_backoff_until.get(p_key, 0.0) or 0.0)
                    if retry_after > now_prefill_ts:
                        continue
                    pending_prefill.append(p)
                if not pending_prefill:
                    bootstrap_queue_fill_required = False
                    now_ts = time.time()
                    _use_idle_wait = False
                    _idle_reason_pending = "unknown"
                    if in_schedule_profiles and not capacity_profiles:
                        wait_seconds = max(60, min(1800, int(self._seconds_until_next_daily_reset())))
                        _use_idle_wait = True
                        _idle_reason_pending = "limits_reached"
                        if now_ts - daily_exhausted_wait_log_ts >= 300:
                            daily_exhausted_wait_log_ts = now_ts
                            await self.publish_log(
                                (
                                    "[SKOOL] All in-schedule communities reached today's limits; "
                                    f"waiting {wait_seconds}s for daily reset or settings changes"
                                ),
                                status="info",
                            )
                    elif in_schedule_profiles and capacity_profiles:
                        wait_seconds = max(10, min(120, int(self._seconds_until_next_run_for_all())))
                        _idle_reason_pending = "backoff"
                        if now_ts - outside_schedule_wait_log_ts >= 60:
                            outside_schedule_wait_log_ts = now_ts
                            await self.publish_log(
                                (
                                    "[SKOOL] Queue prefill is temporarily paused by network backoff; "
                                    f"waiting {wait_seconds}s"
                                ),
                                status="retry",
                            )
                    else:
                        wait_seconds = max(
                            5,
                            min(AUTOMATION_OUTSIDE_SCHEDULE_POLL_SECONDS, int(self._seconds_until_next_run_for_all())),
                        )
                        _idle_reason_pending = "outside_schedule"
                        async with self._lock:
                            self._state.run_state = "waiting_schedule"
                            self._save_run_state_locked()
                        # Throttle this message to avoid log spam when all profiles are outside schedule.
                        if now_ts - outside_schedule_wait_log_ts >= 60:
                            outside_schedule_wait_log_ts = now_ts
                            await self.publish_log(
                                f"[SKOOL] All profiles are outside schedule; waiting {wait_seconds}s until next run window",
                                status="info",
                            )
                    async with self._lock:
                        self._state.idle_mode = True
                        self._state.idle_reason = _idle_reason_pending
                    if _use_idle_wait:
                        await self._idle_daily_reset_wait(wait_seconds)
                    else:
                        await self._countdown(wait_seconds)
                    async with self._lock:
                        self._state.idle_mode = False
                    continue
                await self.publish_log("[SKOOL] Queue prefill started for all profiles", status="info")
                for prefill_round in range(2):
                    current_total = await asyncio.to_thread(self._count_all_queue_actions)
                    if current_total >= prefill_target_total:
                        await self.publish_log(
                            f"[SKOOL] Queue prefill stopped: queue_target_reached current={current_total}/{prefill_target_total}",
                            status="info",
                        )
                        pending_prefill = []
                        break
                    async with self._lock:
                        should_abort_prefill = (not self._state.is_running) or self._state.is_paused
                    if should_abort_prefill:
                        break
                    if not pending_prefill:
                        break
                    next_pending: List[Dict[str, Any]] = []
                    for scan_idx, scan_profile in enumerate(pending_prefill):
                        current_total = await asyncio.to_thread(self._count_all_queue_actions)
                        if current_total >= prefill_target_total:
                            await self.publish_log(
                                f"[SKOOL] Queue prefill stopped: queue_target_reached current={current_total}/{prefill_target_total}",
                                status="info",
                            )
                            next_pending = []
                            break
                        async with self._lock:
                            should_abort_prefill = (not self._state.is_running) or self._state.is_paused
                        if should_abort_prefill:
                            break
                        scan_label = scan_profile.get("label") or scan_profile.get("name") or scan_profile.get("id")
                        await self.publish_log(f"[SKOOL] Queue prefill: {scan_label}", profile=scan_label, status="info")
                        try:
                            await asyncio.to_thread(
                                self._run_profile_automation_sync,
                                scan_profile,
                                settings,
                                True,
                                scan_idx * 75,
                                prefill_target_total,
                            )
                        except Exception as exc:
                            err_text = str(exc or "")
                            err_lower = err_text.lower()
                            await self.publish_log(f"[SKOOL] Queue prefill error: {err_text}", profile=scan_label, status="error")
                            if "network timeout" in err_lower:
                                # Back off noisy network failures to avoid retry storms.
                                scan_key = str(scan_profile.get("id") or scan_profile.get("label") or scan_profile.get("name") or "")
                                prefill_backoff_until[scan_key] = time.time() + 120.0
                                await self.publish_log(
                                    "[SKOOL] Queue prefill network backoff: 120s",
                                    profile=scan_label,
                                    status="retry",
                                )
                            else:
                                next_pending.append(scan_profile)
                        await asyncio.sleep(0.25)
                    pending_prefill = next_pending
                    if pending_prefill:
                        await self.publish_log(
                            f"[SKOOL] Queue prefill retry round {prefill_round + 2}: {len(pending_prefill)} profile(s) pending",
                            status="retry",
                        )
                bootstrap_queue_fill_required = False

            if not profile:
                async with self._lock:
                    self._state.run_state = "completed"
                    self._state.connection_rest_active = False
                    self._state.connection_rest_remaining_seconds = 0
                    self._state.connection_rest_rounds_before = rounds_before_rest
                    self._state.connection_rest_rounds_completed = 0
                    self._state.connection_rest_minutes = rest_minutes
                    self._save_run_state_locked()
                await self.publish_log("[SKOOL] ===== ALL ACCOUNTS COMPLETED =====")
                await self._countdown(self._seconds_until_next_run_for_all())
                async with self._lock:
                    if not self._state.is_running:
                        break
                    for p in self._state.profiles:
                        p["visitsCompleted"] = 0
                        p["repliesCompleted"] = 0
                        p["status"] = "idle"
                    self._state.current_profile_index = 0
                    self._state.run_state = "running"
                    self._state.connection_rest_active = False
                    self._state.connection_rest_remaining_seconds = 0
                    self._state.connection_rest_rounds_before = rounds_before_rest
                    self._state.connection_rest_rounds_completed = 0
                    self._state.connection_rest_minutes = rest_minutes
                    self._state.stats = {
                        "total_comments": 0,
                        "total_skipped": 0,
                        "total_blacklisted": 0,
                    }
                    self._save_run_state_locked()
                continue

            profile_key = str(profile.get("id") or profile.get("label") or profile.get("name") or "")
            now_profile_ts = time.time()
            backoff_until = float(profile_network_backoff_until.get(profile_key, 0.0) or 0.0)
            if backoff_until > now_profile_ts:
                remaining = max(1, int(backoff_until - now_profile_ts))
                last_backoff_log = float(profile_network_backoff_log_ts.get(profile_key, 0.0) or 0.0)
                if (now_profile_ts - last_backoff_log) >= 30:
                    profile_network_backoff_log_ts[profile_key] = now_profile_ts
                    await self.publish_log(
                        f"[SKOOL] Profile temporary network cooldown: {remaining}s",
                        profile=profile.get("label", profile.get("id", "unknown")),
                        status="retry",
                    )
                await asyncio.sleep(0.2)
                continue

            if not self._check_schedule(profile):
                profile_key = str(profile.get("id") or profile.get("label") or profile.get("name") or "")
                now_ts = time.time()
                last_ts = float(outside_schedule_log_ts.get(profile_key, 0.0))
                # Throttle per-profile outside-schedule noise.
                if now_ts - last_ts >= 60:
                    outside_schedule_log_ts[profile_key] = now_ts
                    await self.publish_log(f"[SKOOL] Profile '{profile.get('label', profile.get('id'))}' outside schedule")
                await asyncio.sleep(1)
                continue

            async with self._lock:
                if not self._state.is_running or self._state.is_paused:
                    continue
                self._state.run_state = "running"
                self._save_run_state_locked()
            label = profile.get("label") or profile.get("name") or profile.get("id")
            await self.publish_log(f"[SKOOL] Running profile {label}", profile=label)
            comments_posted_this_pass = 0
            had_due_for_profile_pass = False
            try:
                profile_for_run = dict(profile)
                profile_for_run["repliesPerVisit"] = 1
                run_result = await asyncio.to_thread(self._run_profile_automation_sync, profile_for_run, settings, False, 0)
                comments_posted_this_pass = int(run_result.comments_posted or 0)
                had_due_for_profile_pass = int(run_result.due_queue_items_seen or 0) > 0
                run_network_errors = int(run_result.network_error_count or 0)
                if comments_posted_this_pass > 0:
                    profile_network_fail_streak.pop(profile_key, None)
                    profile_network_backoff_until.pop(profile_key, None)
                    profile_network_backoff_log_ts.pop(profile_key, None)
                elif run_network_errors > 0:
                    streak = int(profile_network_fail_streak.get(profile_key, 0) or 0) + 1
                    profile_network_fail_streak[profile_key] = streak
                    cooldown_seconds = min(300, 20 * (2 ** max(0, streak - 1)))
                    profile_network_backoff_until[profile_key] = time.time() + float(cooldown_seconds)
                    await self.publish_log(
                        f"[SKOOL] Profile network backoff applied: streak={streak} cooldown={int(cooldown_seconds)}s",
                        profile=label,
                        status="retry",
                    )
                else:
                    current_streak = int(profile_network_fail_streak.get(profile_key, 0) or 0)
                    if current_streak > 0:
                        profile_network_fail_streak[profile_key] = max(0, current_streak - 1)
                await self.publish_log(
                    (
                        f"[SKOOL] Profile pass done: posted={int(run_result.comments_posted or 0)} "
                        f"skipped={int(run_result.skipped_count or 0)} "
                        f"blacklisted={int(run_result.blacklisted_count or 0)}"
                    ),
                    profile=label,
                    status="success" if int(run_result.comments_posted or 0) > 0 else "info",
                )
                async with self._lock:
                    applied_profile = run_result.profile
                    applied_profile["visitsCompleted"] = applied_profile.get("visitsCompleted", 0) + max(0, run_result.comments_posted)
                    if applied_profile["visitsCompleted"] >= applied_profile.get("visits", 5):
                        applied_profile["status"] = "finished"
                    if run_result.comments_posted > 0:
                        self._increment_profile_daily_usage(applied_profile.get("id"), run_result.comments_posted)
                    self._state.stats["total_comments"] += run_result.comments_posted
                    self._state.stats["total_skipped"] += run_result.skipped_count
                    self._state.stats["total_blacklisted"] += run_result.blacklisted_count
                    self._state.activity_rows.extend(run_result.activity_rows)
                    self._persist_activity_rows(run_result.activity_rows)
                    self._update_profile_locked(applied_profile)
                    self._save_run_state_locked()
                self._last_action = {
                    "type": "profile_run",
                    "profile_id": applied_profile.get("id"),
                    "profile_label": applied_profile.get("label"),
                    "comments_posted": run_result.comments_posted,
                    "ts": datetime.now().isoformat(),
                }
            except Exception as exc:
                err_text = str(exc or "")
                self._last_error = {"message": err_text[:500], "ts": datetime.now().isoformat()}
                if "network timeout" in err_text.lower():
                    streak = int(profile_network_fail_streak.get(profile_key, 0) or 0) + 1
                    profile_network_fail_streak[profile_key] = streak
                    cooldown_seconds = min(300, 20 * (2 ** max(0, streak - 1)))
                    profile_network_backoff_until[profile_key] = time.time() + float(cooldown_seconds)
                    await self.publish_log(
                        (
                            "[SKOOL] Profile pass skipped: network timeout opening Skool entry page "
                            f"(cooldown={int(cooldown_seconds)}s)"
                        ),
                        profile=label,
                        status="retry",
                    )
                else:
                    await self.publish_log(f"[SKOOL] Scheduler error: {err_text}", profile=label, status="error")

            delay_min = max(30, int(profile.get("delayBetweenMessagesMinSec", profile.get("delay_min", settings.get("delayMin", 30)))))
            delay_max = max(delay_min, int(profile.get("delayBetweenMessagesMaxSec", profile.get("delay_max", settings.get("delayMax", 90)))))
            if delay_max < delay_min:
                delay_min, delay_max = delay_max, delay_min
            random_delay = random.randint(delay_min, delay_max)
            # Keep idle profile passes from collapsing to very short loops.
            # This avoids accidental high-frequency rotation across many accounts.
            wait_seconds = (
                max(AUTOMATION_POSTED_WAIT_MIN_SECONDS, random_delay)
                if comments_posted_this_pass > 0
                else max(AUTOMATION_NO_POST_WAIT_SECONDS, random_delay)
            )
            due_pending = await asyncio.to_thread(self._count_due_queue_actions)
            now_ts = time.time()
            if due_pending > 0:
                # Do not keep due tasks in "overdue/waiting" state for long.
                # While another profile is in pass cooldown, shift due tasks forward
                # to the next runnable slot so UI countdowns remain aligned with actual execution.
                # Keep defer short to avoid visible timer "bounce back" (e.g. Starting -> +50s).
                # Due tasks should run on the next profile turn, not be pushed by a full idle delay.
                wait_seconds = max(12, min(wait_seconds, 18))
                shifted_due = await asyncio.to_thread(self._defer_due_queue_items, max(1, wait_seconds))
                if shifted_due > 0 and (now_ts - due_backlog_log_ts) >= 30:
                    due_backlog_log_ts = now_ts
                    await self.publish_log(
                        (
                            "[SKOOL] Pending due tasks deferred to next profile slot: "
                            f"count={shifted_due} delay={max(1, wait_seconds)}s"
                        ),
                        status="info",
                    )
                elif (now_ts - due_backlog_log_ts) >= 30:
                    due_backlog_log_ts = now_ts
                    await self.publish_log(
                        (
                            "[SKOOL] Pending due tasks waiting for next profile pass: "
                            f"{due_pending} delay={max(1, wait_seconds)}s"
                        ),
                        status="info",
                    )

            rest_started_this_pass = False
            if completed_rounds_since_rest >= rounds_before_rest:
                due_pending_before_rest = await asyncio.to_thread(self._count_due_queue_actions)
                queue_pending_before_rest = await asyncio.to_thread(self._count_all_queue_actions)
                if due_pending_before_rest <= 0 and queue_pending_before_rest <= 0:
                    connection_rest_until_ts = time.time() + float(rest_seconds)
                    connection_rest_log_ts = 0.0
                    rest_started_this_pass = True
                    await self.publish_log(
                        (
                            "[SKOOL] Connection rest started: "
                            f"{rest_minutes} min after {completed_rounds_since_rest} rounds"
                        ),
                        status="info",
                    )
                else:
                    now_wait_ts = time.time()
                    if (now_wait_ts - rest_wait_due_drain_log_ts) >= 20:
                        rest_wait_due_drain_log_ts = now_wait_ts
                        await self.publish_log(
                            (
                                "[SKOOL] Connection rest delayed: "
                                f"waiting for queue to drain (due={due_pending_before_rest}, total={queue_pending_before_rest})"
                            ),
                            status="info",
                        )
            if had_due_for_profile_pass:
                profile_id_for_round = str(profile.get("id") or "")
                if profile_id_for_round:
                    round_seen_profile_ids.add(profile_id_for_round)
                async with self._lock:
                    enabled_profile_ids = {
                        str(p.get("id") or "")
                        for p in self._state.profiles
                        if p.get("enabled", True) and str(p.get("id") or "")
                    }
                if enabled_profile_ids and round_seen_profile_ids.issuperset(enabled_profile_ids):
                    completed_rounds_since_rest += 1
                    round_seen_profile_ids.clear()
                    async with self._lock:
                        self._state.connection_rest_rounds_before = rounds_before_rest
                        self._state.connection_rest_rounds_completed = completed_rounds_since_rest
                        self._state.connection_rest_minutes = rest_minutes
                        self._save_run_state_locked()
            else:
                # No due queue work in this pass: do not accumulate connection-rest rounds.
                round_seen_profile_ids.clear()
            if rest_started_this_pass:
                # Start connection-rest immediately after threshold round is reached.
                continue
            await self._countdown(max(1, wait_seconds))

        async with self._lock:
            if self._task is asyncio.current_task():
                self._task = None
            await self.publish_log("[SKOOL] Scheduler loop ended")

    async def _session_monitor_loop(self) -> None:
        while True:
            async with self._lock:
                if not self._state.is_running:
                    break
                profiles = [dict(p) for p in self._state.profiles if p.get("enabled", True)]

            for profile in profiles:
                try:
                    status = await asyncio.to_thread(self._check_profile_session_status_sync, profile)
                    await self.publish_log(
                        f"[SKOOL] Session check: {profile.get('label', profile.get('id'))} -> {status}",
                        profile=profile.get("label", profile.get("id", "unknown")),
                        status="info",
                    )
                except Exception:
                    continue
                await asyncio.sleep(2)

            await asyncio.sleep(1800)

        async with self._lock:
            if self._session_task is asyncio.current_task():
                self._session_task = None

    async def get_debug_snapshot(self) -> Dict[str, Any]:
        """Read-only scheduler diagnostics. Cached for 10s to avoid repeated DB hits."""
        now = time.time()
        if now - self._debug_snapshot_ts < 10.0 and self._debug_snapshot_cache:
            return dict(self._debug_snapshot_cache)

        # Heartbeat age
        hb_age: Optional[float] = None
        try:
            hb_data = json.loads(self.heartbeat_file.read_text(encoding="utf-8"))
            hb_age = round(time.time() - float(hb_data.get("ts", 0)), 1)
        except Exception:
            pass

        # last_reset_date from daily counters state file
        last_reset_date: Optional[str] = None
        try:
            dc = json.loads(self.daily_counters_state_file.read_text(encoding="utf-8"))
            last_reset_date = dc.get("last_reset_date")
        except Exception:
            pass

        # Snapshot mutable state under lock
        async with self._lock:
            is_running = self._state.is_running
            is_paused = self._state.is_paused
            idle_mode = self._state.idle_mode
            idle_reason_raw = self._state.idle_reason
            tick_ts = self._state.last_scheduler_tick_ts
            profiles = list(self._state.profiles)
            settings = dict(self._state.global_settings)

        tick_iso = datetime.fromtimestamp(tick_ts).isoformat() if tick_ts else None
        tick_age = round(time.time() - tick_ts, 1) if tick_ts else None

        # Derive idle_reason
        if not is_running:
            idle_reason: Optional[str] = "stopped"
        elif is_paused:
            idle_reason = "paused"
        elif idle_mode:
            idle_reason = idle_reason_raw
        else:
            idle_reason = None

        # Counts derived from in-memory profiles (no extra DB query)
        profiles_total = len(profiles)
        profiles_active = sum(
            1 for p in profiles
            if str(p.get("status", "")).lower() not in {"stopped", "paused", "finished"}
        )
        communities_all = [c for p in profiles for c in (p.get("communities") or [])]
        communities_total = len(communities_all)
        in_schedule = self._check_schedule(settings)
        communities_in_schedule = communities_total if in_schedule else 0
        communities_reached = sum(
            1 for c in communities_all
            if str(c.get("status", "active")).lower() == "active"
            and int(c.get("dailyLimit") or 0) > 0
            and int(c.get("actionsToday") or 0) >= int(c.get("dailyLimit") or 0)
        )

        # Queue pending: single cheap COUNT(*)
        queue_pending = 0
        try:
            queue_pending = await asyncio.to_thread(self._count_all_queue_actions)
        except Exception:
            pass

        secs_to_reset: Optional[int] = None
        if is_running:
            try:
                secs_to_reset = self._seconds_until_next_daily_reset()
            except Exception:
                pass

        # state_day from run_state file
        run_state_day: Optional[str] = None
        run_state_last_updated: Optional[str] = None
        try:
            rs = json.loads(self.run_state_file.read_text(encoding="utf-8"))
            run_state_day = rs.get("state_day")
            run_state_last_updated = rs.get("last_updated")
        except Exception:
            pass
        today_str = datetime.now().strftime("%Y-%m-%d")

        result: Dict[str, Any] = {
            "now_iso": datetime.now().isoformat(),
            "heartbeat_age_seconds": hb_age,
            "engine_running": is_running,
            "engine_paused": is_paused,
            "state_day": run_state_day,
            "state_day_is_stale": bool(run_state_day and run_state_day != today_str),
            "last_recover_reason": self._last_recover_reason,
            "run_state_last_updated_iso": run_state_last_updated,
            "last_scheduler_tick_iso": tick_iso,
            "last_scheduler_tick_age_seconds": tick_age,
            "last_reset_date": last_reset_date,
            "seconds_until_next_daily_reset": secs_to_reset,
            "idle_mode": idle_mode,
            "idle_reason": idle_reason,
            "counts": {
                "profiles_total": profiles_total,
                "profiles_active": profiles_active,
                "communities_total": communities_total,
                "communities_in_schedule": communities_in_schedule,
                "communities_reached_limit": communities_reached,
                "queue_pending": queue_pending,
            },
            "last_action": self._last_action or None,
            "last_error": self._last_error or None,
        }
        self._debug_snapshot_cache = result
        self._debug_snapshot_ts = time.time()
        return result

    def _compute_settings_etag(self) -> str:
        """Hash the DB rows that drive capacity decisions so idle can detect changes."""
        import hashlib
        try:
            with self._db() as db:
                settings_row = db.execute(
                    "SELECT value FROM automation_settings WHERE key = 'default'"
                ).fetchone()
                communities = db.execute(
                    "SELECT id, dailyLimit, status FROM communities ORDER BY id"
                ).fetchall()
            parts = [
                (settings_row["value"] if settings_row else ""),
                json.dumps([dict(c) for c in communities], sort_keys=True),
            ]
            return hashlib.md5("|".join(parts).encode()).hexdigest()
        except Exception:
            return self._settings_etag  # don't break on transient DB error

    async def _idle_daily_reset_wait(self, initial_seconds: int) -> None:
        """Interruptible idle wait: wakes at reset time or on settings change."""
        _TICK = 5.0
        _ETAG_INTERVAL = 10.0
        deadline = time.time() + initial_seconds
        _fake_reset = int(os.environ.get("ENGAGEFLOW_DEBUG_FAKE_RESET_SECONDS", "0") or "0")
        reset_time = time.time() + (_fake_reset if _fake_reset > 0 else self._seconds_until_next_daily_reset())
        # Seed etag on first entry so we detect *changes* from this point forward, not from "".
        if not self._settings_etag:
            self._settings_etag = await asyncio.to_thread(self._compute_settings_etag)
        etag_check_ts = time.time()  # first real check in _ETAG_INTERVAL seconds
        while time.time() < deadline:
            async with self._lock:
                if not self._state.is_running or self._state.is_paused:
                    return
                self._state.last_scheduler_tick_ts = time.time()
            now = time.time()
            if now >= reset_time:
                await self.publish_log(
                    "[SKOOL] Daily reset time reached; resuming scheduler immediately",
                    status="info",
                )
                return
            if now - etag_check_ts >= _ETAG_INTERVAL:
                etag_check_ts = now
                new_etag = await asyncio.to_thread(self._compute_settings_etag)
                if new_etag != self._settings_etag:
                    self._settings_etag = new_etag
                    await self.publish_log(
                        "[SKOOL] Settings change detected during idle; resuming immediately",
                        status="info",
                    )
                    return
            await asyncio.sleep(min(_TICK, max(0.1, deadline - time.time())))

    async def _countdown(self, seconds: int) -> None:
        for remaining in range(seconds, 0, -1):
            async with self._lock:
                if not self._state.is_running:
                    self._state.countdown_seconds = 0
                    return
                if self._state.is_paused:
                    self._state.countdown_seconds = 0
                    return
                self._state.countdown_seconds = remaining
                self._state.last_scheduler_tick_ts = time.time()
                if self._state.connection_rest_active:
                    # Keep the visible connection-rest timer ticking each second.
                    self._state.connection_rest_remaining_seconds = remaining
            await asyncio.sleep(1)
        async with self._lock:
            self._state.countdown_seconds = 0
            if self._state.connection_rest_active:
                self._state.connection_rest_remaining_seconds = 0

    def _run_profile_automation_sync(
        self,
        profile: Dict[str, Any],
        settings: Dict[str, Any],
        scan_only: bool = False,
        queue_stagger_seconds: int = 0,
        prefill_target_total: Optional[int] = None,
    ) -> ProfileRunResult:
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not available")

        profile = dict(profile)
        profile_id = profile.get("id")
        profile_label = profile.get("label") or profile.get("name") or profile_id
        if self._ensure_profile_auth_fn:
            auth = self._ensure_profile_auth_fn(profile_id)
            if not auth.get("ok"):
                LOGGER.info("[SKOOL] Profile %s blocked_auth status=%s message=%s refresh_attempted=%s",
                    profile_label, auth.get("status"), auth.get("message"), auth.get("refresh_attempted"))
                result = ProfileRunResult(profile=profile)
                result.comments_posted = 0
                return result
        result = ProfileRunResult(profile=profile)
        browser_profile_dir = self.accounts_dir / profile_id / "browser"
        browser_profile_dir.mkdir(parents=True, exist_ok=True)

        raw_proxy_value = profile.get("proxy") or settings.get("proxy")
        proxy_cfg = _parse_proxy_to_playwright(raw_proxy_value)
        api_key = settings.get("apiKey") or self._get_openai_key()
        if not api_key:
            raise RuntimeError("OpenAI API key missing")

        comment_fallback_enabled = bool(settings.get("commentFallbackEnabled", True))
        keyword_scanning_enabled = bool(settings.get("keywordScanningEnabled", True))
        pre_scan_enabled = bool(settings.get("preScanEnabled", True))
        posts_per_scan = max(1, int(settings.get("postsPerCommunityPerScan", 15)))
        posts_scan_limit = max(1, int(settings.get("postsPerCommunityScanLimit", posts_per_scan)))
        raw_trace_selection = settings.get("postSelectionTraceEnabled", False)
        if isinstance(raw_trace_selection, str):
            trace_selection_logs = raw_trace_selection.strip().lower() in {"1", "true", "yes", "on"}
        else:
            trace_selection_logs = bool(raw_trace_selection)
        blacklist_enabled = bool(settings.get("blacklistEnabled", False))
        blacklist_terms = [str(term).strip().lower() for term in (settings.get("blacklistTerms") or []) if str(term).strip()]

        general_prompt = profile.get("generalEngagementPrompt") or settings.get("commentFallbackPrompt") or "Write a short helpful comment under 40 words."
        keyword_prompt = profile.get("keywordTriggeredPrompt") or general_prompt
        keywords = [str(kw) for kw in (profile.get("keywords", []) or []) if str(kw).strip()]
        per_pass_cap = max(
            1,
            int(settings.get("queuePrefillMaxPerProfilePerPass", QUEUE_PREFILL_MAX_PER_PROFILE_PER_PASS)),
        )
        scan_prefill_added_total = 0
        scan_prefill_keyword_hits_total = 0

        def _fmt_post_time(ts: Optional[float]) -> str:
            if ts is None:
                return "unknown"
            try:
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                age_days = max(0.0, (datetime.now(timezone.utc).timestamp() - float(ts)) / 86400.0)
                return f"{dt.isoformat()} age_days={age_days:.2f}"
            except Exception:
                return "unparsed"

        def _queue_post_key(post_url_value: str) -> str:
            normalized = self._normalize_url(post_url_value) or str(post_url_value or "").strip()
            return f"{str(profile_id or '').strip()}::{normalized}"

        def _clear_queue_network_failure(post_url_value: str) -> None:
            key = _queue_post_key(post_url_value)
            if key:
                self._queue_network_fail_streak.pop(key, None)
                self._queue_submit_fail_streak.pop(key, None)
                self._queue_budget_exhaust_streak.pop(key, None)

        def _register_retry_budget_exhausted(post_url_value: str) -> Tuple[bool, int]:
            key = _queue_post_key(post_url_value)
            if not key:
                return False, 0
            cycle = int(self._queue_budget_exhaust_streak.get(key, 0) or 0) + 1
            self._queue_budget_exhaust_streak[key] = cycle
            if cycle >= QUEUE_NETWORK_MAX_BUDGET_EXHAUST_CYCLES:
                self._queue_post_cooldown_until[key] = time.time() + float(QUEUE_NETWORK_PERSISTENT_FAIL_COOLDOWN_SECONDS)
                return True, cycle
            return False, cycle

        def _queue_community_key(community_id_value: str, community_url_value: str) -> str:
            comm_key = str(community_id_value or "").strip()
            if not comm_key:
                comm_key = self._normalize_url(community_url_value) or str(community_url_value or "").strip()
            return f"{str(profile_id or '').strip()}::community::{comm_key}"

        def _community_cooldown_remaining(community_id_value: str, community_url_value: str) -> int:
            key = _queue_community_key(community_id_value, community_url_value)
            if not key:
                return 0
            until = float(self._queue_community_cooldown_until.get(key, 0.0) or 0.0)
            if until <= 0:
                return 0
            remaining = int(until - time.time())
            if remaining <= 0:
                self._queue_community_cooldown_until.pop(key, None)
                return 0
            return remaining

        def _activate_community_network_cooldown(
            community_id_value: str,
            community_url_value: str,
            seconds: int,
        ) -> int:
            key = _queue_community_key(community_id_value, community_url_value)
            if not key:
                return max(60, int(seconds or 0))
            cooldown = max(60, int(seconds or 0))
            current_until = float(self._queue_community_cooldown_until.get(key, 0.0) or 0.0)
            target_until = max(current_until, time.time() + float(cooldown))
            self._queue_community_cooldown_until[key] = target_until
            return max(1, int(target_until - time.time()))

        def _is_queue_post_on_cooldown(post_url_value: str) -> bool:
            key = _queue_post_key(post_url_value)
            if not key:
                return False
            until = float(self._queue_post_cooldown_until.get(key, 0.0) or 0.0)
            if until <= 0:
                return False
            if time.time() >= until:
                self._queue_post_cooldown_until.pop(key, None)
                return False
            return True

        def _register_queue_network_failure(post_url_value: str) -> Tuple[bool, int, int]:
            key = _queue_post_key(post_url_value)
            if not key:
                return False, QUEUE_NETWORK_RETRY_BASE_SECONDS, 0
            attempt = int(self._queue_network_fail_streak.get(key, 0) or 0) + 1
            self._queue_network_fail_streak[key] = attempt
            if attempt > QUEUE_NETWORK_MAX_RETRIES_PER_POST:
                self._queue_network_fail_streak[key] = QUEUE_NETWORK_MAX_RETRIES_PER_POST
                self._queue_post_cooldown_until[key] = time.time() + float(QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS)
                return False, QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS, QUEUE_NETWORK_MAX_RETRIES_PER_POST
            delay = min(
                QUEUE_NETWORK_RETRY_MAX_DELAY_SECONDS,
                QUEUE_NETWORK_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)),
            )
            return True, int(delay), attempt

        def _register_queue_submit_failure(post_url_value: str) -> Tuple[bool, int, int]:
            key = _queue_post_key(post_url_value)
            if not key:
                return False, QUEUE_NETWORK_RETRY_BASE_SECONDS, 0
            attempt = int(self._queue_submit_fail_streak.get(key, 0) or 0) + 1
            self._queue_submit_fail_streak[key] = attempt
            if attempt > QUEUE_NETWORK_MAX_RETRIES_PER_POST:
                self._queue_submit_fail_streak[key] = QUEUE_NETWORK_MAX_RETRIES_PER_POST
                self._queue_post_cooldown_until[key] = time.time() + float(QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS)
                return False, QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS, QUEUE_NETWORK_MAX_RETRIES_PER_POST
            delay = min(
                QUEUE_NETWORK_RETRY_MAX_DELAY_SECONDS,
                QUEUE_NETWORK_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)),
            )
            return True, int(delay), attempt

        def _trace(message: str, status: str = "info", fallback_level: Optional[str] = None) -> None:
            if not trace_selection_logs:
                return
            self._insert_log(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "profile": str(profile_label),
                    "status": status,
                    "message": message,
                    "fallbackLevelUsed": fallback_level,
                }
            )

        def _prefill_skip_log_gate(key: str, cooldown_seconds: int = PREFILL_SKIP_LOG_COOLDOWN_SECONDS) -> Tuple[bool, int]:
            now_ts = float(time.time())
            state = self._prefill_skip_log_state.get(key) or {}
            last_ts = float(state.get("last", 0.0) or 0.0)
            suppressed = int(state.get("suppressed", 0) or 0)
            if (now_ts - last_ts) >= max(1, int(cooldown_seconds)):
                self._prefill_skip_log_state[key] = {"last": now_ts, "suppressed": 0}
                return True, suppressed
            state["suppressed"] = suppressed + 1
            self._prefill_skip_log_state[key] = state
            return False, int(state["suppressed"])

        launch_kwargs: Dict[str, Any] = {
            "user_data_dir": str(browser_profile_dir),
            "headless": True,
            "viewport": {"width": 1600, "height": 1100},
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        }
        if proxy_cfg:
            launch_kwargs["proxy"] = proxy_cfg

        playwright = None
        context = None
        proxy_slot_handle: Optional[Tuple[str, str]] = None
        try:
            proxy_slot_handle = acquire_proxy_slot("queue", raw_proxy_value)
            with _PLAYWRIGHT_SYNC_LOCK:
                playwright = _start_playwright_safe()
                try:
                    context = playwright.chromium.launch_persistent_context(**launch_kwargs)
                except Exception as launch_exc:
                    launch_err = str(launch_exc).lower()
                    if "connection closed while reading from the driver" in launch_err:
                        try:
                            playwright.stop()
                        except Exception:
                            pass
                        playwright = _start_playwright_safe()
                        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
                    else:
                        raise
            if context is None:
                raise RuntimeError("Failed to launch browser context")

            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_navigation_timeout(45000)
            page.set_default_timeout(30000)

            communities = profile.get("communities", [])
            if not communities:
                return result

            opened = False
            had_runtime_candidate = False
            if scan_only:
                # Prefill should fail fast to avoid stalling the whole scheduler on one slow profile.
                entry_urls = (
                    "https://www.skool.com/",
                    "https://www.skool.com/login",
                )
                nav_rounds = 2
                nav_base_timeout = 20000
                nav_step_timeout = 10000
                round_backoff_base = 900
                round_backoff_step = 600
            else:
                entry_urls = (
                    "https://www.skool.com/",
                    "https://www.skool.com/chat",
                    "https://www.skool.com/login",
                )
                nav_rounds = 3
                nav_base_timeout = 30000
                nav_step_timeout = 10000
                round_backoff_base = 1500
                round_backoff_step = 900
            for nav_round in range(nav_rounds):
                nav_timeout = nav_base_timeout + (nav_round * nav_step_timeout)
                for entry_url in entry_urls:
                    try:
                        page.goto(entry_url, wait_until="commit", timeout=nav_timeout)
                        page.wait_for_timeout(1200)
                        opened = True
                        break
                    except Exception:
                        continue
                if opened:
                    break
                page.wait_for_timeout(round_backoff_base + (nav_round * round_backoff_step))
            if not opened:
                # Entry page may timeout behind some proxies while community routes still work.
                # Fall back to direct community open before failing the profile.
                fallback_communities: List[str] = []
                for c in communities:
                    normalized = self._normalize_url(c.get("url"))
                    if normalized and normalized not in fallback_communities:
                        fallback_communities.append(normalized)
                    if len(fallback_communities) >= 3:
                        break
                for fallback_url in fallback_communities:
                    try:
                        page.goto(fallback_url, wait_until="commit", timeout=45000)
                        page.wait_for_timeout(1200)
                        opened = True
                        break
                    except Exception:
                        continue
            if not opened:
                raise RuntimeError("Unable to open Skool entry page (network timeout)")

            if "/login" in page.url.lower():
                email = profile.get("email")
                password = profile.get("password")
                if not email or not password:
                    self._insert_log(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "profile": str(profile_label),
                            "status": "retry" if scan_only else "error",
                            "message": (
                                "[SKOOL] Queue prefill skipped: missing_credentials"
                                if scan_only
                                else "[SKOOL] Profile pass failed: missing_credentials"
                            ),
                        }
                    )
                    profile["status"] = "logged_out"
                    return result
                page.fill("input#email", email)
                page.fill("input#password", password)
                page.click("button[type='submit']")
                page.wait_for_timeout(5000)
                if "/login" in page.url.lower():
                    self._insert_log(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "profile": str(profile_label),
                            "status": "retry" if scan_only else "error",
                            "message": (
                                "[SKOOL] Queue prefill skipped: login_required_after_submit"
                                if scan_only
                                else "[SKOOL] Profile pass failed: login_required_after_submit"
                            ),
                        }
                    )
                    profile["status"] = "logged_out"
                    return result

            if scan_only and prefill_target_total is not None:
                current_total = self._count_all_queue_actions()
                if current_total >= max(1, int(prefill_target_total)):
                    self._insert_log(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "profile": str(profile_label),
                            "status": "info",
                            "message": (
                                "[SKOOL] Queue prefill skipped: "
                                f"queue_target_reached current={current_total}/{int(prefill_target_total)}"
                            ),
                        }
                    )
                    return result

            self._prune_stale_queue(profile_id)
            self._dedupe_queue_items_for_profile(profile_id)
            commented_posts = self._load_profile_commented_posts(profile_id)
            self._prune_already_commented_queue_items(profile_id, commented_posts)
            due_queue_items = self._load_due_queue_items_for_profile(
                profile_id=profile_id,
                limit=max(1, int(profile.get("repliesPerVisit", 1)) * 4),
            ) if not scan_only else []
            result.due_queue_items_seen = len(due_queue_items)
            if not scan_only:
                self._insert_log(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "profile": str(profile_label),
                        "status": "success",
                        "message": f"[SKOOL] Due queue items: {len(due_queue_items)}",
                    }
                )
            due_by_community: Dict[str, List[Dict[str, str]]] = {}
            for item in due_queue_items:
                cid = str(item.get("community_id") or "").strip()
                due_by_community.setdefault(cid, []).append(item)
            if not scan_only and due_queue_items:
                known_community_ids = {str(c.get("id") or "").strip() for c in communities}
                for due in due_queue_items:
                    due_cid = str(due.get("community_id") or "").strip()
                    if due_cid and due_cid in known_community_ids:
                        continue
                    due_url = str(due.get("post_url") or "").strip()
                    if not due_url:
                        continue
                    task_ref = str(due.get("queue_id") or _extract_task_ref_from_post_url(due_url) or "n/a")
                    self._remove_queue_item(profile_id, due_url)
                    result.skipped_count += 1
                    self._insert_log(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "profile": str(profile_label),
                            "status": "error",
                            "message": f"[SKOOL] Comment failed task={task_ref} reason=community_not_found removed=1",
                        }
                    )

            replies_per_visit = int(profile.get("repliesPerVisit", 1))
            replies_this_visit = 0
            comm_index = int(profile.get("_current_community_index", 0)) % len(communities)
            prefill_stop_logged = False
            prefill_skip_reasons: Dict[str, int] = {}
            prefill_communities_checked = 0
            prefill_communities_with_posts = 0
            prefill_communities_with_eligible_posts = 0

            def _note_prefill_skip(reason: str) -> None:
                if not scan_only:
                    return
                reason_key = str(reason or "unknown").strip() or "unknown"
                prefill_skip_reasons[reason_key] = int(prefill_skip_reasons.get(reason_key, 0) or 0) + 1

            for _ in range(len(communities)):
                if scan_only and prefill_target_total is not None:
                    current_total = self._count_all_queue_actions()
                    if current_total >= max(1, int(prefill_target_total)):
                        break
                if replies_this_visit >= replies_per_visit:
                    break
                if scan_only:
                    queued_now_scan = self._count_pending_queue_for_profile(str(profile_id or ""))
                    daily_cap_scan = max(1, int(profile.get("visits", settings.get("globalDailyCapPerAccount", 5))))
                    done_today_scan = max(
                        0,
                        int(
                            max(
                                int(profile.get("visitsCompleted", 0) or 0),
                                int(profile.get("repliesCompleted", 0) or 0),
                            )
                        ),
                    )
                    remaining_quota_scan = max(0, daily_cap_scan - done_today_scan - queued_now_scan)
                    remaining_pass_slots_scan = max(0, per_pass_cap - scan_prefill_added_total)
                    if remaining_pass_slots_scan <= 0 or remaining_quota_scan <= 0:
                        _note_prefill_skip("no_scan_quota")
                        if not prefill_stop_logged:
                            reason = "per_pass_limit_reached" if remaining_pass_slots_scan <= 0 else "daily_quota_reached"
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": (
                                        "[SKOOL] Queue prefill stopped early: "
                                        f"reason={reason} queued={queued_now_scan} "
                                        f"pass_added={scan_prefill_added_total}/{per_pass_cap}"
                                    ),
                                }
                            )
                            prefill_stop_logged = True
                        break

                community = communities[comm_index % len(communities)]
                comm_index += 1
                if scan_only:
                    prefill_communities_checked += 1
                community_id = str(community.get("id", "")).strip()
                if str(community.get("status", "active")).lower() != "active":
                    if not scan_only and due_by_community.get(community_id):
                        for due in list(due_by_community.get(community_id) or []):
                            due_url = str(due.get("post_url") or "").strip()
                            if not due_url:
                                continue
                            task_ref = str(due.get("queue_id") or _extract_task_ref_from_post_url(due_url) or "n/a")
                            self._remove_queue_item(profile_id, due_url)
                            result.skipped_count += 1
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "error",
                                    "message": f"[SKOOL] Comment failed task={task_ref} reason=community_paused removed=1",
                                }
                            )
                    continue
                community_daily_limit = max(0, int(community.get("dailyLimit") or 0))
                max_post_age_days = max(0, int(community.get("maxPostAgeDays") or 0))
                community_actions_today = max(0, int(community.get("actionsToday") or 0))
                community_queued_today = self._count_pending_queue_for_profile_community_today(
                    str(profile_id or ""),
                    community_id,
                )
                community_used_today = community_actions_today + community_queued_today
                community_limit_reached = (
                    community_daily_limit > 0
                    and (
                        (scan_only and community_used_today >= community_daily_limit)
                        or ((not scan_only) and community_actions_today >= community_daily_limit)
                    )
                )
                if community_limit_reached:
                    _note_prefill_skip("daily_limit_reached")
                    if scan_only:
                        throttle_key = (
                            f"prefill_skip_daily::{str(profile_id or '').strip()}::{community_id or community_url}"
                        )
                        should_log, suppressed_count = _prefill_skip_log_gate(throttle_key)
                        if should_log:
                            suppressed_text = (
                                f" repeats_suppressed={suppressed_count}" if suppressed_count > 0 else ""
                            )
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": (
                                        "[SKOOL] Queue prefill skipped: "
                                        f"community_daily_limit_reached community={community.get('name', community_id)} "
                                        f"actions_today={community_actions_today} "
                                        f"queued_today={community_queued_today} limit={community_daily_limit}"
                                        f"{suppressed_text}"
                                    ),
                                }
                            )
                        elif trace_selection_logs and (suppressed_count % PREFILL_SKIP_DEBUG_EVERY == 0):
                            _trace(
                                "[SKOOL][TRACE] Queue prefill skip suppressed: "
                                f"community_daily_limit_reached community={community.get('name', community_id)} "
                                f"count={suppressed_count}",
                                status="info",
                            )
                    if not scan_only and due_by_community.get(community_id):
                        removed = 0
                        for due in list(due_by_community.get(community_id) or []):
                            due_url = str(due.get("post_url") or "").strip()
                            if not due_url:
                                continue
                            task_ref = str(due.get("queue_id") or _extract_task_ref_from_post_url(due_url) or "n/a")
                            self._remove_queue_item(profile_id, due_url)
                            removed += 1
                            result.skipped_count += 1
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "error",
                                    "message": f"[SKOOL] Comment failed task={task_ref} reason=community_daily_limit_reached removed=1",
                                }
                            )
                        if removed > 0:
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": (
                                        "[SKOOL] Queue cleanup: "
                                        f"community_daily_limit_reached community={community.get('name', community_id)} "
                                        f"removed={removed}"
                                    ),
                                }
                            )
                    _trace(
                        f"[SKOOL][TRACE] Community skipped: daily_limit_reached "
                        f"name={community.get('name', community_id)} actions_today={community_actions_today} "
                        f"queued_today={community_queued_today} "
                        f"limit={community_daily_limit}"
                    )
                    continue
                community_url = self._normalize_url(community.get("url"))
                if not community_url:
                    _note_prefill_skip("missing_community_url")
                    continue
                community_name = str(community.get("name") or community_id or community_url or "community")
                community_cooldown_remaining = _community_cooldown_remaining(community_id, community_url)
                if community_cooldown_remaining > 0:
                    _note_prefill_skip("community_network_cooldown")
                    if scan_only:
                        _trace(
                            f"[SKOOL][TRACE] Community skipped: network_cooldown_active "
                            f"name={community_name} remaining={community_cooldown_remaining}s",
                            status="retry",
                        )
                        continue
                    # Runtime queue execution should not be hard-blocked by community cooldown.
                    # Keep processing due tasks to avoid long freezes on temporary network issues.

                blacklist = self._load_blacklist()
                queued_for_community = due_by_community.get(community_id, [])
                eligible_posts: List[Dict[str, Any]] = []
                if not scan_only and not queued_for_community:
                    # Runtime mode must execute only already-scheduled due queue tasks.
                    continue
                _trace(
                    f"[SKOOL][TRACE] Community scan started: name={community.get('name', community_url)} "
                    f"queued_items={len(queued_for_community)} age_limit_days_config={max_post_age_days} (ignored)"
                )
                if queued_for_community and not scan_only:
                    for item in queued_for_community:
                        post_url = str(item.get("post_url") or "").strip()
                        queue_task_id = str(item.get("queue_id") or "").strip()
                        if not post_url:
                            continue
                        norm_post_url = self._normalize_url(post_url)
                        if norm_post_url and norm_post_url in commented_posts:
                            self._remove_queue_item(profile_id, post_url)
                            result.skipped_count += 1
                            task_ref = str(queue_task_id or _extract_task_ref_from_post_url(post_url) or "n/a")
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "error",
                                    "message": f"[SKOOL] Comment failed task={task_ref} reason=already_commented_history removed=1",
                                }
                            )
                            _trace(f"[SKOOL][TRACE] Queue candidate skipped: already_commented post={post_url}", fallback_level="skip")
                            continue
                        _trace(f"[SKOOL][TRACE] Queue candidate accepted: post={post_url} time=unknown from_queue=true")
                        eligible_posts.append(
                            {
                                "post_url": post_url,
                                "preview_text": "",
                                "post_ts": None,
                                "from_queue": True,
                                "queue_id": queue_task_id,
                            }
                        )
                else:
                    opened_community = False
                    for wait_until_mode, nav_timeout in (("domcontentloaded", 45000), ("commit", 60000)):
                        try:
                            page.goto(community_url, wait_until=wait_until_mode, timeout=nav_timeout)
                            page.wait_for_timeout(1800)
                            opened_community = True
                            break
                        except Exception:
                            continue
                    if not opened_community:
                        cooldown_remaining = _activate_community_network_cooldown(
                            community_id,
                            community_url,
                            QUEUE_COMMUNITY_NETWORK_COOLDOWN_SECONDS,
                        )
                        self._insert_log(
                            {
                                "id": str(uuid.uuid4()),
                                "timestamp": datetime.now().strftime("%H:%M:%S"),
                                "profile": str(profile_label),
                                "status": "retry",
                                "message": (
                                    f"[SKOOL] Community open timeout: {community_name} "
                                    f"cooldown={cooldown_remaining}s"
                                ),
                            }
                        )
                        _trace(
                            f"[SKOOL][TRACE] Community open timeout: {community_url} cooldown={cooldown_remaining}s",
                            status="retry",
                        )
                        continue
                    if "login" in page.url.lower():
                        profile["status"] = "logged_out"
                        return result
                    community_page_text_lower = ""
                    try:
                        community_page_text_lower = str(page.content() or "").lower()
                    except Exception:
                        community_page_text_lower = ""
                    community_archived_read_only = (
                        ("this group has been archived" in community_page_text_lower)
                        or ("archived groups are read only" in community_page_text_lower)
                        or ("you can't like, post, comment, or chat" in community_page_text_lower)
                    )
                    if community_archived_read_only:
                        self._pause_community_for_archived_read_only(
                            community_id=str(community_id or ""),
                            community_name=str(community_name or community_id or "community"),
                            profile_label=str(profile_label or "SYSTEM"),
                        )
                        community["status"] = "paused"
                        _note_prefill_skip("archived_read_only")
                        if scan_only:
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": (
                                        "[SKOOL] Queue prefill skipped: "
                                        f"archived_read_only community={community_name}"
                                    ),
                                }
                            )
                        if not scan_only and due_by_community.get(community_id):
                            removed = 0
                            for due in list(due_by_community.get(community_id) or []):
                                due_url = str(due.get("post_url") or "").strip()
                                if not due_url:
                                    continue
                                task_ref = str(due.get("queue_id") or _extract_task_ref_from_post_url(due_url) or "n/a")
                                self._remove_queue_item(profile_id, due_url)
                                removed += 1
                                result.skipped_count += 1
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "error",
                                        "message": f"[SKOOL] Comment failed task={task_ref} reason=archived_read_only removed=1",
                                    }
                                )
                            if removed > 0:
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "info",
                                        "message": (
                                            "[SKOOL] Queue cleanup: "
                                            f"archived_read_only community={community_name} removed={removed}"
                                        ),
                                    }
                                )
                        _trace(
                            f"[SKOOL][TRACE] Community skipped: archived_read_only name={community_name}",
                            status="info",
                            fallback_level="skip",
                        )
                        continue
                    community_membership_pending = (
                        ("membership pending" in community_page_text_lower)
                        and (
                            ("admins are reviewing your request" in community_page_text_lower)
                            or ("cancel membership request" in community_page_text_lower)
                            or ("joingroupbuttonwrapper" in community_page_text_lower)
                        )
                    ) or bool(
                        page.query_selector("h2:has-text('Membership pending')")
                        or page.query_selector("button:has-text('Membership Pending')")
                        or page.query_selector("button:has-text('Cancel membership request')")
                    )
                    if community_membership_pending:
                        self._pause_community_for_membership_pending(
                            community_id=str(community_id or ""),
                            community_name=str(community_name or community_id or "community"),
                            profile_label=str(profile_label or "SYSTEM"),
                        )
                        community["status"] = "paused"
                        _note_prefill_skip("membership_pending_approval")
                        if scan_only:
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": (
                                        "[SKOOL] Queue prefill skipped: "
                                        f"membership_pending_approval community={community_name}"
                                    ),
                                }
                            )
                        if not scan_only and due_by_community.get(community_id):
                            removed = 0
                            for due in list(due_by_community.get(community_id) or []):
                                due_url = str(due.get("post_url") or "").strip()
                                if not due_url:
                                    continue
                                task_ref = str(due.get("queue_id") or _extract_task_ref_from_post_url(due_url) or "n/a")
                                self._remove_queue_item(profile_id, due_url)
                                removed += 1
                                result.skipped_count += 1
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "error",
                                        "message": f"[SKOOL] Comment failed task={task_ref} reason=membership_pending_approval removed=1",
                                    }
                                )
                            if removed > 0:
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "info",
                                        "message": (
                                            "[SKOOL] Queue cleanup: "
                                            f"membership_pending_approval community={community_name} removed={removed}"
                                        ),
                                    }
                                )
                        _trace(
                            f"[SKOOL][TRACE] Community skipped: membership_pending_approval name={community_name}",
                            status="info",
                            fallback_level="skip",
                        )
                        continue

                    posts = self._collect_feed_posts_newest_to_oldest(page)
                    if not posts:
                        _note_prefill_skip("no_posts_in_feed")
                        _trace("[SKOOL][TRACE] Community scan found no posts")
                        continue
                    if scan_only:
                        prefill_communities_with_posts += 1
                    posts = posts[:posts_scan_limit]
                    _trace(f"[SKOOL][TRACE] Community posts collected={len(posts)} limit={posts_scan_limit}")

                    for selected in posts:
                        try:
                            post_url = selected.get("post_url")
                            preview = selected.get("preview_text", "")
                            post_ts = selected.get("post_ts")
                            if not post_url:
                                continue
                            _trace(
                                f"[SKOOL][TRACE] Feed candidate post={post_url} time={_fmt_post_time(post_ts)} "
                                f"preview_words={_count_words(preview)}"
                            )
                            norm_post_url = self._normalize_url(post_url)
                            if norm_post_url and norm_post_url in commented_posts:
                                self._remove_queue_item(profile_id, post_url)
                                result.skipped_count += 1
                                _trace(f"[SKOOL][TRACE] Feed candidate skipped: already_commented post={post_url}", fallback_level="skip")
                                continue
                            if _is_queue_post_on_cooldown(post_url):
                                _trace(
                                    f"[SKOOL][TRACE] Feed candidate skipped: temporary_network_cooldown post={post_url}",
                                    fallback_level="skip",
                                )
                                continue
                            if self._is_url_blacklisted(post_url, blacklist, preview):
                                result.blacklisted_count += 1
                                _trace(f"[SKOOL][TRACE] Feed candidate skipped: global_blacklist post={post_url}", fallback_level="skip")
                                continue
                            if blacklist_terms and preview:
                                preview_lower = preview.lower()
                                if any(term in preview_lower for term in blacklist_terms):
                                    result.blacklisted_count += 1
                                    _trace(f"[SKOOL][TRACE] Feed candidate skipped: banword_in_preview post={post_url}", fallback_level="skip")
                                    continue
                            _trace(f"[SKOOL][TRACE] Feed candidate accepted: post={post_url} time={_fmt_post_time(post_ts)}")
                            eligible_posts.append({"post_url": post_url, "preview_text": preview, "post_ts": post_ts, "from_queue": False})
                        except Exception:
                            continue

                if not eligible_posts:
                    _note_prefill_skip("no_eligible_posts")
                    _trace("[SKOOL][TRACE] No eligible posts after filtering")
                    continue
                if scan_only:
                    prefill_communities_with_eligible_posts += 1

                # Always prefer freshest posts first (newest -> oldest).
                # This prevents fallback from commenting on stale posts when recent ones exist.
                eligible_posts = sorted(
                    eligible_posts,
                    key=lambda item: (
                        1 if item.get("post_ts") is not None else 0,
                        float(item.get("post_ts") or 0.0),
                    ),
                    reverse=True,
                )
                keyword_hits_in_feed = 0
                if keyword_scanning_enabled and keywords and eligible_posts:
                    lowered_keywords = [str(kw or "").strip().lower() for kw in keywords if str(kw or "").strip()]
                    for item in eligible_posts:
                        preview_text = str(item.get("preview_text") or "").lower()
                        if preview_text and any(kw in preview_text for kw in lowered_keywords):
                            keyword_hits_in_feed += 1
                general_hits_in_feed = max(0, len(eligible_posts) - keyword_hits_in_feed)
                if scan_only and keyword_hits_in_feed > 0:
                    scan_prefill_keyword_hits_total += keyword_hits_in_feed

                if eligible_posts:
                    _trace(
                        "[SKOOL][TRACE] Eligible posts sorted newest_first: "
                        f"keyword_count={keyword_hits_in_feed} general_count={general_hits_in_feed} "
                        f"total={len(eligible_posts)} first_time={_fmt_post_time(eligible_posts[0].get('post_ts'))}"
                    )

                if not scan_only:
                    comment_posted = False
                    removed_due_existing_comment = 0

                    def _drop_due_task(selected_item: Dict[str, Any], post_url_value: str, reason: str, *, status: str = "error") -> bool:
                        if not bool(selected_item.get("from_queue")):
                            return False
                        task_ref_local = str(selected_item.get("queue_id") or _extract_task_ref_from_post_url(post_url_value) or "n/a")
                        self._remove_queue_item(profile_id, post_url_value)
                        result.skipped_count += 1
                        self._insert_log(
                            {
                                "id": str(uuid.uuid4()),
                                "timestamp": datetime.now().strftime("%H:%M:%S"),
                                "profile": str(profile_label),
                                "status": status,
                                "message": f"[SKOOL] Comment failed task={task_ref_local} reason={reason} removed=1",
                            }
                        )
                        return True

                    def _requeue_due_task(
                        selected_item: Dict[str, Any],
                        post_url_value: str,
                        *,
                        delay_seconds: int,
                        reason: str,
                        status: str = "retry",
                    ) -> bool:
                        if not bool(selected_item.get("from_queue")):
                            return False
                        try:
                            scheduled_for = datetime.now() + timedelta(seconds=max(10, int(delay_seconds)))
                            self._upsert_queue_item(
                                profile_id=profile_id,
                                profile_name=profile_label,
                                community_id=community.get("id", ""),
                                community_name=community.get("name", community_url),
                                keyword="general engagement",
                                post_url=post_url_value,
                                scheduled_for=scheduled_for,
                            )
                            task_ref_local = str(selected_item.get("queue_id") or _extract_task_ref_from_post_url(post_url_value) or "n/a")
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": status,
                                    "message": (
                                        f"[SKOOL] Task requeued task={task_ref_local} reason={reason} "
                                        f"delay={max(10, int(delay_seconds))}s"
                                    ),
                                }
                            )
                            return True
                        except Exception as requeue_exc:
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "error",
                                    "message": f"[SKOOL] Requeue failed reason={reason} error={str(requeue_exc)[:180]}",
                                }
                            )
                            return False

                    def _open_post_with_retries(post_url_value: str) -> Tuple[bool, str]:
                        last_error = ""
                        nav_plan = (
                            ("domcontentloaded", 35000),
                            ("commit", 50000),
                        )
                        for nav_attempt in range(3):
                            for wait_until_mode, nav_timeout in nav_plan:
                                try:
                                    page.goto(post_url_value, wait_until=wait_until_mode, timeout=nav_timeout)
                                    page.wait_for_timeout(1800)
                                    return True, ""
                                except Exception as nav_exc:
                                    last_error = str(nav_exc or "").strip()
                            # Short backoff before next full retry cycle.
                            try:
                                page.wait_for_timeout(1200 * (nav_attempt + 1))
                            except Exception:
                                pass
                        return False, last_error

                    for selected in eligible_posts:
                        if comment_posted:
                            break
                        had_runtime_candidate = True
                        try:
                            post_url = selected["post_url"]
                            task_ref = str(selected.get("queue_id") or _extract_task_ref_from_post_url(post_url) or "n/a")
                            if bool(selected.get("from_queue")) and not bool(selected.get("_queue_claimed")):
                                # Remove active task from queue immediately when execution starts.
                                self._remove_queue_item(profile_id, post_url)
                                selected["_queue_claimed"] = True
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": f"[SKOOL] TASK START task={task_ref} post={post_url}",
                                }
                            )
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": f"[SKOOL] OPENING POST task={task_ref}",
                                }
                            )
                            opened_post, open_err = _open_post_with_retries(post_url)
                            if not opened_post:
                                raise RuntimeError(
                                    "network_or_navigation_error:open_post_failed"
                                    + (f":{open_err[:180]}" if open_err else "")
                                )
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": f"[SKOOL] POST OPENED task={task_ref}",
                                }
                            )
                            if "login" in page.url.lower():
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "error",
                                        "message": f"[SKOOL] Comment failed task={task_ref} reason=session_logged_out_on_post_open removed=1",
                                    }
                                )
                                result.skipped_count += 1
                                profile["status"] = "logged_out"
                                return result

                            page_post_ts = self._extract_page_post_timestamp(page)
                            _trace(
                                f"[SKOOL][TRACE] Opened thread post={post_url} "
                                f"time_from_thread={_fmt_post_time(page_post_ts)}"
                            )
                            page_text_lower = ""
                            try:
                                page_text_lower = str(page.content() or "").lower()
                            except Exception:
                                page_text_lower = ""
                            membership_pending = (
                                ("membership pending" in page_text_lower)
                                and (
                                    ("admins are reviewing your request" in page_text_lower)
                                    or ("cancel membership request" in page_text_lower)
                                    or ("joingroupbuttonwrapper" in page_text_lower)
                                )
                            ) or bool(
                                page.query_selector("h2:has-text('Membership pending')")
                                or page.query_selector("button:has-text('Membership Pending')")
                                or page.query_selector("button:has-text('Cancel membership request')")
                            )
                            if membership_pending:
                                self._pause_community_for_membership_pending(
                                    community_id=str(community.get("id", "") or ""),
                                    community_name=str(community.get("name", community_url) or community_url or "community"),
                                    profile_label=str(profile_label or "SYSTEM"),
                                )
                                community["status"] = "paused"
                                self._remove_queue_item(profile_id, post_url)
                                result.skipped_count += 1
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "error",
                                        "message": f"[SKOOL] Comment failed task={task_ref} reason=membership_pending_approval removed=1",
                                    }
                                )
                                _trace(f"[SKOOL][TRACE] Thread skipped: membership_pending post={post_url}", fallback_level="skip")
                                continue
                            comments_turned_off = (
                                bool(page.query_selector('div[class*="DisabledCommentsText"]'))
                                or ("comments are turned off for this post" in page_text_lower)
                                or ("comments are turned off" in page_text_lower)
                            )
                            if comments_turned_off:
                                self._remove_queue_item(profile_id, post_url)
                                result.skipped_count += 1
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "error",
                                        "message": f"[SKOOL] Comment failed task={task_ref} reason=comments_turned_off_for_post removed=1",
                                    }
                                )
                                _trace(f"[SKOOL][TRACE] Thread skipped: comments_turned_off post={post_url}", fallback_level="skip")
                                continue

                            if self._thread_has_profile_comment(page, profile):
                                self._remove_queue_item(profile_id, post_url)
                                norm_post_url = self._normalize_url(post_url)
                                if norm_post_url:
                                    commented_posts.add(norm_post_url)
                                result.skipped_count += 1
                                if bool(selected.get("from_queue")):
                                    self._insert_log(
                                        {
                                            "id": str(uuid.uuid4()),
                                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                                            "profile": str(profile_label),
                                            "status": "error",
                                            "message": f"[SKOOL] Comment failed task={task_ref} reason=already_commented_on_thread removed=1",
                                        }
                                    )
                                if bool(selected.get("from_queue")):
                                    removed_due_existing_comment += 1
                                _trace(f"[SKOOL][TRACE] Thread skipped: profile_already_commented post={post_url}", fallback_level="skip")
                                continue

                            thread_targets = self._extract_thread_targets(page, selected.get("preview_text", ""))
                            if not thread_targets:
                                if _drop_due_task(selected, post_url, "no_comment_targets"):
                                    continue
                                _trace(f"[SKOOL][TRACE] Thread skipped: no_targets post={post_url}", fallback_level="skip")
                                continue

                            eligible_targets: List[Dict[str, Any]] = []
                            first_non_empty_target: Optional[Dict[str, Any]] = None
                            for target in thread_targets:
                                target_text = str(target.get("text") or "").strip()
                                if not target_text:
                                    _trace(f"[SKOOL][TRACE] Target skipped: empty_text post={post_url}", fallback_level="skip")
                                    continue
                                if first_non_empty_target is None:
                                    first_non_empty_target = {"text": target_text, "is_reply": bool(target.get("is_reply"))}
                                if self._is_url_blacklisted(post_url, blacklist, target_text):
                                    result.blacklisted_count += 1
                                    _trace(f"[SKOOL][TRACE] Target skipped: global_blacklist post={post_url}", fallback_level="skip")
                                    continue
                                if blacklist_terms and any(term in target_text.lower() for term in blacklist_terms):
                                    result.blacklisted_count += 1
                                    _trace(f"[SKOOL][TRACE] Target skipped: banword post={post_url}", fallback_level="skip")
                                    continue
                                eligible_targets.append({"text": target_text, "is_reply": bool(target.get("is_reply"))})

                            if not eligible_targets and bool(selected.get("from_queue")) and first_non_empty_target:
                                # Queue tasks should still attempt posting when only blacklist filtering removed targets.
                                eligible_targets.append(first_non_empty_target)
                                _trace(
                                    f"[SKOOL][TRACE] Queue fallback target selected post={post_url} reason=all_targets_filtered",
                                    fallback_level="general_comment_fallback",
                                )

                            if not eligible_targets:
                                if _drop_due_task(selected, post_url, "no_eligible_targets"):
                                    continue
                                _trace(f"[SKOOL][TRACE] Thread skipped: no_eligible_targets post={post_url}", fallback_level="skip")
                                continue

                            matched_kw: Optional[str] = None
                            target_to_reply = eligible_targets[0]
                            if keyword_scanning_enabled and keywords:
                                for target in eligible_targets:
                                    target_lower = target["text"].lower()
                                    for kw in keywords:
                                        if kw.lower() in target_lower:
                                            matched_kw = kw
                                            target_to_reply = target
                                            break
                                    if matched_kw:
                                        break

                            fallback_level_used = "skip"
                            if matched_kw and keyword_prompt:
                                prompt_to_use = keyword_prompt
                                fallback_level_used = "keyword_rule"
                            elif comment_fallback_enabled and general_prompt:
                                prompt_to_use = general_prompt
                                fallback_level_used = "general_comment_fallback"
                                _trace(
                                    f"[SKOOL][TRACE] No keyword match for eligible targets, switching to general fallback prompt "
                                    f"post={post_url}",
                                    fallback_level=fallback_level_used,
                                )
                            else:
                                if _drop_due_task(selected, post_url, "no_keyword_match_and_fallback_disabled"):
                                    continue
                                _trace(
                                    f"[SKOOL][TRACE] Thread skipped: no_keyword_or_fallback_disabled_or_empty_prompt "
                                    f"post={post_url} fallback_enabled={comment_fallback_enabled}",
                                    fallback_level="skip",
                                )
                                continue
                            is_kw = fallback_level_used == "keyword_rule"
                            reply_source_text = target_to_reply["text"]
                            _trace(
                                f"[SKOOL][TRACE] Prompt selected post={post_url} "
                                f"target_is_reply={bool(target_to_reply.get('is_reply'))} "
                                f"matched_keyword={matched_kw or 'none'} fallback_level_used={fallback_level_used} "
                                f"fallback_enabled_setting={comment_fallback_enabled}",
                                fallback_level=fallback_level_used,
                            )

                            if target_to_reply.get("is_reply"):
                                self._focus_reply_target_editor(page, reply_source_text)

                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": (
                                        f"[SKOOL] AI GENERATE task={task_ref} "
                                        f"mode={fallback_level_used} keyword={matched_kw or 'none'}"
                                    ),
                                }
                            )
                            ai_reply = _openai_generate_comment_rest(api_key, prompt_to_use, reply_source_text)
                            if not ai_reply:
                                if bool(selected.get("from_queue")):
                                    if not bool(selected.get("_retried_empty_ai")):
                                        selected["_retried_empty_ai"] = True
                                        eligible_posts.append(selected)
                                        self._insert_log(
                                            {
                                                "id": str(uuid.uuid4()),
                                                "timestamp": datetime.now().strftime("%H:%M:%S"),
                                                "profile": str(profile_label),
                                                "status": "retry",
                                                "message": f"[SKOOL] Comment retry task={task_ref} reason=empty_ai_reply",
                                            }
                                        )
                                        continue
                                    if _drop_due_task(selected, post_url, "empty_ai_reply"):
                                        continue
                                _trace(f"[SKOOL][TRACE] AI generation returned empty text post={post_url}", status="retry", fallback_level=fallback_level_used)
                                continue

                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": f"[SKOOL] WRITING COMMENT task={task_ref} chars={len(ai_reply)}",
                                }
                            )
                            editor = self._ensure_comment_editor(page, timeout_ms=15000)
                            if not editor:
                                raise RuntimeError("send_or_dom_error:editor_not_visible")
                            editor.click()
                            page.keyboard.press("Control+a")
                            page.keyboard.press("Delete")
                            editor.type(ai_reply, delay=40)

                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": f"[SKOOL] SEND CLICK task={task_ref}",
                                }
                            )
                            sent, send_reason = self._submit_comment_with_fallback(page, ai_reply)
                            if not sent:
                                raise RuntimeError(f"send_or_dom_error:{send_reason}")
                            if not self._verify_comment_published(page, profile, ai_reply):
                                raise RuntimeError("send_or_dom_error:post_submit_not_verified")

                            self._add_to_blacklist(post_url, blacklist, reply_source_text)
                            self._save_blacklist(blacklist)
                            _clear_queue_network_failure(post_url)

                            replies_this_visit += 1
                            community_actions_today += 1
                            community["actionsToday"] = community_actions_today
                            profile["repliesCompleted"] = profile.get("repliesCompleted", 0) + 1
                            result.comments_posted += 1
                            self._increment_community_action_counters(
                                community_id=community.get("id", ""),
                                is_keyword_match=bool(is_kw),
                            )
                            result.activity_rows.append({
                                "id": str(uuid.uuid4()),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "profileLabel": profile_label,
                                "profileId": profile_id,
                                "community": community_url,
                                "keywordMatched": matched_kw or "",
                                "matchSource": "keyword" if is_kw else "general",
                                "postSnippet": reply_source_text[:200],
                                "promptUsed": prompt_to_use,
                                "aiReply": ai_reply,
                                "postUrl": post_url,
                                "result": "Commented",
                                "skipReason": "",
                            })
                            self._remove_queue_item(profile_id, post_url)
                            norm_post_url = self._normalize_url(post_url)
                            if norm_post_url:
                                commented_posts.add(norm_post_url)
                            comment_posted = True
                            _trace(
                                f"[SKOOL][TRACE] Comment posted post={post_url} "
                                f"fallback_level_used={fallback_level_used} matched_keyword={matched_kw or 'none'}",
                                status="success",
                                fallback_level=fallback_level_used,
                            )
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "success",
                                    "message": f"[SKOOL] Comment posted task={task_ref}",
                                }
                            )
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "success",
                                    "message": f"[SKOOL] TASK SUCCESS task={task_ref}",
                                }
                            )

                            try:
                                page.go_back(wait_until="domcontentloaded")
                                page.wait_for_timeout(2000)
                            except Exception:
                                pass
                            if community_daily_limit and community_actions_today >= community_daily_limit:
                                break
                        except Exception as exc:
                            err_text = str(exc or "").strip().lower()
                            send_error_detail = ""
                            if err_text.startswith("send_or_dom_error:"):
                                send_error_detail = err_text.split(":", 1)[1].strip()
                            network_detail = "network"
                            if "proxy" in err_text or "tunnel" in err_text:
                                network_detail = "proxy"
                            elif "dns" in err_text:
                                network_detail = "dns"
                            elif "timeout" in err_text or "timed out" in err_text:
                                network_detail = "timeout"
                            elif "navigation" in err_text or "net::" in err_text:
                                network_detail = "navigation"
                            elif "connection reset" in err_text or "connection closed" in err_text:
                                network_detail = "connection"
                            is_network_error = any(
                                token in err_text
                                for token in (
                                    "timeout",
                                    "net::",
                                    "network",
                                    "dns",
                                    "proxy",
                                    "connection reset",
                                    "connection closed",
                                    "tunnel",
                                )
                            )
                            try:
                                fail_page_text = str(page.content() or "").lower()
                            except Exception:
                                fail_page_text = ""
                            if AUTOMATION_FAILURE_DIAGNOSTICS_ENABLED:
                                try:
                                    current_url = str(getattr(page, "url", "") or "").strip()
                                except Exception:
                                    current_url = ""
                                try:
                                    page_title = str(page.title() or "").strip()
                                except Exception:
                                    page_title = ""
                                raw_error = str(exc or "").strip()
                                lowered_page = fail_page_text or ""
                                on_login_page = (
                                    "/login" in current_url.lower()
                                    or "input#email" in lowered_page
                                    or "input#password" in lowered_page
                                )
                                captcha_suspected = (
                                    "captcha" in lowered_page
                                    or "verify you are human" in lowered_page
                                    or "cloudflare" in lowered_page
                                )
                                context_closed = any(
                                    token in err_text
                                    for token in (
                                        "target page, context or browser has been closed",
                                        "browser has been closed",
                                        "context has been closed",
                                    )
                                )
                                stage = "submit_or_dom"
                                if "open_post_failed" in err_text:
                                    stage = "open_post"
                                elif send_error_detail:
                                    stage = "submit_send_or_verify"
                                elif "timeout" in err_text or "navigation" in err_text or "net::" in err_text:
                                    stage = "navigation_or_network"
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "retry",
                                        "message": (
                                            f"[SKOOL] FAILURE DETAIL task={task_ref} stage={stage} "
                                            f"net={network_detail} from_queue={1 if bool(selected.get('from_queue')) else 0} "
                                            f"login_page={1 if on_login_page else 0} captcha={1 if captcha_suspected else 0} "
                                            f"context_closed={1 if context_closed else 0} "
                                            f"url={current_url[:180] or '-'} "
                                            f"title={page_title[:80] or '-'} "
                                            f"send_detail={send_error_detail[:80] or '-'} "
                                            f"error_raw={raw_error[:220] or '-'}"
                                        ),
                                    }
                                )
                            if (
                                ("this group has been archived" in fail_page_text)
                                or ("archived groups are read only" in fail_page_text)
                                or ("you can't like, post, comment, or chat" in fail_page_text)
                            ):
                                self._pause_community_for_archived_read_only(
                                    community_id=str(community.get("id", "") or ""),
                                    community_name=str(community.get("name", community_url) or community_url or "community"),
                                    profile_label=str(profile_label or "SYSTEM"),
                                )
                                community["status"] = "paused"
                                self._remove_queue_item(profile_id, post_url)
                                result.skipped_count += 1
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "error",
                                        "message": f"[SKOOL] Comment failed task={task_ref} reason=archived_read_only removed=1",
                                    }
                                )
                            elif (
                                "comments are turned off for this post" in fail_page_text
                                or "comments are turned off" in fail_page_text
                            ):
                                self._remove_queue_item(profile_id, post_url)
                                result.skipped_count += 1
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "error",
                                        "message": f"[SKOOL] Comment failed task={task_ref} reason=comments_turned_off_for_post removed=1",
                                    }
                                )
                            elif (
                                ("membership pending" in fail_page_text)
                                and (
                                    ("admins are reviewing your request" in fail_page_text)
                                    or ("cancel membership request" in fail_page_text)
                                    or ("joingroupbuttonwrapper" in fail_page_text)
                                )
                            ):
                                self._pause_community_for_membership_pending(
                                    community_id=str(community.get("id", "") or ""),
                                    community_name=str(community.get("name", community_url) or community_url or "community"),
                                    profile_label=str(profile_label or "SYSTEM"),
                                )
                                community["status"] = "paused"
                                self._remove_queue_item(profile_id, post_url)
                                result.skipped_count += 1
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "error",
                                        "message": f"[SKOOL] Comment failed task={task_ref} reason=membership_pending_approval removed=1",
                                    }
                                )
                            else:
                                if bool(selected.get("from_queue")) and send_error_detail == "editor_not_visible":
                                    # This post/thread often has delayed or unavailable editor.
                                    # Cool it down longer instead of burning immediate retries/cycles.
                                    requeued_editor = _requeue_due_task(
                                        selected,
                                        post_url,
                                        delay_seconds=QUEUE_EDITOR_NOT_VISIBLE_COOLDOWN_SECONDS,
                                        reason=(
                                            "send_or_dom_error:editor_not_visible_cooldown"
                                            f"_attempt_{int(self._queue_submit_fail_streak.get(_queue_post_key(post_url), 0) or 0) + 1}"
                                        ),
                                        status="retry",
                                    )
                                    if requeued_editor:
                                        self._insert_log(
                                            {
                                                "id": str(uuid.uuid4()),
                                                "timestamp": datetime.now().strftime("%H:%M:%S"),
                                                "profile": str(profile_label),
                                                "status": "retry",
                                                "message": (
                                                    f"[SKOOL] Task postponed task={task_ref} "
                                                    "reason=editor_not_visible_cooldown "
                                                    f"cooldown={QUEUE_EDITOR_NOT_VISIBLE_COOLDOWN_SECONDS}s"
                                                ),
                                            }
                                        )
                                        try:
                                            page.go_back(wait_until="domcontentloaded")
                                            page.wait_for_timeout(2000)
                                        except Exception:
                                            pass
                                        continue
                                    self._remove_queue_item(profile_id, post_url)
                                    result.skipped_count += 1
                                    try:
                                        page.go_back(wait_until="domcontentloaded")
                                        page.wait_for_timeout(2000)
                                    except Exception:
                                        pass
                                    continue
                                if is_network_error and bool(selected.get("from_queue")):
                                    result.network_error_count += 1
                                    # One immediate in-pass retry before deferred requeue.
                                    if not bool(selected.get("_network_local_retry")):
                                        selected["_network_local_retry"] = True
                                        eligible_posts.append(selected)
                                        self._insert_log(
                                            {
                                                "id": str(uuid.uuid4()),
                                                "timestamp": datetime.now().strftime("%H:%M:%S"),
                                                "profile": str(profile_label),
                                                "status": "retry",
                                                "message": f"[SKOOL] Comment retry task={task_ref} reason=network_local_retry",
                                            }
                                        )
                                        try:
                                            page.go_back(wait_until="domcontentloaded")
                                            page.wait_for_timeout(1800)
                                        except Exception:
                                            pass
                                        continue
                                    # Network/browser flakiness should not permanently drop queue items.
                                    can_retry, delay_seconds, attempt = _register_queue_network_failure(post_url)
                                    if can_retry:
                                        if _requeue_due_task(
                                            selected,
                                            post_url,
                                            delay_seconds=delay_seconds,
                                            reason=(
                                                "network_or_navigation_error"
                                                f"_{network_detail}"
                                                f"_attempt_{attempt}_of_{QUEUE_NETWORK_MAX_RETRIES_PER_POST}"
                                            ),
                                            status="retry",
                                        ):
                                            selected["_network_retried"] = True
                                            try:
                                                page.go_back(wait_until="domcontentloaded")
                                                page.wait_for_timeout(2000)
                                            except Exception:
                                                pass
                                            continue
                                    community_cooldown_remaining = _community_cooldown_remaining(
                                        community_id,
                                        community_url,
                                    )
                                    should_drop_task, exhaust_cycle = _register_retry_budget_exhausted(post_url)
                                    if should_drop_task:
                                        delayed = _requeue_due_task(
                                            selected,
                                            post_url,
                                            delay_seconds=QUEUE_NETWORK_PERSISTENT_FAIL_COOLDOWN_SECONDS,
                                            reason=(
                                                "persistent_network_failure_cooldown"
                                                f"_cycle_{exhaust_cycle}_of_{QUEUE_NETWORK_MAX_BUDGET_EXHAUST_CYCLES}"
                                            ),
                                            status="retry",
                                        )
                                        if delayed:
                                            _clear_queue_network_failure(post_url)
                                            self._insert_log(
                                                {
                                                    "id": str(uuid.uuid4()),
                                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                                    "profile": str(profile_label),
                                                    "status": "retry",
                                                    "message": (
                                                        f"[SKOOL] Task postponed task={task_ref} "
                                                        "reason=persistent_network_failure_cooldown "
                                                        f"budget_exhaust_cycles={exhaust_cycle}/{QUEUE_NETWORK_MAX_BUDGET_EXHAUST_CYCLES} "
                                                        f"cooldown={QUEUE_NETWORK_PERSISTENT_FAIL_COOLDOWN_SECONDS}s"
                                                    ),
                                                }
                                            )
                                            try:
                                                page.go_back(wait_until="domcontentloaded")
                                                page.wait_for_timeout(2000)
                                            except Exception:
                                                pass
                                            continue
                                        self._remove_queue_item(profile_id, post_url)
                                        result.skipped_count += 1
                                        try:
                                            page.go_back(wait_until="domcontentloaded")
                                            page.wait_for_timeout(2000)
                                        except Exception:
                                            pass
                                        continue
                                    postponed = _requeue_due_task(
                                        selected,
                                        post_url,
                                        delay_seconds=QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS,
                                        reason=(
                                            "network_retry_budget_exhausted"
                                            f"_attempt_{attempt}_of_{QUEUE_NETWORK_MAX_RETRIES_PER_POST}"
                                        ),
                                        status="retry",
                                    )
                                    if postponed:
                                        _clear_queue_network_failure(post_url)
                                        self._insert_log(
                                            {
                                                "id": str(uuid.uuid4()),
                                                "timestamp": datetime.now().strftime("%H:%M:%S"),
                                                "profile": str(profile_label),
                                                "status": "retry",
                                                "message": (
                                                    f"[SKOOL] Task postponed task={task_ref} "
                                                    "reason=network_retry_budget_exhausted "
                                                    f"attempts={attempt}/{QUEUE_NETWORK_MAX_RETRIES_PER_POST} "
                                                    f"cooldown={QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS}s "
                                                    f"community_cooldown={community_cooldown_remaining}s"
                                                ),
                                            }
                                        )
                                        try:
                                            page.go_back(wait_until="domcontentloaded")
                                            page.wait_for_timeout(2000)
                                        except Exception:
                                            pass
                                        continue
                                    self._remove_queue_item(profile_id, post_url)
                                    result.skipped_count += 1
                                    self._insert_log(
                                        {
                                            "id": str(uuid.uuid4()),
                                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                                            "profile": str(profile_label),
                                            "status": "error",
                                            "message": (
                                                f"[SKOOL] Comment failed task={task_ref} "
                                                "reason=network_retry_exhausted "
                                                f"attempts={attempt}/{QUEUE_NETWORK_MAX_RETRIES_PER_POST} "
                                                f"cooldown={QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS}s "
                                                f"community_cooldown={community_cooldown_remaining}s removed=1"
                                            ),
                                        }
                                    )
                                    try:
                                        page.go_back(wait_until="domcontentloaded")
                                        page.wait_for_timeout(2000)
                                    except Exception:
                                        pass
                                    continue

                                if not bool(selected.get("_retried")):
                                    selected["_retried"] = True
                                    eligible_posts.append(selected)
                                    self._insert_log(
                                        {
                                            "id": str(uuid.uuid4()),
                                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                                            "profile": str(profile_label),
                                            "status": "retry",
                                            "message": (
                                                f"[SKOOL] Comment retry task={task_ref} reason=transient_send_or_dom_error"
                                                + (f" detail={send_error_detail}" if send_error_detail else "")
                                            ),
                                        }
                                    )
                                    try:
                                        page.go_back(wait_until="domcontentloaded")
                                        page.wait_for_timeout(2000)
                                    except Exception:
                                        pass
                                    continue

                                requeued = False
                                if bool(selected.get("from_queue")):
                                    can_retry_submit, submit_delay, submit_attempt = _register_queue_submit_failure(post_url)
                                    if can_retry_submit:
                                        requeued = _requeue_due_task(
                                            selected,
                                            post_url,
                                            delay_seconds=submit_delay,
                                            reason=(
                                                "send_or_dom_error"
                                                + (f":{send_error_detail}" if send_error_detail else "")
                                                + f"_attempt_{submit_attempt}_of_{QUEUE_NETWORK_MAX_RETRIES_PER_POST}"
                                            ),
                                            status="retry",
                                        )
                                if not requeued:
                                    community_cooldown_remaining = _community_cooldown_remaining(
                                        community_id,
                                        community_url,
                                    )
                                    should_drop_task, exhaust_cycle = _register_retry_budget_exhausted(post_url)
                                    if should_drop_task:
                                        delayed = _requeue_due_task(
                                            selected,
                                            post_url,
                                            delay_seconds=QUEUE_NETWORK_PERSISTENT_FAIL_COOLDOWN_SECONDS,
                                            reason=(
                                                "persistent_network_failure_cooldown"
                                                + (f":{send_error_detail}" if send_error_detail else "")
                                                + f"_cycle_{exhaust_cycle}_of_{QUEUE_NETWORK_MAX_BUDGET_EXHAUST_CYCLES}"
                                            ),
                                            status="retry",
                                        )
                                        if delayed:
                                            _clear_queue_network_failure(post_url)
                                            self._insert_log(
                                                {
                                                    "id": str(uuid.uuid4()),
                                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                                    "profile": str(profile_label),
                                                    "status": "retry",
                                                    "message": (
                                                        f"[SKOOL] Task postponed task={task_ref} "
                                                        "reason=persistent_network_failure_cooldown "
                                                        + (f"detail={send_error_detail} " if send_error_detail else "")
                                                        + f"budget_exhaust_cycles={exhaust_cycle}/{QUEUE_NETWORK_MAX_BUDGET_EXHAUST_CYCLES} "
                                                        + f"cooldown={QUEUE_NETWORK_PERSISTENT_FAIL_COOLDOWN_SECONDS}s"
                                                    ),
                                                }
                                            )
                                            try:
                                                page.go_back(wait_until="domcontentloaded")
                                                page.wait_for_timeout(2000)
                                            except Exception:
                                                pass
                                            continue
                                        self._remove_queue_item(profile_id, post_url)
                                        result.skipped_count += 1
                                        try:
                                            page.go_back(wait_until="domcontentloaded")
                                            page.wait_for_timeout(2000)
                                        except Exception:
                                            pass
                                        continue
                                    submit_attempt_capped = int(self._queue_submit_fail_streak.get(_queue_post_key(post_url), 0) or 0)
                                    postponed = _requeue_due_task(
                                        selected,
                                        post_url,
                                        delay_seconds=QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS,
                                        reason=(
                                            "send_or_dom_retry_budget_exhausted"
                                            + (f":{send_error_detail}" if send_error_detail else "")
                                            + f"_attempt_{submit_attempt_capped}_of_{QUEUE_NETWORK_MAX_RETRIES_PER_POST}"
                                        ),
                                        status="retry",
                                    )
                                    if postponed:
                                        _clear_queue_network_failure(post_url)
                                        self._insert_log(
                                            {
                                                "id": str(uuid.uuid4()),
                                                "timestamp": datetime.now().strftime("%H:%M:%S"),
                                                "profile": str(profile_label),
                                                "status": "retry",
                                                "message": (
                                                    f"[SKOOL] Task postponed task={task_ref} reason=submit_retry_budget_exhausted "
                                                    + (f"detail={send_error_detail} " if send_error_detail else "")
                                                    + f"attempts={submit_attempt_capped}/{QUEUE_NETWORK_MAX_RETRIES_PER_POST} "
                                                    + f"cooldown={QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS}s "
                                                    + f"community_cooldown={community_cooldown_remaining}s"
                                                ),
                                            }
                                        )
                                        try:
                                            page.go_back(wait_until="domcontentloaded")
                                            page.wait_for_timeout(2000)
                                        except Exception:
                                            pass
                                        continue
                                    self._remove_queue_item(profile_id, post_url)
                                    result.skipped_count += 1
                                    self._insert_log(
                                        {
                                            "id": str(uuid.uuid4()),
                                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                                            "profile": str(profile_label),
                                            "status": "error",
                                            "message": (
                                                f"[SKOOL] Comment failed task={task_ref} reason=submit_retry_exhausted "
                                                + (f"detail={send_error_detail} " if send_error_detail else "")
                                                + f"attempts={int(self._queue_submit_fail_streak.get(_queue_post_key(post_url), 0) or 0)}/{QUEUE_NETWORK_MAX_RETRIES_PER_POST} "
                                                + f"cooldown={QUEUE_NETWORK_FAIL_COOLDOWN_SECONDS}s "
                                                + f"community_cooldown={community_cooldown_remaining}s removed=1"
                                            ),
                                        }
                                    )
                                try:
                                    page.go_back(wait_until="domcontentloaded")
                                    page.wait_for_timeout(2000)
                                except Exception:
                                    pass
                                continue

                    if removed_due_existing_comment > 0:
                        try:
                            page.goto(community_url, wait_until="domcontentloaded")
                            page.wait_for_timeout(1800)
                            replacement_posts = self._collect_feed_posts_newest_to_oldest(page)
                            if replacement_posts:
                                scan_interval_minutes = max(1, int(settings.get("scanIntervalMinutes", 5)))
                                step_seconds = scan_interval_minutes * 60
                                tail_dt = self._queue_tail_datetime_for_profile(str(profile_id or ""))
                                inserted = 0
                                for selected in replacement_posts:
                                    if inserted >= removed_due_existing_comment:
                                        break
                                    replacement_url = str(selected.get("post_url") or "").strip()
                                    if not replacement_url:
                                        continue
                                    replacement_norm = self._normalize_url(replacement_url)
                                    if replacement_norm and replacement_norm in commented_posts:
                                        continue
                                    if self._is_post_queued_for_profile(str(profile_id or ""), replacement_url):
                                        continue
                                    preview = str(selected.get("preview_text") or "")
                                    if self._is_url_blacklisted(replacement_url, blacklist, preview):
                                        continue
                                    if blacklist_terms and preview:
                                        preview_lower = preview.lower()
                                        if any(term in preview_lower for term in blacklist_terms):
                                            continue
                                    tail_dt = max(datetime.now(), tail_dt) + timedelta(seconds=step_seconds)
                                    self._upsert_queue_item(
                                        profile_id=profile_id,
                                        profile_name=profile_label,
                                        community_id=community.get("id", ""),
                                        community_name=community.get("name", community_url),
                                        keyword="general engagement",
                                        post_url=replacement_url,
                                        scheduled_for=tail_dt,
                                    )
                                    inserted += 1
                                    _trace(
                                        f"[SKOOL][TRACE] Queue refill added post={replacement_url} "
                                        f"time={_fmt_post_time(selected.get('post_ts'))}"
                                    )
                                self._insert_log(
                                    {
                                        "id": str(uuid.uuid4()),
                                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                                        "profile": str(profile_label),
                                        "status": "success" if inserted > 0 else "info",
                                        "message": f"[SKOOL] Queue refill after skipped queued posts: requested={removed_due_existing_comment}, added={inserted}",
                                    }
                                )
                        except Exception as refill_exc:
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "retry",
                                    "message": f"[SKOOL] Queue refill skipped after already-commented post: {refill_exc}",
                                }
                            )

                if eligible_posts and scan_only:
                    scan_interval_minutes = max(1, int(settings.get("scanIntervalMinutes", 5)))
                    step_seconds = scan_interval_minutes * 60
                    stagger = max(0, int(queue_stagger_seconds or 0))
                    base_dt = datetime.now() + timedelta(seconds=step_seconds + stagger)
                    daily_cap = max(1, int(profile.get("visits", settings.get("globalDailyCapPerAccount", 5))))
                    done_today = max(
                        0,
                        int(
                            max(
                                int(profile.get("visitsCompleted", 0) or 0),
                                int(profile.get("repliesCompleted", 0) or 0),
                            )
                        ),
                    )
                    queued_now = self._count_pending_queue_for_profile(str(profile_id or ""))
                    remaining_quota = max(0, daily_cap - done_today - queued_now)
                    remaining_pass_slots = max(0, per_pass_cap - scan_prefill_added_total)
                    community_remaining_today = (
                        max(0, community_daily_limit - community_used_today)
                        if community_daily_limit > 0
                        else remaining_pass_slots
                    )
                    target_quota = min(remaining_quota, remaining_pass_slots, community_remaining_today)
                    if target_quota <= 0:
                        community_quota_exhausted = bool(
                            community_daily_limit > 0 and community_remaining_today <= 0
                        )
                        _note_prefill_skip(
                            "community_daily_limit_reached" if community_quota_exhausted else "no_scan_quota"
                        )
                        if community_quota_exhausted:
                            # This community is exhausted for today; keep scanning other communities.
                            continue
                        if not prefill_stop_logged:
                            reason = "per_pass_limit_reached" if remaining_pass_slots <= 0 else "daily_quota_reached"
                            self._insert_log(
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                                    "profile": str(profile_label),
                                    "status": "info",
                                    "message": (
                                        "[SKOOL] Queue prefill stopped early: "
                                        f"reason={reason} queued={queued_now} "
                                        f"pass_added={scan_prefill_added_total}/{per_pass_cap}"
                                    ),
                                }
                            )
                            prefill_stop_logged = True
                        break
                    # Keep queue prefill diverse: at most one new task per community per pass.
                    per_community_quota = min(1, target_quota)
                    planned_count = 0
                    for idx, selected in enumerate(eligible_posts):
                        if planned_count >= per_community_quota:
                            break
                        post_url = selected.get("post_url")
                        if not post_url:
                            continue
                        planned_at = base_dt + timedelta(seconds=step_seconds * (scan_prefill_added_total + planned_count))
                        self._upsert_queue_item(
                            profile_id=profile_id,
                            profile_name=profile_label,
                            community_id=community.get("id", ""),
                            community_name=community.get("name", community_url),
                            keyword="general engagement",
                            post_url=post_url,
                            scheduled_for=planned_at,
                        )
                        planned_count += 1
                        scan_prefill_added_total += 1

            profile["_current_community_index"] = comm_index
            if scan_only:
                queued_after = self._count_pending_queue_for_profile(str(profile_id or ""))
                queued_total_after = self._count_all_queue_actions()
                skip_reasons_text = ", ".join(
                    f"{key}={count}" for key, count in sorted(prefill_skip_reasons.items(), key=lambda x: x[0])
                ) or "none"
                summary_only_daily_limit = (
                    scan_prefill_added_total <= 0
                    and prefill_skip_reasons
                    and set(prefill_skip_reasons.keys()) <= {"daily_limit_reached"}
                )
                should_emit_summary = True
                suppressed_summary_count = 0
                if summary_only_daily_limit:
                    summary_key = f"prefill_summary_daily::{str(profile_id or '').strip()}"
                    should_emit_summary, suppressed_summary_count = _prefill_skip_log_gate(summary_key)
                if should_emit_summary:
                    suppressed_text = (
                        f" repeats_suppressed={suppressed_summary_count}" if suppressed_summary_count > 0 else ""
                    )
                    diagnostics = (
                        f"checked={prefill_communities_checked} "
                        f"with_posts={prefill_communities_with_posts} "
                        f"with_eligible_posts={prefill_communities_with_eligible_posts}"
                    )
                    self._insert_log(
                        {
                            "id": str(uuid.uuid4()),
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "profile": str(profile_label),
                            "status": "success" if scan_prefill_added_total > 0 else "info",
                            "message": (
                                "[SKOOL] Queue prefill summary: "
                                f"added={scan_prefill_added_total}/{per_pass_cap} "
                                f"queued_now={queued_after} queued_total={queued_total_after} "
                                f"keyword_hits={scan_prefill_keyword_hits_total} "
                                f"skip_reasons={skip_reasons_text} "
                                f"diagnostics={diagnostics}{suppressed_text}"
                            ),
                        }
                    )
                elif trace_selection_logs and (suppressed_summary_count % PREFILL_SKIP_DEBUG_EVERY == 0):
                    _trace(
                        "[SKOOL][TRACE] Queue prefill summary suppressed: "
                        f"skip_reasons={skip_reasons_text} count={suppressed_summary_count}",
                        status="info",
                    )
            if replies_this_visit == 0 and not scan_only and had_runtime_candidate:
                result.skipped_count += 1
        finally:
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if playwright:
                    playwright.stop()
            except Exception:
                pass
            release_proxy_slot(proxy_slot_handle)
        return result

    def _has_pending_queue_actions(self) -> bool:
        try:
            with self._db() as db:
                row = db.execute(
                    "SELECT COUNT(*) AS cnt FROM queue_items WHERE scheduledFor <= ?",
                    (datetime.now().isoformat(timespec="seconds"),),
                ).fetchone()
                return bool(int(row["cnt"] if row and "cnt" in row.keys() else 0) > 0)
        except Exception:
            return False

    def _count_due_queue_actions(self) -> int:
        try:
            with self._db() as db:
                row = db.execute(
                    "SELECT COUNT(*) AS cnt FROM queue_items WHERE scheduledFor <= ?",
                    (datetime.now().isoformat(timespec="seconds"),),
                ).fetchone()
                return int(row["cnt"] if row and "cnt" in row.keys() else 0)
        except Exception:
            return 0

    def _has_any_queue_actions(self) -> bool:
        try:
            with self._db() as db:
                row = db.execute("SELECT COUNT(*) AS cnt FROM queue_items").fetchone()
                return bool(int(row["cnt"] if row and "cnt" in row.keys() else 0) > 0)
        except Exception:
            return False

    def _count_all_queue_actions(self) -> int:
        try:
            with self._db() as db:
                row = db.execute("SELECT COUNT(*) AS cnt FROM queue_items").fetchone()
                return int(row["cnt"] if row and "cnt" in row.keys() else 0)
        except Exception:
            return 1

    def _count_pending_queue_for_profile(self, profile_id: str) -> int:
        pid = str(profile_id or "").strip()
        if not pid:
            return 0
        try:
            with self._db() as db:
                row = db.execute(
                    "SELECT COUNT(*) AS cnt FROM queue_items WHERE profileId = ?",
                    (pid,),
                ).fetchone()
                return int(row["cnt"] if row and "cnt" in row.keys() else 0)
        except Exception:
            return 0

    def _count_pending_queue_for_profile_community_today(self, profile_id: str, community_id: str) -> int:
        pid = str(profile_id or "").strip()
        cid = str(community_id or "").strip()
        if not pid or not cid:
            return 0
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            with self._db() as db:
                row = db.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM queue_items
                    WHERE profileId = ?
                      AND communityId = ?
                      AND substr(COALESCE(scheduledFor, ''), 1, 10) = ?
                    """,
                    (pid, cid, today),
                ).fetchone()
                return int(row["cnt"] if row and "cnt" in row.keys() else 0)
        except Exception:
            return 0

    def _load_due_queue_items_for_profile(
        self,
        profile_id: Optional[str],
        limit: int = 20,
        lead_seconds: int = QUEUE_EXECUTION_LEAD_SECONDS,
    ) -> List[Dict[str, str]]:
        pid = str(profile_id or "").strip()
        if not pid:
            return []
        out: List[Dict[str, str]] = []
        due_dt = datetime.now() + timedelta(seconds=max(0, int(lead_seconds)))
        now_iso = due_dt.isoformat(timespec="seconds")
        try:
            with self._db() as db:
                rows = db.execute(
                    """
                    SELECT id, postId, communityId, community, keyword
                    FROM queue_items
                    WHERE profileId = ? AND scheduledFor <= ?
                    ORDER BY scheduledFor ASC
                    LIMIT ?
                    """,
                    (pid, now_iso, max(1, int(limit))),
                ).fetchall()
            for row in rows:
                post_url = str(row["postId"] or "").strip()
                if not post_url:
                    continue
                out.append(
                    {
                        "post_url": post_url,
                        "community_id": str(row["communityId"] or "").strip(),
                        "community_name": str(row["community"] or "").strip(),
                        "keyword": str(row["keyword"] or "").strip(),
                        "queue_id": str(row["id"] or "").strip(),
                    }
                )
        except Exception:
            return []
        return out

    def _load_profile_commented_posts(self, profile_id: Optional[str]) -> Set[str]:
        pid = str(profile_id or "").strip()
        if not pid:
            return set()
        out: Set[str] = set()
        try:
            with self._db() as db:
                rows = db.execute(
                    """
                    SELECT postUrl
                    FROM automation_comment_events
                    WHERE profileId = ?
                    ORDER BY createdAt DESC
                    LIMIT 3000
                    """,
                    (pid,),
                ).fetchall()
            for row in rows:
                norm = self._normalize_url(str(row["postUrl"] or ""))
                if norm:
                    out.add(norm)
        except Exception:
            return set()
        return out

    def _prune_already_commented_queue_items(self, profile_id: Optional[str], commented_posts: Set[str]) -> int:
        pid = str(profile_id or "").strip()
        if not pid or not commented_posts:
            return 0
        removed = 0
        try:
            with self._db() as db:
                rows = db.execute("SELECT id, postId FROM queue_items WHERE profileId = ?", (pid,)).fetchall()
                for row in rows:
                    item_id = str(row["id"] or "").strip()
                    post_id = self._normalize_url(str(row["postId"] or ""))
                    if not item_id or not post_id:
                        continue
                    if post_id in commented_posts:
                        db.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
                        removed += 1
                if removed > 0:
                    db.commit()
        except Exception:
            return 0
        return removed

    def _reschedule_overdue_queue_items(self, grace_seconds: int = 20, spacing_seconds: int = 35) -> int:
        now_dt = datetime.now()
        try:
            with self._db() as db:
                overdue = db.execute(
                    """
                    SELECT id
                    FROM queue_items
                    WHERE scheduledFor <= ?
                    ORDER BY scheduledFor ASC, id ASC
                    """,
                    (now_dt.isoformat(timespec="seconds"),),
                ).fetchall()
                if not overdue:
                    return 0
                shifted = 0
                for idx, row in enumerate(overdue):
                    item_id = str(row["id"] or "").strip()
                    if not item_id:
                        continue
                    target_dt = now_dt + timedelta(seconds=max(1, int(grace_seconds)) + idx * max(5, int(spacing_seconds)))
                    scheduled = target_dt.isoformat(timespec="seconds")
                    display_time = _format_display_time(target_dt)
                    countdown = max(0, int((target_dt - now_dt).total_seconds()))
                    priority = max(1, int((target_dt - now_dt).total_seconds() // 60) * -1 + 100)
                    db.execute(
                        """
                        UPDATE queue_items
                        SET scheduledTime = ?, scheduledFor = ?, priorityScore = ?, countdown = ?
                        WHERE id = ?
                        """,
                        (display_time, scheduled, priority, countdown, item_id),
                    )
                    shifted += 1
                db.commit()
                return shifted
        except Exception:
            return 0

    def _defer_due_queue_items(self, delay_seconds: int, spacing_seconds: int = 2) -> int:
        delay = max(1, int(delay_seconds))
        spacing = max(0, int(spacing_seconds))
        now_dt = datetime.now()
        now_iso = now_dt.isoformat(timespec="seconds")
        try:
            with self._db() as db:
                due_rows = db.execute(
                    """
                    SELECT id
                    FROM queue_items
                    WHERE scheduledFor <= ?
                    ORDER BY scheduledFor ASC, id ASC
                    """,
                    (now_iso,),
                ).fetchall()
                if not due_rows:
                    return 0
                shifted = 0
                for idx, row in enumerate(due_rows):
                    item_id = str(row["id"] or "").strip()
                    if not item_id:
                        continue
                    target_dt = now_dt + timedelta(seconds=delay + idx * spacing)
                    scheduled = target_dt.isoformat(timespec="seconds")
                    display_time = _format_display_time(target_dt)
                    countdown = max(0, int((target_dt - now_dt).total_seconds()))
                    priority = max(1, int((target_dt - now_dt).total_seconds() // 60) * -1 + 100)
                    db.execute(
                        """
                        UPDATE queue_items
                        SET scheduledTime = ?, scheduledFor = ?, priorityScore = ?, countdown = ?
                        WHERE id = ?
                        """,
                        (display_time, scheduled, priority, countdown, item_id),
                    )
                    shifted += 1
                db.commit()
                return shifted
        except Exception:
            return 0

    def _check_profile_session_status_sync(self, profile: Dict[str, Any]) -> str:
        profile_id = profile.get("id")
        manager = SkoolSessionManager(
            account_id=profile_id,
            email=profile.get("email", ""),
            password=profile.get("password", ""),
            proxy=profile.get("proxy"),
            base_dir=self.accounts_dir,
            headless=True,
        )
        status = "logged_out"
        try:
            with _PLAYWRIGHT_SYNC_LOCK:
                manager.launch()
                session_status = manager.validate_session()
                if session_status == "valid":
                    status = "ready"
                elif session_status == "blocked":
                    status = "blocked"
                elif session_status == "captcha":
                    status = "captcha"
                elif session_status in {"login_required", "error"}:
                    status = "logged_out"
        except Exception:
            status = "logged_out"
        finally:
            manager.close()

        self._update_profile_status_in_db(profile_id, status)
        return status

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA synchronous = NORMAL")
        except Exception:
            pass
        return conn

    def _write_with_retry(self, operation: Callable[[], None], attempts: int = 10, sleep_seconds: float = 0.2) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                operation()
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                last_exc = exc
                if attempt == attempts - 1:
                    break
                # Small incremental backoff reduces collision between background tasks.
                time.sleep(sleep_seconds * (attempt + 1))
        if last_exc:
            raise last_exc

    def _load_settings_from_db(self) -> Dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT value FROM automation_settings WHERE key = 'default'").fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["value"]) if row["value"] else {}
        except Exception:
            return {}

    def _infer_log_module_action(self, message: str) -> Tuple[str, str]:
        text = str(message or "").strip()
        lower = text.lower()

        if "proxy" in lower:
            if "check" in lower or "passed" in lower or "retry" in lower or "failed" in lower:
                return "proxy", "check"
            return "proxy", "event"

        if any(token in lower for token in ("chat", "inbox sync", "dm send", "dm ", "conversation")):
            if "started" in lower:
                return "chats", "start"
            if "retry" in lower:
                return "chats", "retry"
            if "failed" in lower or "error" in lower:
                return "chats", "fail"
            if "send" in lower:
                return "chats", "send"
            if "sync" in lower or "imported" in lower or "complete" in lower:
                return "chats", "sync"
            return "chats", "event"

        if "queue" in lower or "task=" in lower:
            if any(token in lower for token in ("added", "enqueued")):
                return "queue", "add"
            if "updated" in lower or "expedited" in lower:
                return "queue", "update"
            if "removed" in lower or "delete" in lower or "deleted" in lower:
                return "queue", "remove"
            if "requeued" in lower:
                return "queue", "requeue"
            if "start" in lower or "execute" in lower:
                return "queue", "execute"
            if "prefill" in lower or "scan" in lower:
                return "queue", "prefill"
            return "queue", "event"

        if any(token in lower for token in ("profile", "login check", "session", "scheduler")):
            if "check" in lower:
                return "profiles", "check"
            if "queued" in lower:
                return "profiles", "queue"
            if "running" in lower or "pass" in lower:
                return "profiles", "run"
            if "paused" in lower:
                return "profiles", "pause"
            if "resumed" in lower:
                return "profiles", "resume"
            if "outside schedule" in lower:
                return "profiles", "schedule"
            if "error" in lower or "failed" in lower:
                return "profiles", "fail"
            return "profiles", "event"

        if any(token in lower for token in ("openai", "api key", "ai auto")):
            if "check" in lower or "test" in lower:
                return "openai", "check_key"
            if "generate" in lower or "reply" in lower:
                return "openai", "generate"
            return "openai", "event"

        return "system", "event"

    def _insert_log(self, event: Dict[str, Any]) -> None:
        profile = str(event.get("profile") or "SYSTEM")
        status = str(event.get("status") or "info").lower()
        message = str(event.get("message") or "").strip()
        inferred_module, inferred_action = self._infer_log_module_action(message)
        module = str(event.get("module") or inferred_module or "system").strip().lower() or "system"
        action = str(event.get("action") or inferred_action or "event").strip().lower() or "event"
        event["profile"] = profile
        event["status"] = status
        event["message"] = message
        event["module"] = module
        event["action"] = action

        def _op() -> None:
            with self._db() as db:
                try:
                    db.execute(
                        "INSERT INTO logs (id, timestamp, profile, status, module, action, message, fallbackLevelUsed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            event["id"],
                            event["timestamp"],
                            profile,
                            status,
                            module,
                            action,
                            message,
                            str(event.get("fallbackLevelUsed") or "").strip() or None,
                        ),
                    )
                except sqlite3.OperationalError as exc:
                    err_lower = str(exc).lower()
                    if "no column named module" not in err_lower and "no column named action" not in err_lower:
                        raise
                    db.execute(
                        "INSERT INTO logs (id, timestamp, profile, status, message, fallbackLevelUsed) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            event["id"],
                            event["timestamp"],
                            profile,
                            status,
                            message,
                            str(event.get("fallbackLevelUsed") or "").strip() or None,
                        ),
                    )
                db.commit()
        self._write_with_retry(_op)
        line = f"[AUTOMATION][{module.upper()}:{action.upper()}][{status.upper()}][{profile}] {message}"
        if status == "error":
            LOGGER.error(line)
        elif status == "retry":
            LOGGER.warning(line)
        else:
            LOGGER.info(line)

    def _persist_activity_rows(self, activity_rows: List[Dict[str, Any]]) -> None:
        if not activity_rows:
            return
        with self._db() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_comment_events (
                    id TEXT PRIMARY KEY,
                    profileId TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    community TEXT NOT NULL,
                    postUrl TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    commentText TEXT NOT NULL,
                    createdAt TEXT NOT NULL
                )
                """
            )
            for row in activity_rows:
                event_id = row.get("id", str(uuid.uuid4()))
                db.execute(
                    """
                    INSERT INTO activity_feed (id, profile, groupName, action, timestamp, postUrl)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        row.get("profileLabel", ""),
                        row.get("community", ""),
                        row.get("result", "Commented"),
                        row.get("timestamp", datetime.now(timezone.utc).isoformat()),
                        row.get("postUrl", ""),
                    ),
                )
                db.execute(
                    """
                    INSERT OR REPLACE INTO automation_comment_events
                    (id, profileId, profile, community, postUrl, keyword, prompt, commentText, createdAt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        str(row.get("profileId") or ""),
                        str(row.get("profileLabel") or ""),
                        str(row.get("community") or ""),
                        str(row.get("postUrl") or ""),
                        str(row.get("keywordMatched") or ("general" if str(row.get("matchSource") or "") == "general" else "")),
                        str(row.get("promptUsed") or ""),
                        str(row.get("aiReply") or ""),
                        str(row.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                    ),
                )
            db.commit()

    def _upsert_queue_item(
        self,
        profile_id: str,
        profile_name: str,
        community_id: str,
        community_name: str,
        keyword: str,
        post_url: str,
        scheduled_for: Optional[datetime] = None,
    ) -> None:
        canonical_post_url = self._normalize_url(post_url) or str(post_url or "").strip()
        if not canonical_post_url:
            return
        target_dt = scheduled_for or datetime.now()
        scheduled = target_dt.isoformat(timespec="seconds")
        display_time = _format_display_time(target_dt)
        with self._db() as db:
            existing = None
            rows = db.execute(
                "SELECT id, postId FROM queue_items WHERE profileId = ?",
                (profile_id,),
            ).fetchall()
            for row in rows:
                row_post = self._normalize_url(str(row["postId"] or ""))
                if row_post and row_post == canonical_post_url:
                    existing = row
                    break
            item_id = existing["id"] if existing else str(uuid.uuid4())
            priority = max(1, int((target_dt - datetime.now()).total_seconds() // 60) * -1 + 100)
            countdown = max(0, int((target_dt - datetime.now()).total_seconds()))
            if existing:
                db.execute(
                    """
                    UPDATE queue_items
                    SET profile = ?, community = ?, communityId = ?, keyword = ?, postId = ?, scheduledTime = ?, scheduledFor = ?, priorityScore = ?, countdown = ?
                    WHERE id = ?
                    """,
                    (
                        profile_name,
                        community_name,
                        community_id,
                        keyword,
                        canonical_post_url,
                        display_time,
                        scheduled,
                        priority,
                        countdown,
                        item_id,
                    ),
                )
                db.commit()
                self._insert_log(
                    {
                        "id": str(uuid.uuid4()),
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "profile": str(profile_name or profile_id or "SYSTEM"),
                        "status": "success",
                        "message": (
                            f"[SKOOL] Queue task updated task={item_id} "
                            f"community={community_name or community_id or 'community'} "
                            f"scheduled={display_time}"
                        ),
                    }
                )
                return
            db.execute(
                """
                INSERT INTO queue_items
                (id, profile, profileId, community, communityId, postId, keyword, keywordId, scheduledTime, scheduledFor, priorityScore, countdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    profile_name,
                    profile_id,
                    community_name,
                    community_id,
                    canonical_post_url,
                    keyword,
                    "",
                    display_time,
                    scheduled,
                    priority,
                    countdown,
                ),
            )
            db.commit()
            self._insert_log(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "profile": str(profile_name or profile_id or "SYSTEM"),
                    "status": "success",
                    "message": (
                        f"[SKOOL] Queue task added task={item_id} "
                        f"community={community_name or community_id or 'community'} "
                        f"scheduled={display_time}"
                    ),
                }
            )

    def _remove_queue_item(self, profile_id: str, post_url: str) -> None:
        canonical_post_url = self._normalize_url(post_url) or str(post_url or "").strip()
        if not canonical_post_url:
            return
        with self._db() as db:
            rows = db.execute(
                "SELECT id, postId FROM queue_items WHERE profileId = ?",
                (profile_id,),
            ).fetchall()
            ids_to_delete: List[str] = []
            for row in rows:
                row_post = self._normalize_url(str(row["postId"] or ""))
                if row_post and row_post == canonical_post_url:
                    ids_to_delete.append(str(row["id"] or "").strip())
            for item_id in ids_to_delete:
                if item_id:
                    db.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
            db.commit()

    def _prune_stale_queue(self, profile_id: str) -> None:
        threshold = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        with self._db() as db:
            db.execute("DELETE FROM queue_items WHERE profileId = ? AND scheduledFor < ?", (profile_id, threshold))
            db.commit()

    def _dedupe_queue_items_for_profile(self, profile_id: str) -> None:
        pid = str(profile_id or "").strip()
        if not pid:
            return
        with self._db() as db:
            rows = db.execute(
                "SELECT id, postId, scheduledFor FROM queue_items WHERE profileId = ? ORDER BY scheduledFor ASC, id ASC",
                (pid,),
            ).fetchall()
            seen: Set[str] = set()
            dup_ids: List[str] = []
            for row in rows:
                key = self._normalize_url(str(row["postId"] or "")) or ""
                if not key:
                    continue
                if key in seen:
                    dup_ids.append(str(row["id"] or "").strip())
                    continue
                seen.add(key)
            for item_id in dup_ids:
                if item_id:
                    db.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
            if dup_ids:
                db.commit()

    def _update_profile_status_in_db(self, profile_id: str, status: str) -> None:
        def _op() -> None:
            with self._db() as db:
                db.execute("UPDATE profiles SET status = ? WHERE id = ?", (status, profile_id))
                db.commit()
        self._write_with_retry(_op)


    def set_ensure_profile_auth(self, fn: Callable[[str], dict]) -> None:
        self._ensure_profile_auth_fn = fn

    def run_profile_login_refresh_sync(self, profile_id: str) -> Dict[str, Any]:
        """Blocking. Runs login refresh. Returns result dict. Saves cookie_json on success."""
        profile = self._load_profile_for_session(profile_id)
        if not profile:
            return {"success": False, "status": "invalid_credentials", "message": "Profile not found"}
        manager = SkoolSessionManager(
            account_id=profile_id,
            email=profile.get("email", ""),
            password=profile.get("password", ""),
            proxy=profile.get("proxy"),
            base_dir=self.accounts_dir,
            headless=True,
        )
        try:
            with _PLAYWRIGHT_SYNC_LOCK:
                manager.launch()
                session_status = manager.validate_session()
                if session_status == "valid":
                    manager.update_state("ready")
                    cookie_json = manager.get_cookies_json()
                    if cookie_json:
                        self._save_profile_cookie_json(profile_id, cookie_json)
                    return {"success": True, "status": "ready", "message": "Session is active", "cookie_json": cookie_json}
                if session_status == "blocked":
                    manager.update_state("blocked")
                    return {"success": False, "status": "network_error", "message": "Account is blocked or access denied"}
                if session_status == "captcha":
                    manager.update_state("captcha")
                    return {"success": False, "status": "captcha", "message": "Captcha required"}
                if not profile.get("email") or not profile.get("password"):
                    manager.update_state("logged_out")
                    return {"success": False, "status": "invalid_credentials", "message": "Missing email or password"}
                login_result = manager.perform_login()
                manager.page.wait_for_timeout(2500)
                if login_result == "success":
                    recheck = manager.validate_session()
                    if recheck == "valid":
                        manager.update_state("ready")
                        cookie_json = manager.get_cookies_json()
                        if cookie_json:
                            self._save_profile_cookie_json(profile_id, cookie_json)
                        return {"success": True, "status": "ready", "message": "Session is active", "cookie_json": cookie_json}
                if login_result == "failed":
                    manager.update_state("logged_out")
                    return {"success": False, "status": "invalid_credentials", "message": "Credentials are invalid"}
                if login_result == "captcha" or manager.detect_captcha():
                    manager.update_state("captcha")
                    return {"success": False, "status": "captcha", "message": "Captcha required"}
                if login_result == "blocked" or manager.detect_blocked():
                    manager.update_state("blocked")
                    return {"success": False, "status": "network_error", "message": "Account is blocked or access denied"}
                current_url = str(getattr(manager.page, "url", "") or "").lower()
                if "/login" not in current_url and manager.has_authenticated_markers():
                    manager.update_state("ready")
                    cookie_json = manager.get_cookies_json()
                    if cookie_json:
                        self._save_profile_cookie_json(profile_id, cookie_json)
                    return {"success": True, "status": "ready", "message": "Session is active", "cookie_json": cookie_json}
                if "/login" in current_url:
                    manager.update_state("logged_out")
                    return {"success": False, "status": "invalid_credentials", "message": "Still on login page after login attempt"}
                manager.update_state("error")
                return {"success": False, "status": "network_error", "message": "Could not validate session"}
        except Exception as exc:
            err = str(exc)
            low = err.lower()
            if "proxy" in low:
                return {"success": False, "status": "proxy_error", "message": "Proxy connection failed"}
            if "timeout" in low:
                return {"success": False, "status": "network_error", "message": "Network timeout"}
            return {"success": False, "status": "network_error", "message": err[:300] or "Unknown error"}
        finally:
            manager.close()

    def _save_profile_cookie_json(self, profile_id: str, cookie_json: str) -> None:
        """Save extracted cookies to profile for API-based fetch."""
        if not cookie_json or not str(cookie_json).strip():
            return
        def _op() -> None:
            with self._db() as db:
                pc = {str(r["name"]) for r in db.execute("PRAGMA table_info(profiles)").fetchall()}
                if "cookie_json" not in pc:
                    db.execute("ALTER TABLE profiles ADD COLUMN cookie_json TEXT")
                db.execute("UPDATE profiles SET cookie_json = ? WHERE id = ?", (cookie_json, profile_id))
                db.commit()
        self._write_with_retry(_op)
        LOGGER.info("[SKOOL] Saved cookie_json for profile %s", profile_id)

    def _increment_profile_daily_usage(self, profile_id: Optional[str], amount: int) -> None:
        if not profile_id or amount <= 0:
            return
        with self._db() as db:
            db.execute(
                "UPDATE profiles SET dailyUsage = COALESCE(dailyUsage, 0) + ? WHERE id = ?",
                (amount, profile_id),
            )
            db.commit()

    def _reset_all_profile_daily_usage(self) -> None:
        with self._db() as db:
            db.execute("UPDATE profiles SET dailyUsage = 0")
            db.commit()

    def _increment_community_action_counters(self, community_id: str, is_keyword_match: bool) -> None:
        if not community_id:
            return
        with self._db() as db:
            db.execute(
                """
                UPDATE communities
                SET actionsToday = COALESCE(actionsToday, 0) + 1,
                    totalScannedPosts = COALESCE(totalScannedPosts, 0) + 1,
                    totalKeywordMatches = COALESCE(totalKeywordMatches, 0) + ?,
                    matchesToday = COALESCE(matchesToday, 0) + ?,
                    lastScanned = ?
                WHERE id = ?
                """,
                (
                    1 if is_keyword_match else 0,
                    1 if is_keyword_match else 0,
                    datetime.now().strftime("%H:%M:%S"),
                    community_id,
                ),
            )
            db.commit()

    def _pause_community_for_membership_pending(
        self,
        community_id: str,
        community_name: str,
        profile_label: str,
    ) -> None:
        cid = str(community_id or "").strip()
        if not cid:
            return

        def _op() -> None:
            with self._db() as db:
                db.execute(
                    "UPDATE communities SET status = 'paused' WHERE id = ?",
                    (cid,),
                )
                db.commit()

        try:
            self._write_with_retry(_op)
            self._insert_log(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "profile": str(profile_label or "SYSTEM"),
                    "status": "info",
                    "message": (
                        "[SKOOL] Community auto-paused: "
                        f"membership_pending_approval community={community_name or cid}"
                    ),
                }
            )
        except Exception:
            return

    def _pause_community_for_archived_read_only(
        self,
        community_id: str,
        community_name: str,
        profile_label: str,
    ) -> None:
        cid = str(community_id or "").strip()
        if not cid:
            return

        def _op() -> None:
            with self._db() as db:
                db.execute(
                    "UPDATE communities SET status = 'paused' WHERE id = ?",
                    (cid,),
                )
                db.commit()

        try:
            self._write_with_retry(_op)
            self._insert_log(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "profile": str(profile_label or "SYSTEM"),
                    "status": "info",
                    "message": (
                        "[SKOOL] Community auto-paused: "
                        f"archived_read_only community={community_name or cid}"
                    ),
                }
            )
        except Exception:
            return

    def _reset_all_community_daily_counters(self) -> None:
        with self._db() as db:
            db.execute("UPDATE communities SET actionsToday = 0, matchesToday = 0")
            db.commit()

    def _reset_daily_counters_if_needed(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        state: Dict[str, Any] = {}
        if self.daily_counters_state_file.exists():
            try:
                with self.daily_counters_state_file.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        state = loaded
            except Exception:
                state = {}

        if state.get("last_reset_date") == today:
            return

        self._reset_all_profile_daily_usage()
        self._reset_all_community_daily_counters()
        try:
            with self.daily_counters_state_file.open("w", encoding="utf-8") as f:
                json.dump({"last_reset_date": today}, f)
        except Exception:
            return

    def _get_profile_status_from_db(self, profile_id: str) -> Optional[str]:
        with self._db() as db:
            row = db.execute("SELECT status FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return row["status"] if row else None
    def _load_runtime_config_from_db(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        with self._db() as db:
            profiles = db.execute("SELECT * FROM profiles ORDER BY name").fetchall()
            communities = db.execute("SELECT * FROM communities ORDER BY name").fetchall()
            rules = db.execute("SELECT * FROM keyword_rules WHERE active = 1 ORDER BY keyword").fetchall()
            settings_row = db.execute("SELECT value FROM automation_settings WHERE key = 'default'").fetchone()

        settings = json.loads(settings_row["value"]) if settings_row else {}
        communities_by_profile: Dict[str, List[Dict[str, Any]]] = {}
        for row in communities:
            communities_by_profile.setdefault(row["profileId"], []).append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "url": row["url"],
                    "dailyLimit": int(row["dailyLimit"] or 0),
                    "actionsToday": int(row["actionsToday"] or 0),
                    "matchesToday": int(row["matchesToday"] or 0),
                    "status": row["status"],
                }
            )

        rules_norm: List[Dict[str, Any]] = []
        for row in rules:
            rules_norm.append({
                "keyword": row["keyword"],
                "prompt": row["commentPrompt"] or row["promptPreview"] or "",
                "assigned": json.loads(row["assignedProfileIds"]),
                "dmMaxReplies": row["dmMaxReplies"] or 1,
            })

        profile_list: List[Dict[str, Any]] = []
        for row in profiles:
            pid = row["id"]
            applied = [r for r in rules_norm if not r["assigned"] or pid in r["assigned"]]
            profile = {
                "id": pid,
                "name": row["name"],
                "label": row["name"],
                "email": row["email"],
                "password": decrypt_secret(row["password"]),
                "proxy": row["proxy"],
                "delay_min": settings.get("delayMin", 30),
                "delay_max": settings.get("delayMax", 90),
                "visits": settings.get("globalDailyCapPerAccount", 5),
                "repliesPerVisit": max([int(r["dmMaxReplies"] or 1) for r in applied], default=1),
                "visitsCompleted": int(row["dailyUsage"] or 0),
                "repliesCompleted": 0,
                "status": "idle",
                "keywords": [r["keyword"] for r in applied],
                "keywordTriggeredPrompt": "\n".join([r["prompt"] for r in applied if r["prompt"]]).strip(),
                "generalEngagementPrompt": settings.get("commentFallbackPrompt", ""),
                "communities": communities_by_profile.get(pid, []),
                "enabled": row["status"] != "paused",
                "runFrom": settings.get("runFrom", "00:00"),
                "runTo": settings.get("runTo", "23:59"),
                "days": {d: True for d in settings.get("activeDays", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])},
            }
            self._merge_profile_from_account_file(profile)
            profile_list.append(profile)

        global_settings = dict(settings)
        global_settings["apiKey"] = self._get_openai_key()
        return profile_list, global_settings

    def _merge_profiles(self, db_profiles: List[Dict[str, Any]], request_profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not request_profiles:
            return db_profiles
        req = {p.get("id"): p for p in request_profiles if p.get("id")}
        return [{**p, **req.get(p.get("id"), {})} for p in db_profiles]

    def _validate_start_payload(self, profiles: List[Dict[str, Any]], settings: Dict[str, Any]) -> None:
        if settings.get("masterEnabled") is False:
            raise RuntimeError("Validation failed: master automation is disabled")

        enabled_profiles = [p for p in profiles if p.get("enabled", True) is not False]
        if not enabled_profiles:
            raise RuntimeError("Validation failed: no enabled profiles found")

        has_community = False
        for profile in enabled_profiles:
            communities = profile.get("communities", [])
            for community in communities:
                if community.get("url"):
                    has_community = True
                    break
            if has_community:
                break
        if not has_community:
            raise RuntimeError("Validation failed: no community URL configured in enabled profiles")

        api_key = (settings.get("apiKey") or self._get_openai_key()).strip()
        if not api_key:
            raise RuntimeError("Validation failed: no OpenAI API key configured")

    def _apply_persisted_counters(self, profiles: List[Dict[str, Any]], persisted: Dict[str, Any]) -> List[Dict[str, Any]]:
        by_id = {p.get("id"): p for p in persisted.get("profiles", [])}
        for profile in profiles:
            saved = by_id.get(profile.get("id"))
            if saved:
                profile["visitsCompleted"] = saved.get("visitsCompleted", 0)
                profile["repliesCompleted"] = saved.get("repliesCompleted", 0)
                profile["status"] = saved.get("status", "idle")
                if "_current_community_index" in saved:
                    profile["_current_community_index"] = int(saved.get("_current_community_index", 0) or 0)
        return profiles

    def _refresh_runtime_profiles_locked(self, db_profiles: List[Dict[str, Any]]) -> None:
        current_by_id = {str(p.get("id") or ""): p for p in self._state.profiles}
        refreshed: List[Dict[str, Any]] = []
        for base_profile in db_profiles:
            pid = str(base_profile.get("id") or "")
            current = current_by_id.get(pid)
            merged = dict(base_profile)
            if current:
                merged["visitsCompleted"] = int(current.get("visitsCompleted", merged.get("visitsCompleted", 0)) or 0)
                merged["repliesCompleted"] = int(current.get("repliesCompleted", merged.get("repliesCompleted", 0)) or 0)
                if "_current_community_index" in current:
                    merged["_current_community_index"] = int(current.get("_current_community_index", 0) or 0)
                if not bool(merged.get("enabled", True)):
                    merged["status"] = "paused"
                else:
                    prev_status = str(current.get("status") or "idle")
                    merged["status"] = "idle" if prev_status == "paused" else prev_status
            refreshed.append(merged)
        self._state.profiles = refreshed

    def _get_next_profile_locked(self) -> Tuple[Optional[Dict[str, Any]], int]:
        enabled = [p for p in self._state.profiles if p.get("enabled", True)]
        if not enabled:
            return None, self._state.current_profile_index
        attempts = 0
        idx = self._state.current_profile_index
        while attempts < len(enabled):
            profile = enabled[idx % len(enabled)]
            idx = (idx + 1) % len(enabled)
            if profile.get("status") in {"finished", "blocked"}:
                attempts += 1
                continue
            if profile.get("visitsCompleted", 0) >= profile.get("visits", 5):
                profile["status"] = "finished"
                attempts += 1
                continue
            return profile, idx
        return None, idx

    def _update_profile_locked(self, profile: Dict[str, Any]) -> None:
        for i, p in enumerate(self._state.profiles):
            if p.get("id") == profile.get("id"):
                self._state.profiles[i] = profile
                break

    def _check_schedule(self, settings: Dict[str, Any]) -> bool:
        now = datetime.now()
        current = now.hour * 60 + now.minute
        from_h, from_m = map(int, settings.get("runFrom", "00:00").split(":"))
        to_h, to_m = map(int, settings.get("runTo", "23:59").split(":"))
        from_time = from_h * 60 + from_m
        to_time = to_h * 60 + to_m
        in_window = from_time <= current < to_time if from_time < to_time else (current >= from_time or current < to_time)
        day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][now.weekday()]
        return in_window and bool(settings.get("days", {}).get(day, True))

    def _seconds_until_next_run_for_all(self) -> int:
        wait_seconds = 86400
        for p in self._state.profiles:
            if not p.get("enabled", True):
                continue
            wait_seconds = min(wait_seconds, _seconds_until_next_run(p.get("runFrom", "00:00"), p.get("days", {})))
        return wait_seconds

    def _seconds_until_next_daily_reset(self) -> int:
        now = datetime.now()
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        return max(30, int((next_reset - now).total_seconds()))

    def _profile_has_prefill_capacity_today(self, profile: Dict[str, Any]) -> bool:
        visits_limit = max(0, int(profile.get("visits", 0) or 0))
        visits_done = max(0, int(profile.get("visitsCompleted", 0) or 0))
        if visits_limit > 0 and visits_done >= visits_limit:
            return False
        profile_id = str(profile.get("id") or "").strip()
        communities = list(profile.get("communities") or [])
        if not communities:
            return False
        for community in communities:
            if str(community.get("status", "active")).lower() != "active":
                continue
            community_url = self._normalize_url(community.get("url"))
            if not community_url:
                continue
            community_id = str(community.get("id") or "").strip()
            daily_limit = max(0, int(community.get("dailyLimit") or 0))
            actions_today = max(0, int(community.get("actionsToday") or 0))
            queued_today = self._count_pending_queue_for_profile_community_today(profile_id, community_id)
            if daily_limit > 0 and (actions_today + queued_today) >= daily_limit:
                continue
            return True
        return False

    def _save_run_state_locked(self) -> None:
        payload = {
            "profiles": [{"id": p.get("id"), "visitsCompleted": p.get("visitsCompleted", 0), "repliesCompleted": p.get("repliesCompleted", 0), "status": p.get("status", "idle"), "_current_community_index": int(p.get("_current_community_index", 0) or 0)} for p in self._state.profiles],
            "stats": self._state.stats,
            "run_state": self._state.run_state,
            "current_profile_index": self._state.current_profile_index,
            "last_updated": datetime.now().isoformat(),
            "state_day": datetime.now().strftime("%Y-%m-%d"),
        }
        with self.run_state_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _load_run_state_file(self) -> Optional[Dict[str, Any]]:
        if not self.run_state_file.exists():
            return None
        try:
            with self.run_state_file.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _hydrate_state_from_disk(self) -> None:
        persisted = self._load_run_state_file()
        if not persisted:
            return
        self._state.run_state = persisted.get("run_state", "idle")
        self._state.current_profile_index = int(persisted.get("current_profile_index", 0))
        self._state.stats = persisted.get("stats", self._state.stats)

    def _load_blacklist(self) -> Set[str]:
        if not self.blacklist_file.exists():
            return set()
        try:
            with self.blacklist_file.open("r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()

    def _save_blacklist(self, blacklist: Set[str]) -> None:
        with self.blacklist_file.open("w", encoding="utf-8") as f:
            json.dump(list(blacklist), f)

    def _is_url_blacklisted(self, url: str, blacklist: Set[str], preview_text: Optional[str] = None) -> bool:
        if url in blacklist:
            return True
        normalized = _normalize_skool_url(url)
        if normalized in blacklist:
            return True
        if "?p=" in url:
            pid = url.split("?p=", 1)[1].split("&", 1)[0]
            for entry in blacklist:
                if f"?p={pid}" in entry:
                    return True
        text_key = _blacklist_text_key(preview_text)
        return bool(text_key and text_key in blacklist)

    def _add_to_blacklist(self, url: str, blacklist: Set[str], preview_text: Optional[str] = None) -> None:
        blacklist.add(url)
        blacklist.add(_normalize_skool_url(url))
        key = _blacklist_text_key(preview_text)
        if key:
            blacklist.add(key)

    def _parse_post_timestamp(self, raw_value: str) -> Optional[float]:
        text = str(raw_value or "").strip()
        if not text:
            return None

        lowered = text.lower()
        if lowered in {"now", "just now", "today"}:
            return datetime.now(timezone.utc).timestamp()
        if lowered == "yesterday":
            return (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()

        rel = re.search(r"(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks|mo|month|months|y|year|years)\b", lowered)
        if rel:
            amount = max(0, int(rel.group(1)))
            unit = rel.group(2)
            seconds = 0
            if unit.startswith("s"):
                seconds = amount
            elif unit.startswith("m") and unit not in {"mo", "month", "months"}:
                seconds = amount * 60
            elif unit.startswith("h"):
                seconds = amount * 3600
            elif unit.startswith("d"):
                seconds = amount * 86400
            elif unit.startswith("w"):
                seconds = amount * 7 * 86400
            elif unit.startswith("mo"):
                seconds = amount * 30 * 86400
            elif unit.startswith("y"):
                seconds = amount * 365 * 86400
            if seconds >= 0:
                return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).timestamp()

        candidates = [text, text.replace("Z", "+00:00")]
        for candidate in candidates:
            try:
                parsed = datetime.fromisoformat(candidate)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.timestamp()
            except Exception:
                continue

        for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d", "%B %d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                now_utc = datetime.now(timezone.utc)
                if "%Y" not in fmt:
                    parsed = parsed.replace(year=now_utc.year)
                parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed.timestamp() > now_utc.timestamp() + 86400:
                    parsed = parsed.replace(year=parsed.year - 1)
                return parsed.timestamp()
            except Exception:
                continue
        return None

    def _parse_relative_age_from_meta(self, raw_value: str) -> Optional[float]:
        text = str(raw_value or "").strip().replace("•", " ")
        if not text:
            return None
        # Prefer compact Skool age tokens at the start of metadata, e.g. "28d", "5h", "2w".
        m = re.match(r"^\s*(\d+)\s*(mo|[smhdwy])\b", text.lower())
        if not m:
            return None
        amount = max(0, int(m.group(1)))
        unit = m.group(2)
        seconds = 0
        if unit == "s":
            seconds = amount
        elif unit == "m":
            seconds = amount * 60
        elif unit == "h":
            seconds = amount * 3600
        elif unit == "d":
            seconds = amount * 86400
        elif unit == "w":
            seconds = amount * 7 * 86400
        elif unit == "mo":
            seconds = amount * 30 * 86400
        elif unit == "y":
            seconds = amount * 365 * 86400
        return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).timestamp()

    def _extract_post_timestamp(self, post_item: Any) -> Optional[float]:
        try:
            time_elem = post_item.query_selector("time")
        except Exception:
            time_elem = None
        if time_elem is not None:
            for attr in ("datetime", "title", "aria-label"):
                try:
                    attr_value = time_elem.get_attribute(attr)
                    parsed = self._parse_post_timestamp(str(attr_value or ""))
                    if parsed is not None:
                        return parsed
                except Exception:
                    continue
            try:
                parsed = self._parse_post_timestamp(time_elem.inner_text().strip())
                if parsed is not None:
                    return parsed
            except Exception:
                pass

        # Skool feed metadata wrappers (strict selectors only, to avoid false matches from post body text).
        for selector in (
            '[class*="PostTimeContent"]',
            '[class*="PostTime"]',
            '[class*="TimeContent"]',
            '[class*="DateAndLabelWrapper"]',
            '[class*="PostDetailMeta"]',
        ):
            try:
                nodes = post_item.query_selector_all(selector)
            except Exception:
                nodes = []
            for node in nodes[:30]:
                try:
                    text = str(node.inner_text() or "").strip()
                except Exception:
                    continue
                if not text:
                    continue
                rel = self._parse_relative_age_from_meta(text)
                if rel is not None:
                    return rel
                parsed = self._parse_post_timestamp(text)
                if parsed is not None:
                    return parsed
        return None

    def _extract_page_post_timestamp(self, page: Any) -> Optional[float]:
        candidates: List[str] = []
        try:
            raw_values = page.evaluate(
                """
                () => {
                  const values = [];
                  const nodes = Array.from(document.querySelectorAll("main time, article time, time"));
                  for (const node of nodes) {
                    const dt = node.getAttribute("datetime");
                    const title = node.getAttribute("title");
                    const aria = node.getAttribute("aria-label");
                    const text = (node.innerText || "").trim();
                    if (dt) values.push(dt);
                    if (title) values.push(title);
                    if (aria) values.push(aria);
                    if (text) values.push(text);
                  }
                  const relNodes = Array.from(document.querySelectorAll("main [class*='PostTimeContent'], main [class*='PostTime'], main [class*='TimeContent'], main [class*='DateAndLabelWrapper'], main [class*='PostDetailMeta']"));
                  for (const node of relNodes) {
                    const text = (node.innerText || "").trim();
                    if (text) values.push(text);
                  }
                  return values.slice(0, 20);
                }
                """
            )
            if isinstance(raw_values, list):
                candidates.extend([str(v or "").strip() for v in raw_values if str(v or "").strip()])
        except Exception:
            pass

        for item in candidates:
            rel = self._parse_relative_age_from_meta(item)
            if rel is not None:
                return rel
            parsed = self._parse_post_timestamp(item)
            if parsed is not None:
                return parsed
        return None

    def _collect_feed_posts_newest_to_oldest(self, page: Any, max_scroll_rounds: int = 120) -> List[Dict[str, Any]]:
        seen: Set[str] = set()
        collected: List[Dict[str, str]] = []
        stable_rounds = 0
        previous_count = 0
        for _ in range(max_scroll_rounds):
            post_items = page.query_selector_all('div[class*="PostItemWrapper"]')
            for post_item in post_items:
                try:
                    links = post_item.query_selector_all('a[class*="__ChildrenLink"]')
                    if not links:
                        continue
                    href = links[-1].get_attribute("href")
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = f"https://www.skool.com{href}"
                    post_url = self._normalize_url(href)
                    if not post_url or post_url in seen:
                        continue
                    preview_elem = post_item.query_selector('div[class*="ContentPreviewWrapper"]')
                    preview = preview_elem.inner_text().strip() if preview_elem else ""
                    seen.add(post_url)
                    post_ts = self._extract_post_timestamp(post_item)
                    collected.append({"post_url": post_url, "preview_text": preview, "post_ts": post_ts})
                except Exception:
                    continue
            if len(collected) == previous_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                previous_count = len(collected)
            if stable_rounds >= 5:
                break
            try:
                page.mouse.wheel(0, 2600)
            except Exception:
                page.evaluate("() => window.scrollBy(0, Math.max(window.innerHeight, 1200))")
            page.wait_for_timeout(900)
        return collected

    def _extract_thread_targets(self, page: Any, preview_text: str) -> List[Dict[str, Any]]:
        targets: List[Dict[str, Any]] = []
        try:
            payload = page.evaluate(
                """
                () => {
                  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                  const root = document.querySelector("main") || document.body;
                  let postText = "";
                  const postSelectors = [
                    "main h1",
                    "main h2",
                    "main [class*='PostContent']",
                    "main [class*='Content']",
                    "main article",
                  ];
                  for (const selector of postSelectors) {
                    const node = document.querySelector(selector);
                    const text = clean(node && node.innerText ? node.innerText : "");
                    if (text && text.length > postText.length) postText = text;
                  }
                  if (!postText) postText = clean(root && root.innerText ? root.innerText : "");

                  const replies = [];
                  const seen = new Set();
                  const replySelectors = [
                    "main [class*='Comment']",
                    "main [class*='comment']",
                    "main [class*='Reply']",
                    "main [class*='reply']",
                    "main article",
                    "main li",
                  ];
                  for (const selector of replySelectors) {
                    const nodes = Array.from(document.querySelectorAll(selector));
                    for (const node of nodes) {
                      const text = clean(node && node.innerText ? node.innerText : "");
                      if (!text || text.length < 8) continue;
                      const lowered = text.toLowerCase();
                      if (seen.has(lowered)) continue;
                      seen.add(lowered);
                      replies.push(text);
                    }
                  }
                  return {
                    postText,
                    replies: replies.slice(-80).reverse(),
                  };
                }
                """
            )
        except Exception:
            payload = {}

        replies = payload.get("replies") if isinstance(payload, dict) else []
        if isinstance(replies, list):
            for item in replies:
                text = str(item or "").strip()
                if text:
                    targets.append({"text": text, "is_reply": True})

        post_text = str((payload or {}).get("postText") or "").strip() if isinstance(payload, dict) else ""
        if post_text:
            targets.append({"text": post_text, "is_reply": False})

        if not targets and preview_text.strip():
            targets.append({"text": preview_text.strip(), "is_reply": False})
        return targets

    def _focus_reply_target_editor(self, page: Any, reply_text: str) -> None:
        snippet = " ".join(str(reply_text or "").split())
        if not snippet:
            return
        snippet = snippet[:90]
        try:
            clicked = bool(
                page.evaluate(
                    """
                    ({ snippet }) => {
                      const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                      const needle = normalize(snippet);
                      if (!needle) return false;
                      const root = document.querySelector("main") || document.body;
                      const blocks = Array.from(root.querySelectorAll("article, li, div"));
                      for (const block of blocks) {
                        const text = normalize(block && block.innerText ? block.innerText : "");
                        if (!text || !text.includes(needle)) continue;
                        const button = Array.from(block.querySelectorAll("button")).find((btn) => normalize(btn.textContent) === "reply");
                        if (button) {
                          button.click();
                          return true;
                        }
                      }
                      return false;
                    }
                    """,
                    {"snippet": snippet},
                )
            )
            if clicked:
                page.wait_for_timeout(500)
        except Exception:
            return

    def _ensure_comment_editor(self, page: Any, timeout_ms: int = 15000) -> Optional[Any]:
        # Skool sometimes renders the post but delays/misses mounting the inline editor.
        # Try to activate the comment composer before giving up on the task.
        deadline = time.time() + max(4.0, float(timeout_ms) / 1000.0)
        selectors = [
            SKOOL_SELECTORS.get("comment_editor") or 'div[contenteditable="true"].tiptap.ProseMirror',
            'div[contenteditable="true"]',
        ]
        while time.time() < deadline:
            for selector in selectors:
                if not selector:
                    continue
                try:
                    editor = page.wait_for_selector(selector, timeout=1200, state="visible")
                    if editor:
                        return editor
                except Exception:
                    continue
            try:
                page.evaluate(
                    """
                    () => {
                      const normalize = (v) => (v || "").replace(/\\s+/g, " ").trim().toLowerCase();
                      const isVisible = (el) => {
                        if (!el) return false;
                        const st = window.getComputedStyle(el);
                        if (!st || st.display === "none" || st.visibility === "hidden") return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                      };
                      const buttons = Array.from(document.querySelectorAll("button"));
                      for (const btn of buttons) {
                        const text = normalize(btn.innerText || btn.textContent || "");
                        if (!text) continue;
                        if (text === "comment" || text === "reply" || text.includes("add comment")) {
                          if (isVisible(btn) && !btn.disabled) {
                            try { btn.scrollIntoView({ block: "center", inline: "center" }); } catch (_) {}
                            btn.click();
                            return true;
                          }
                        }
                      }
                      return false;
                    }
                    """
                )
            except Exception:
                pass
            try:
                page.wait_for_timeout(450)
            except Exception:
                break
        return None

    def _editor_contains_snippet(self, page: Any, snippet: str) -> bool:
        probe = " ".join(str(snippet or "").split())[:80]
        if not probe:
            return False
        try:
            return bool(
                page.evaluate(
                    """
                    ({ probe }) => {
                      const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                      const needle = normalize(probe);
                      if (!needle) return false;
                      const editor = document.querySelector('div[contenteditable="true"]');
                      if (!editor) return false;
                      const text = normalize(editor.innerText || editor.textContent || "");
                      return !!text && text.includes(needle);
                    }
                    """,
                    {"probe": probe},
                )
            )
        except Exception:
            return False

    def _click_send_button_js(self, page: Any) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                      const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                      const editor = document.querySelector('div[contenteditable="true"]');
                      const buttons = Array.from(document.querySelectorAll("button"));
                      if (!buttons.length) return false;
                      const isNearEditor = (btn) => {
                        if (!editor) return false;
                        try {
                          const e = editor.getBoundingClientRect();
                          const b = btn.getBoundingClientRect();
                          const dx = Math.abs((b.left + b.right) / 2 - (e.left + e.right) / 2);
                          const dy = Math.abs((b.top + b.bottom) / 2 - (e.top + e.bottom) / 2);
                          return dx <= 700 && dy <= 450;
                        } catch (_) {
                          return false;
                        }
                      };
                      const score = (btn) => {
                        const cls = normalize(btn.className || "");
                        const text = normalize(btn.innerText || btn.textContent || "");
                        let s = 0;
                        if (cls.includes("replybutton")) s += 5;
                        if (text === "comment" || text === "post" || text === "post reply") s += 6;
                        if (text === "reply" || text === "send" || text === "post comment") s += 4;
                        if (text.includes("comment")) s += 3;
                        if (text.includes("reply")) s += 2;
                        if (text.includes("send")) s += 2;
                        if (isNearEditor(btn)) s += 3;
                        if (!text) s -= 3;
                        if (btn.disabled) s -= 10;
                        return s;
                      };
                      const ordered = buttons
                        .map((btn) => ({ btn, s: score(btn) }))
                        .filter((x) => x.s > 0)
                        .sort((a, b) => b.s - a.s);
                      if (!ordered.length) return false;
                      const target = ordered[0].btn;
                      try { target.scrollIntoView({ block: "center", inline: "center" }); } catch (_) {}
                      target.click();
                      return true;
                    }
                    """
                )
            )
        except Exception:
            return False

    def _click_primary_comment_button(self, page: Any) -> bool:
        # Prefer visible "Comment" submit button near the active editor.
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                      const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                      const isVisible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        if (!style || style.visibility === "hidden" || style.display === "none") return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                      };
                      const editor = document.querySelector('div[contenteditable="true"]');
                      const buttons = Array.from(document.querySelectorAll("button"));
                      if (!buttons.length) return false;
                      const isNearEditor = (btn) => {
                        if (!editor) return true;
                        try {
                          const e = editor.getBoundingClientRect();
                          const b = btn.getBoundingClientRect();
                          const dx = Math.abs((b.left + b.right) / 2 - (e.left + e.right) / 2);
                          const dy = Math.abs((b.top + b.bottom) / 2 - (e.top + e.bottom) / 2);
                          return dx <= 750 && dy <= 500;
                        } catch (_) {
                          return false;
                        }
                      };
                      const score = (btn) => {
                        const text = normalize(btn.innerText || btn.textContent || "");
                        const cls = normalize(btn.className || "");
                        let s = 0;
                        if (text === "comment") s += 12;
                        if (text === "post comment" || text === "post reply") s += 8;
                        if (text === "send" || text === "reply") s += 4;
                        if (text.includes("comment")) s += 4;
                        if (cls.includes("replybutton")) s += 4;
                        if (isNearEditor(btn)) s += 5;
                        if (btn.disabled || !isVisible(btn)) s -= 20;
                        return s;
                      };
                      const candidates = buttons
                        .map((btn) => ({ btn, s: score(btn) }))
                        .filter((x) => x.s > 0)
                        .sort((a, b) => b.s - a.s);
                      if (!candidates.length) return false;
                      const target = candidates[0].btn;
                      try { target.scrollIntoView({ block: "center", inline: "center" }); } catch (_) {}
                      target.click();
                      return true;
                    }
                    """
                )
            )
        except Exception:
            return False

    def _submit_looks_confirmed(self, page: Any, comment_text: str) -> bool:
        # Primary signal: editor no longer contains typed text.
        if not self._editor_contains_snippet(page, comment_text):
            return True
        # Secondary signal: posted comment appears in visible thread text.
        probe = " ".join(str(comment_text or "").split())
        if not probe:
            return False
        if len(probe) > 80:
            probe = probe[:80]
        try:
            return bool(
                page.evaluate(
                    """
                    ({ probe }) => {
                      const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                      const needle = normalize(probe);
                      if (!needle) return false;
                      const root = document.querySelector("main") || document.body;
                      if (!root) return false;
                      const text = normalize(root.innerText || "");
                      return text.includes(needle);
                    }
                    """,
                    {"probe": probe},
                )
            )
        except Exception:
            return False

    def _submit_comment_with_fallback(self, page: Any, comment_text: str) -> Tuple[bool, str]:
        # Try selector click, then DOM-based click, then keyboard submit.
        for _ in range(2):
            clicked = self._click_primary_comment_button(page)
            try:
                if not clicked:
                    comment_btn = page.wait_for_selector(SKOOL_SELECTORS["comment_button_text"], timeout=1800)
                    comment_btn.click()
                    clicked = True
            except Exception:
                pass
            try:
                if not clicked:
                    reply_btn = page.wait_for_selector(SKOOL_SELECTORS["reply_button"], timeout=1800)
                    reply_btn.click()
                    clicked = True
            except Exception:
                pass
            if not clicked:
                clicked = self._click_send_button_js(page)

            if not clicked:
                try:
                    page.keyboard.press("Control+Enter")
                    clicked = True
                except Exception:
                    pass
            if not clicked:
                try:
                    page.keyboard.press("Enter")
                    clicked = True
                except Exception:
                    pass

            if not clicked:
                continue
            for _confirm in range(5):
                page.wait_for_timeout(700)
                if self._submit_looks_confirmed(page, comment_text):
                    return True, "sent"
        return False, "submit_not_confirmed"

    def _thread_contains_comment_snippet(self, page: Any, comment_text: str) -> bool:
        probe = " ".join(str(comment_text or "").split())
        if not probe:
            return False
        if len(probe) > 120:
            probe = probe[:120]
        try:
            return bool(
                page.evaluate(
                    """
                    ({ probe }) => {
                      const normalize = (value) => (value || "")
                        .toLowerCase()
                        .replace(/[^\\p{L}\\p{N}\\s]/gu, " ")
                        .replace(/\\s+/g, " ")
                        .trim();
                      const needle = normalize(probe);
                      if (!needle || needle.length < 6) return false;
                      const root = document.querySelector("main") || document.body;
                      if (!root) return false;
                      const editor = document.querySelector('div[contenteditable="true"]');
                      const editorText = normalize(editor ? (editor.innerText || editor.textContent || "") : "");
                      if (editorText && editorText.includes(needle)) return false;

                      const candidates = Array.from(
                        root.querySelectorAll(
                          'article, li, [class*="Comment"], [class*="comment"], [class*="Reply"], [class*="reply"], [data-testid*="comment"]'
                        )
                      );
                      for (const node of candidates) {
                        const text = normalize(node && (node.innerText || node.textContent || ""));
                        if (!text || text.length < 6) continue;
                        if (text.includes(needle)) return true;
                      }

                      // Fallback: full thread text (editor already excluded above).
                      const allText = normalize(root.innerText || "");
                      return !!(allText && allText.includes(needle));
                    }
                    """,
                    {"probe": probe},
                )
            )
        except Exception:
            return False

    def _verify_comment_published(self, page: Any, profile: Dict[str, Any], comment_text: str) -> bool:
        # Primary confirmation: snippet appears in thread and editor is cleared.
        # Fallback confirmation: profile author + snippet when thread render is slower.
        for _ in range(12):
            page.wait_for_timeout(1000)
            has_profile_comment = self._thread_has_profile_comment(page, profile)
            has_text_snippet = self._thread_contains_comment_snippet(page, comment_text)
            editor_cleared = not self._editor_contains_snippet(page, comment_text)
            if has_text_snippet and editor_cleared:
                return True
            if has_profile_comment and has_text_snippet:
                return True
        return False

    def _profile_name_candidates(self, profile: Dict[str, Any]) -> List[str]:
        candidates: List[str] = []
        for raw in (
            profile.get("name"),
            profile.get("label"),
            str(profile.get("email") or "").split("@", 1)[0],
        ):
            text = " ".join(str(raw or "").strip().replace("_", " ").replace("-", " ").split()).lower()
            if text and len(text) >= 2 and text not in candidates:
                candidates.append(text)
        return candidates

    def _thread_has_profile_comment(self, page: Any, profile: Dict[str, Any]) -> bool:
        names = self._profile_name_candidates(profile)
        if not names:
            return False
        try:
            return bool(
                page.evaluate(
                    """
                    ({ names }) => {
                      const clean = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                      const normNames = (names || []).map((n) => clean(n)).filter(Boolean);
                      if (!normNames.length) return false;
                      const root = document.querySelector("main") || document.body;
                      const blocks = Array.from(root.querySelectorAll("article, li, div"));
                      for (const block of blocks) {
                        const raw = (block && block.innerText ? block.innerText : "");
                        if (!raw) continue;
                        const text = clean(raw);
                        if (!text || text.length < 8) continue;
                        const firstLine = clean((raw.split("\\n")[0] || ""));
                        if (!firstLine) continue;
                        for (const n of normNames) {
                          if (firstLine === n || firstLine.startsWith(n + " ") || firstLine.includes(n)) {
                            return true;
                          }
                        }
                      }
                      return false;
                    }
                    """,
                    {"names": names},
                )
            )
        except Exception:
            return False

    def _normalize_url(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return url
        clean = url.strip()
        if not clean.startswith("http://") and not clean.startswith("https://"):
            clean = "https://" + clean
        try:
            parsed = urlsplit(clean)
            host = str(parsed.netloc or "").lower()
            path = str(parsed.path or "").rstrip("/")
            # Canonicalize Skool post URLs: same post can have different ?p= comment anchors.
            if "skool.com" in host and path and not path.startswith("/chat"):
                return urlunsplit((parsed.scheme or "https", parsed.netloc, path, "", ""))
            # For other URLs keep query/fragment normalized.
            query = urlencode(parse_qsl(parsed.query, keep_blank_values=True))
            return urlunsplit((parsed.scheme or "https", parsed.netloc, path, query, ""))
        except Exception:
            return clean

    def _is_post_queued_for_profile(self, profile_id: str, post_url: str) -> bool:
        canonical_post_url = self._normalize_url(post_url) or str(post_url or "").strip()
        if not canonical_post_url:
            return False
        with self._db() as db:
            rows = db.execute(
                "SELECT postId FROM queue_items WHERE profileId = ?",
                (profile_id,),
            ).fetchall()
        for row in rows:
            row_post = self._normalize_url(str(row["postId"] or ""))
            if row_post and row_post == canonical_post_url:
                return True
        return False

    def _queue_tail_datetime_for_profile(self, profile_id: str) -> datetime:
        with self._db() as db:
            row = db.execute(
                "SELECT scheduledFor FROM queue_items WHERE profileId = ? ORDER BY scheduledFor DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
        if not row:
            return datetime.now()
        raw = str(row["scheduledFor"] or "").strip()
        if not raw:
            return datetime.now()
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return datetime.now()

    def _get_openai_key(self) -> str:
        if self.config_file.exists():
            try:
                with self.config_file.open("r", encoding="utf-8") as f:
                    value = str((json.load(f) or {}).get("openai_api_key", "")).strip()
                    if value and not _looks_like_masked_secret(value):
                        return value
            except Exception:
                pass
        env = os.environ.get("OPENAI_API_KEY", "").strip()
        if env and not _looks_like_masked_secret(env):
            return env
        return ""

    def _merge_profile_from_account_file(self, profile: Dict[str, Any]) -> None:
        account_file = self.accounts_dir / profile["id"] / "account.json"
        if not account_file.exists():
            return
        try:
            with account_file.open("r", encoding="utf-8") as f:
                persisted = json.load(f)
            for key in ["email", "password", "proxy", "delay_min", "delay_max", "visits", "repliesPerVisit", "keywords", "keywordTriggeredPrompt", "generalEngagementPrompt", "communities", "enabled", "label", "runFrom", "runTo", "days"]:
                if key in persisted:
                    profile[key] = decrypt_secret(persisted[key]) if key == "password" else persisted[key]
        except Exception:
            return

    def _load_profile_for_session(self, profile_id: str) -> Optional[Dict[str, Any]]:
        profiles, _ = self._load_runtime_config_from_db()
        return next((p for p in profiles if p.get("id") == profile_id), None)


def _normalize_skool_url(url: str) -> str:
    if not url:
        return url
    if "?p=" in url:
        path = url.split("?", 1)[0].rstrip("/")
        post_id = url.split("?p=", 1)[1].split("&", 1)[0]
        return f"{path}?p={post_id}"
    return url.rstrip("/").split("#", 1)[0]


def _blacklist_text_key(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    clean = text.strip().lower()[:80]
    return f"text:{clean}" if clean else None


def _count_words(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"[^\W_]+(?:['’][^\W_]+)?", text, flags=re.UNICODE))


def _parse_proxy_to_playwright(proxy_str: Optional[str]) -> Optional[Dict[str, str]]:
    if not proxy_str:
        return None
    s = proxy_str.strip()
    if not s:
        return None
    if "://" in s:
        s = s.split("://", 1)[1]
    if "@" in s:
        auth, server = s.rsplit("@", 1)
        host, port = server.split(":", 1) if ":" in server else (server, "8080")
        username = auth.split(":", 1)[0]
        password = auth.split(":", 1)[1] if ":" in auth else ""
        return {"server": f"http://{host}:{port}", "username": username, "password": password}
    host, port = s.split(":", 1) if ":" in s else (s, "8080")
    return {"server": f"http://{host}:{port}"}


def _normalize_proxy_url(proxy_str: str) -> str:
    s = (proxy_str or "").strip()
    if not s:
        return s
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"http://{s}"


def _looks_like_masked_secret(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if "*" in text:
        return True
    if "..." in text or "…" in text:
        return True
    return False


def _format_display_time(dt: datetime) -> str:
    hour24 = dt.hour
    minute = dt.minute
    hour12 = hour24 % 12 or 12
    suffix = "PM" if hour24 >= 12 else "AM"
    return f"{hour12:02d}:{minute:02d} {suffix}"


def _humanize_login_check_message(status: str, raw_message: str) -> str:
    normalized_status = (status or "").strip().lower()
    normalized_message = (raw_message or "").strip().lower()

    if normalized_status == "ready":
        return "Login check passed: session is active"
    if normalized_status == "invalid_credentials":
        if "missing email or password" in normalized_message:
            return "Login check failed: credentials are missing"
        return "Login check failed: invalid credentials"
    if normalized_status == "captcha":
        return "Login check failed: captcha required"
    if normalized_status == "proxy_error":
        return "Login check retry: proxy connection issue"
    if normalized_status == "network_error":
        if "timeout" in normalized_message:
            return "Login check retry: network timeout"
        return "Login check retry: network issue"

    return f"Login check: {normalized_status or 'unknown status'}"


def _humanize_proxy_check_message(status: str, raw_message: str) -> str:
    normalized_status = (status or "").strip().lower()
    normalized_message = (raw_message or "").strip().lower()

    if normalized_status == "connected":
        return "Proxy check passed: connection is active"
    if normalized_status == "no_proxy":
        return "Proxy check skipped: no proxy configured"
    if normalized_status == "proxy_error":
        return "Proxy check retry: proxy connection issue"
    if normalized_status == "network_error":
        if "timed out" in normalized_message or "timeout" in normalized_message:
            return "Proxy check retry: network timeout"
        return "Proxy check retry: network issue"

    return f"Proxy check: {normalized_status or 'unknown status'}"


def _session_check_log_status(success: bool, result_status: str) -> str:
    if success:
        return "success"
    normalized = (result_status or "").strip().lower()
    if normalized in {"network_error", "proxy_error"}:
        return "retry"
    return "error"


def _seconds_until_next_run(run_from: str, days_of_week: Dict[str, bool]) -> int:
    now = datetime.now()
    from_hour, from_min = map(int, run_from.split(":"))
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for days_ahead in range(0, 8):
        candidate = now + timedelta(days=days_ahead)
        day_name = day_names[candidate.weekday()]
        if days_of_week and not days_of_week.get(day_name, True):
            continue
        target = candidate.replace(hour=from_hour, minute=from_min, second=0, microsecond=0)
        if target <= now:
            continue
        return int((target - now).total_seconds())
    return 86400


def _extract_task_ref_from_post_url(post_url: str) -> str:
    raw = str(post_url or "").strip()
    if not raw:
        return ""
    m = re.search(r"[?&]p=([^&#]+)", raw, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"/([^/?#]+)$", raw)
    if m:
        return m.group(1).strip()
    return raw[:32]


def _openai_generate_comment_rest(api_key: str, prompt: str, post_text: str) -> str:
    if not api_key:
        raise RuntimeError("OpenAI API key missing")
    payload = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo"),
        "messages": [
            {"role": "system", "content": prompt or "Write a short helpful comment under 40 words."},
            {"role": "user", "content": f"Post:\n{post_text}\n\nWrite a single comment reply."},
        ],
        "max_tokens": 120,
        "temperature": 0.7,
    }
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI returned no choices")
    content = (choices[0].get("message") or {}).get("content", "")
    return content.strip()
