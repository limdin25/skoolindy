
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from security_utils import decrypt_secret, encrypt_secret, is_encrypted_secret, mask_secret
from proxy_slots import acquire_proxy_slot, release_proxy_slot


def _load_local_env_file() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            os.environ[key] = value.strip()
    except Exception:
        return


_load_local_env_file()

from automation.engine import AutomationEngine

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PlaywrightError = Exception
    PlaywrightTimeoutError = TimeoutError
    PLAYWRIGHT_AVAILABLE = False

DB_PATH = Path(__file__).parent / "engageflow.db"
LOGGER = logging.getLogger("engageflow")
LOG_LEVEL = str(os.environ.get("ENGAGEFLOW_LOG_LEVEL", "INFO")).strip().upper() or "INFO"
LOG_RETENTION_DAYS = max(1, int(os.environ.get("ENGAGEFLOW_LOG_RETENTION_DAYS", "14")))
LOG_DIR = Path(os.environ.get("ENGAGEFLOW_LOG_DIR", str(Path(__file__).parent / "logs")))
SKOOL_CHAT_IMPORT_PREFIX = "skool-chat-"
SKOOL_CHAT_IMPORT_MESSAGE_PREFIX = "skool-msg-"
SKOOL_CHAT_BACKGROUND_SYNC_ENABLED = str(os.environ.get("SKOOL_CHAT_BACKGROUND_SYNC_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
SKOOL_CHAT_STRICT_IDENTITY_CHECK = str(os.environ.get("SKOOL_CHAT_STRICT_IDENTITY_CHECK", "1")).strip().lower() in {"1", "true", "yes", "on"}
SKOOL_CHAT_SYNC_TTL_SECONDS = max(120, int(os.environ.get("SKOOL_CHAT_SYNC_TTL_SECONDS", "180")))
SKOOL_CHAT_BACKGROUND_SYNC_INITIAL_DELAY_SECONDS = max(15, int(os.environ.get("SKOOL_CHAT_BACKGROUND_SYNC_INITIAL_DELAY_SECONDS", "60")))
SKOOL_CHAT_BACKGROUND_SYNC_INTERVAL_SECONDS = max(60, int(os.environ.get("SKOOL_CHAT_BACKGROUND_SYNC_INTERVAL_SECONDS", "180")))
SKOOL_CHAT_SYNC_WHILE_AUTOMATION_RUNNING = str(
    os.environ.get("SKOOL_CHAT_SYNC_WHILE_AUTOMATION_RUNNING", "1")
).strip().lower() in {"1", "true", "yes", "on"}
SKOOL_CHAT_NAV_TIMEOUT_MS = max(12000, int(os.environ.get("SKOOL_CHAT_NAV_TIMEOUT_MS", "35000")))
SKOOL_CHAT_MAX_CHATS_PER_PROFILE = max(20, int(os.environ.get("SKOOL_CHAT_MAX_CHATS_PER_PROFILE", "150")))
SKOOL_CHAT_PROFILES_PER_SYNC = max(1, int(os.environ.get("SKOOL_CHAT_PROFILES_PER_SYNC", "1")))
SKOOL_CHAT_PROFILE_FETCH_DELAY_SECONDS = max(0.0, float(os.environ.get("SKOOL_CHAT_PROFILE_FETCH_DELAY_SECONDS", "3.0")))
SKOOL_CHAT_PROFILE_RETRY_ATTEMPTS = max(1, int(os.environ.get("SKOOL_CHAT_PROFILE_RETRY_ATTEMPTS", "3")))
SKOOL_CHAT_MAX_MESSAGES_PER_CHAT = max(200, int(os.environ.get("SKOOL_CHAT_MAX_MESSAGES_PER_CHAT", "2000")))
SKOOL_CHAT_MESSAGE_WINDOW = max(1, int(os.environ.get("SKOOL_CHAT_MESSAGE_WINDOW", "35")))
SKOOL_CHAT_MESSAGE_PAGE_FETCH_LIMIT = max(4, int(os.environ.get("SKOOL_CHAT_MESSAGE_PAGE_FETCH_LIMIT", "80")))
SKOOL_CHAT_PROFILE_SYNC_TIMEOUT_SECONDS = max(30, int(os.environ.get("SKOOL_CHAT_PROFILE_SYNC_TIMEOUT_SECONDS", "180")))
SKOOL_CHAT_DETAIL_FETCH_LIMIT = max(0, int(os.environ.get("SKOOL_CHAT_DETAIL_FETCH_LIMIT", "0")))
_SKOOL_CHAT_IMPORT_CACHE: Dict[str, Any] = {
    "path": None,
    "mtime_ns": None,
    "size": None,
    "live_synced_at": 0.0,
    "live_cards": [],
    "stale_miss_counts": {},
    "profile_cursor": 0,
    "dm_log_last": {},
}
_SKOOL_CHAT_SYNC_LOCK = threading.Lock()
_PLAYWRIGHT_SYNC_LOCK = threading.Lock()
_SKOOL_DM_SEND_DEDUPE_LOCK = threading.Lock()
_SKOOL_DM_SEND_DEDUPE: Dict[str, float] = {}
_PROXY_STATUS_CACHE: Dict[str, Dict[str, str]] = {}
_COMMUNITY_FETCH_LOCK = threading.Lock()
_COMMUNITY_FETCH_STATE_LOCK = threading.Lock()
_COMMUNITY_FETCH_STATE: Dict[str, Any] = {
    "running": False,
    "startedAt": "",
    "finishedAt": "",
    "profilesTotal": 0,
    "profilesDone": 0,
    "currentProfileId": "",
    "currentProfileName": "",
    "lastError": "",
    "lastResult": None,
}
_DAILY_COUNTERS_RESET_LOCK = threading.Lock()
DB_WRITE_RETRY_ATTEMPTS = 8
DB_WRITE_RETRY_SLEEP_SECONDS = 0.08
APP_BOOT_TS = time.time()
PROFILE_LOGIN_MONITOR_ENABLED = str(os.environ.get("PROFILE_LOGIN_MONITOR_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
PROFILE_LOGIN_MONITOR_INTERVAL_SECONDS = max(1800, int(os.environ.get("PROFILE_LOGIN_MONITOR_INTERVAL_SECONDS", "7200")))
DETAILED_TRACE_LOGS_ENABLED = str(os.environ.get("DETAILED_TRACE_LOGS_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
SKOOL_ACCOUNTS_CLEANUP_ENABLED = str(os.environ.get("SKOOL_ACCOUNTS_CLEANUP_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
SKOOL_ACCOUNTS_PRUNE_ORPHANS_ENABLED = str(os.environ.get("SKOOL_ACCOUNTS_PRUNE_ORPHANS_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
SKOOL_ACCOUNTS_PRUNE_TRANSIENT_CACHE_ENABLED = str(os.environ.get("SKOOL_ACCOUNTS_PRUNE_TRANSIENT_CACHE_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
SKOOL_TRANSIENT_CACHE_RELATIVE_DIRS = (
    "browser/Default/Code Cache",
    "browser/Default/GPUCache",
    "browser/Default/GrShaderCache",
    "browser/Default/GraphiteDawnCache",
    "browser/Default/DawnGraphiteCache",
    "browser/Default/DawnWebGPUCache",
)


# Configure app and uvicorn logging with daily file rotation and retention.
def _setup_application_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = TimedRotatingFileHandler(
        filename=str(LOG_DIR / "engageflow.log"),
        when="midnight",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
        utc=False,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Avoid duplicate handlers on hot reload / repeated imports.
    LOGGER.handlers.clear()
    LOGGER.setLevel(level)
    LOGGER.propagate = False
    LOGGER.addHandler(console_handler)
    LOGGER.addHandler(file_handler)

    # Keep uvicorn logs consistent with application logs.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.setLevel(level)
        uvicorn_logger.propagate = False
        uvicorn_logger.addHandler(console_handler)
        uvicorn_logger.addHandler(file_handler)


_setup_application_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.automation_engine = AutomationEngine(DB_PATH, Path(__file__).parent)
    await app.state.automation_engine.recover_after_restart()
    global _AUTOMATION_ENGINE_REF
    _AUTOMATION_ENGINE_REF = app.state.automation_engine
    app.state.automation_engine.set_ensure_profile_auth(ensure_profile_auth)
    app.state.profile_login_monitor_task = asyncio.create_task(
        _profile_login_monitor_loop(app),
        name="profile-login-monitor",
    )
    app.state.skool_chat_sync_task = None
    if SKOOL_CHAT_BACKGROUND_SYNC_ENABLED:
        app.state.skool_chat_sync_task = asyncio.create_task(
            _skool_chat_sync_loop(),
            name="skool-chat-sync",
        )
    try:
        yield
    finally:
        chat_sync_task = getattr(app.state, "skool_chat_sync_task", None)
        if chat_sync_task:
            chat_sync_task.cancel()
            try:
                await chat_sync_task
            except asyncio.CancelledError:
                pass
        monitor_task = getattr(app.state, "profile_login_monitor_task", None)
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        try:
            await app.state.automation_engine.shutdown(preserve_run_state=True)
        except Exception:
            pass


app = FastAPI(title="EngageFlow Backend", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
# Log each request with status code and latency for diagnostics.
async def request_logging_middleware(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        LOGGER.exception(
            "HTTP %s %s -> 500 (%.1fms)",
            request.method,
            request.url.path,
            elapsed_ms,
        )
        raise

    elapsed_ms = (time.perf_counter() - started) * 1000
    status_code = int(getattr(response, "status_code", 0) or 0)
    log_fn = LOGGER.warning if status_code >= 400 else LOGGER.info
    log_fn("HTTP %s %s -> %s (%.1fms)", request.method, request.url.path, status_code, elapsed_ms)
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": _machine_code(message, exc.status_code),
            "message": message,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(part) for part in first.get("loc", []) if part != "body")
    msg = first.get("msg", "Invalid request payload")
    message = f"{loc}: {msg}" if loc else str(msg)
    return JSONResponse(
        status_code=400,
        content={"success": False, "error": "validation_error", "message": message},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    LOGGER.exception("Unhandled backend exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "internal_error",
            "message": "Internal server error",
        },
    )


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = NORMAL")
    except Exception:
        pass
    try:
        yield conn
    finally:
        conn.close()


def get_automation_engine(request: Request) -> AutomationEngine:
    return request.app.state.automation_engine

_AUTOMATION_ENGINE_REF: Optional[AutomationEngine] = None

SKOOL_AUTH_CHECK_URL = "https://api2.skool.com/self/groups?offset=0&limit=1&prefs=false&members=true"


def ensure_profile_auth(profile_id: str) -> dict:
    """Shared auth gate. Returns {ok, cookie_json, status, message, refresh_attempted, refresh_result}.
    Used by fetch, join, comment flows. One retry on 401/403."""
    import urllib.request
    import urllib.error
    result = {"ok": False, "cookie_json": None, "status": "unknown", "message": "", "refresh_attempted": False, "refresh_result": None}
    with get_db() as db:
        row = db.execute("SELECT id, cookie_json, email, password FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if not row:
            result["status"] = "profile_not_found"
            result["message"] = "Profile not found"
            return result
        cookie_json = (row["cookie_json"] or "").strip() if row["cookie_json"] else ""
        if not cookie_json:
            result["status"] = "login_required"
            result["message"] = "No cookies / not logged in"
            return result
    cookies = _cookies_from_json(cookie_json)
    if not cookies:
        result["status"] = "login_required"
        result["message"] = "No cookies / not logged in"
        return result
    headers = {
        "Cookie": cookies,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://www.skool.com",
        "Referer": "https://www.skool.com/",
    }
    req = urllib.request.Request(SKOOL_AUTH_CHECK_URL, headers=headers, method="GET")
    got_401_403 = False
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            if 200 <= resp.status < 300:
                result["ok"] = True
                result["status"] = "ok"
                result["cookie_json"] = cookie_json
                result["message"] = "Auth valid"
                return result
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403):
            result["status"] = "api_error"
            result["message"] = f"Skool API HTTP {e.code}"
            return result
        got_401_403 = True
    except Exception as exc:
        err = str(exc).lower()
        if "401" in err or "403" in err:
            got_401_403 = True
        else:
            result["status"] = "network_error"
            result["message"] = str(exc)[:200]
            return result
    if not got_401_403:
        return result
    LOGGER.info("[AUTH] profile=%s Skool API 401/403, attempting refresh", profile_id)
    result["refresh_attempted"] = True
    engine = _AUTOMATION_ENGINE_REF
    if not engine:
        result["status"] = "login_required"
        result["message"] = "Cookies invalid/expired; refresh unavailable"
        return result
    refresh = engine.run_profile_login_refresh_sync(profile_id)
    LOGGER.info("[AUTH] profile=%s refresh_attempted=true refresh_result=%s", profile_id, refresh.get("status", "failed"))
    result["refresh_result"] = refresh.get("status", "failed")
    if refresh.get("success") and refresh.get("cookie_json"):
        new_cookies = _cookies_from_json(refresh["cookie_json"])
        if new_cookies:
            req2 = urllib.request.Request(SKOOL_AUTH_CHECK_URL, headers={**headers, "Cookie": new_cookies}, method="GET")
            try:
                with urllib.request.urlopen(req2, timeout=12) as resp2:
                    if 200 <= resp2.status < 300:
                        result["ok"] = True
                        result["status"] = "ok"
                        result["cookie_json"] = refresh["cookie_json"]
                        result["message"] = "Auth refreshed"
                        return result
            except Exception:
                pass
    if refresh.get("status") == "captcha":
        result["status"] = "captcha"
        result["message"] = refresh.get("message", "Captcha required")
        return result
    if refresh.get("status") == "network_error" and "blocked" in (refresh.get("message") or "").lower():
        result["status"] = "blocked"
        result["message"] = refresh.get("message", "Account blocked")
        return result
    result["status"] = "login_required"
    result["message"] = refresh.get("message", "Login required") or "Cookies invalid/expired"
    return result



def _normalize_log_message(message: str, max_len: int = 1000) -> str:
    text = str(message or "").replace("\x00", "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len]


def _is_trace_log_message(message: str) -> bool:
    text = str(message or "").strip()
    return text.startswith("[SKOOL][TRACE]") or text.startswith("[TRACE]")


def _db_execute_with_retry(
    db: sqlite3.Connection,
    query: str,
    params: Sequence[Any] = (),
    *,
    attempts: int = DB_WRITE_RETRY_ATTEMPTS,
    sleep_seconds: float = DB_WRITE_RETRY_SLEEP_SECONDS,
) -> None:
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            db.execute(query, tuple(params))
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_exc = exc
            if attempt == attempts - 1:
                break
            time.sleep(sleep_seconds * (attempt + 1))
    if last_exc:
        raise last_exc


def _db_commit_with_retry(
    db: sqlite3.Connection,
    *,
    attempts: int = DB_WRITE_RETRY_ATTEMPTS,
    sleep_seconds: float = DB_WRITE_RETRY_SLEEP_SECONDS,
) -> None:
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            db.commit()
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_exc = exc
            if attempt == attempts - 1:
                break
            time.sleep(sleep_seconds * (attempt + 1))
    if last_exc:
        raise last_exc


def _reset_daily_counters_if_needed_for_api() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    state_path = Path(__file__).parent / "skool_daily_counters_state.json"

    with _DAILY_COUNTERS_RESET_LOCK:
        state: Dict[str, Any] = {}
        if state_path.exists():
            try:
                with state_path.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        state = loaded
            except Exception:
                state = {}

        if str(state.get("last_reset_date") or "") == today:
            return

        with get_db() as db:
            _db_execute_with_retry(db, "UPDATE profiles SET dailyUsage = 0")
            _db_execute_with_retry(db, "UPDATE communities SET actionsToday = 0, matchesToday = 0")
            _db_commit_with_retry(db)

        try:
            with state_path.open("w", encoding="utf-8") as f:
                json.dump({"last_reset_date": today}, f)
        except Exception:
            LOGGER.exception("Failed to persist daily counters reset state")


def _infer_log_module_action(message: str) -> Tuple[str, str]:
    text = str(message or "").strip()
    lower = text.lower()

    if any(token in lower for token in ("openai", "api key", "ai auto")):
        if "set" in lower or "save" in lower or "update" in lower:
            return "openai", "set_key"
        if "test" in lower or "check" in lower or "connection" in lower:
            return "openai", "check_key"
        if "generate" in lower or "reply" in lower:
            return "openai", "generate"
        return "openai", "event"

    if "proxy" in lower:
        if "cached" in lower or "cache" in lower:
            return "proxy", "cache"
        if "check" in lower or "passed" in lower or "retry" in lower or "failed" in lower:
            return "proxy", "check"
        return "proxy", "event"

    if any(token in lower for token in ("chat", "inbox sync", "dm send", "dm ", "conversation")):
        if "started" in lower:
            return "chats", "start"
        if "retry" in lower or "requeue" in lower:
            return "chats", "retry"
        if "failed" in lower or "error" in lower:
            return "chats", "fail"
        if "imported" in lower or "complete" in lower or "updated" in lower:
            return "chats", "sync"
        if "send" in lower:
            return "chats", "send"
        if "fetch" in lower or "load" in lower:
            return "chats", "fetch"
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
        if "start" in lower or "running" in lower or "execute" in lower:
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

    return "system", "event"


def _insert_backend_log(
    db: sqlite3.Connection,
    profile: str,
    status: Literal["success", "retry", "error", "info"],
    message: str,
    module: Optional[str] = None,
    action: Optional[str] = None,
) -> None:
    normalized_message = _normalize_log_message(message)
    inferred_module, inferred_action = _infer_log_module_action(normalized_message)
    module_value = str(module or inferred_module or "system").strip().lower() or "system"
    action_value = str(action or inferred_action or "event").strip().lower() or "event"
    try:
        try:
            _db_execute_with_retry(
                db,
                "INSERT INTO logs (id, timestamp, profile, status, module, action, message, fallbackLevelUsed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), now_display_time(), profile, status, module_value, action_value, normalized_message, None),
            )
        except sqlite3.OperationalError as exc:
            if "no column named module" not in str(exc).lower() and "no column named action" not in str(exc).lower():
                raise
            _db_execute_with_retry(
                db,
                "INSERT INTO logs (id, timestamp, profile, status, message, fallbackLevelUsed) VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), now_display_time(), profile, status, normalized_message, None),
            )
        # Commit immediately to avoid holding a write transaction across long Playwright sync steps.
        _db_commit_with_retry(db)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            LOGGER.warning("Skipped log write due to sqlite lock: profile=%s status=%s", profile, status)
            return
        raise


def _emit_dm_sync_log_once(
    db: sqlite3.Connection,
    profile_id: str,
    profile_name: str,
    status: Literal["success", "retry", "error", "info"],
    dedupe_key: str,
    message: str,
    cooldown_sec: int = 180,
) -> None:
    cache = _SKOOL_CHAT_IMPORT_CACHE.setdefault("dm_log_last", {})
    cache_key = f"{profile_id}:{status}:{dedupe_key}"
    now_ts = time.time()
    last_ts = float(cache.get(cache_key) or 0.0)
    if (now_ts - last_ts) < cooldown_sec:
        return
    cache[cache_key] = now_ts
    _insert_backend_log(db, profile_name or profile_id or "SYSTEM", status, message)


def _humanize_dm_sync_error(raw_error: str) -> str:
    text = str(raw_error or "").strip()
    lower = text.lower()
    if not text:
        return "Unknown sync error."
    if lower.startswith("live dm sync failed:"):
        text = text.split(":", 1)[1].strip()
        lower = text.lower()
    if "_playwright" in lower and "playwrightcontextmanager" in lower:
        return "Browser driver startup glitch (Playwright internal error). Will retry automatically."
    if "connection closed while reading from the driver" in lower:
        return "Browser driver connection was interrupted while reading chats."
    if "target page, context or browser has been closed" in lower:
        return "Browser context closed during chat sync."
    if "skool navigation aborted" in lower or "browser/context interrupted" in lower:
        return "Chat sync was interrupted while navigating Skool."
    if "timeout" in lower and ("skool.com/chat" in lower or "skool.com/" in lower):
        return "Timed out while opening Skool page. Will retry automatically."
    if "could not discover chat links on /chat page" in lower:
        return "Opened Skool, but chat list was not detected on /chat page. Will retry automatically."
    if "could not discover chat links" in lower:
        return "Opened Skool, but chat links were not detected in this cycle. Will retry automatically."
    if "chat dropdown opened, but no chat links were found" in lower:
        return "Opened Skool, but chat list was not detected on /chat page. Will retry automatically."
    if "profile is not logged in to skool" in lower or "login page detected during dm sync" in lower:
        return "Profile session looks logged out during chat sync. Re-login may be required."
    if "not logged in to skool" in lower:
        return "Profile is not logged in to Skool for chat sync."
    return text


def _is_transient_dm_sync_error(raw_error: str) -> bool:
    lower = str(raw_error or "").strip().lower()
    if not lower:
        return False
    transient_markers = [
        "transient/network issue",
        "timeout",
        "timed out",
        "could not discover chat links on /chat page",
        "could not discover chat links",
        "chat dropdown opened, but no chat links were found",
        "skool navigation aborted",
        "browser/context interrupted",
        "target page, context or browser has been closed",
        "connection closed while reading from the driver",
        "network issue",
        "network error",
    ]
    return any(marker in lower for marker in transient_markers)


async def _run_profile_login_checks(app: FastAPI) -> None:
    # Avoid concurrent browser-session checks while DM sync is running.
    if _SKOOL_CHAT_SYNC_LOCK.locked():
        return
    engine = app.state.automation_engine
    try:
        status = await engine.get_status()
        if bool((status or {}).get("isRunning")):
            return
    except Exception:
        return
    with get_db() as db:
        rows = db.execute("SELECT id FROM profiles ORDER BY name").fetchall()
    for row in rows:
        if _SKOOL_CHAT_SYNC_LOCK.locked():
            return
        profile_id = row["id"]
        try:
            await engine.check_login(profile_id)
        except RuntimeError as exc:
            if "not found" in str(exc).lower():
                continue
            LOGGER.exception("Periodic login check failed for profile '%s'", profile_id)
        except Exception:
            LOGGER.exception("Periodic login check failed for profile '%s'", profile_id)


async def _profile_login_monitor_loop(app: FastAPI) -> None:
    if not PROFILE_LOGIN_MONITOR_ENABLED:
        return
    while True:
        try:
            await _run_profile_login_checks(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Periodic profile login monitor failed")
        await asyncio.sleep(PROFILE_LOGIN_MONITOR_INTERVAL_SECONDS)


async def _run_skool_chat_sync_once(force: bool = True) -> None:
    def _sync() -> None:
        with get_db() as db:
            _sync_skool_chats_to_inbox(db, force=force)
    await asyncio.to_thread(_sync)


async def _skool_chat_sync_loop() -> None:
    await asyncio.sleep(SKOOL_CHAT_BACKGROUND_SYNC_INITIAL_DELAY_SECONDS)
    while True:
        cycle_started_at = time.time()
        try:
            if not SKOOL_CHAT_SYNC_WHILE_AUTOMATION_RUNNING:
                try:
                    status = await app.state.automation_engine.get_status()
                except Exception:
                    status = {}
                # Optional conservative mode: avoid parallel browser traffic while scheduler is active.
                if bool((status or {}).get("isRunning")) and not bool((status or {}).get("isPaused")):
                    await asyncio.sleep(max(5.0, float(SKOOL_CHAT_BACKGROUND_SYNC_INTERVAL_SECONDS) / 2.0))
                    continue
            await _run_skool_chat_sync_once(force=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Background Skool chat sync failed")
        elapsed = max(0.0, time.time() - cycle_started_at)
        sleep_for = max(2.0, float(SKOOL_CHAT_BACKGROUND_SYNC_INTERVAL_SECONDS) - elapsed)
        await asyncio.sleep(sleep_for)


def now_display_time() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _format_queue_display_time(dt: datetime) -> str:
    hour = dt.hour
    minute = dt.minute
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12:02d}:{minute:02d} {suffix}"


def _parse_queue_scheduled_for(raw_value: str) -> datetime:
    raw = str(raw_value or "").strip()
    if not raw:
        raise ValueError("scheduledFor is required")
    normalized = raw.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _format_queue_scheduled_for_api(raw_value: str) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return raw
    try:
        naive_local_dt = _parse_queue_scheduled_for(raw)
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            return naive_local_dt.isoformat(timespec="seconds")
        return naive_local_dt.replace(tzinfo=local_tz).isoformat(timespec="seconds")
    except Exception:
        return raw


def _queue_row_to_api_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(row)
    payload["scheduledFor"] = _format_queue_scheduled_for_api(str(payload.get("scheduledFor") or ""))
    return payload


def _default_error_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        404: "not_found",
        500: "internal_error",
    }.get(status_code, "request_error")


def _machine_code(detail: Any, status_code: int) -> str:
    default = _default_error_code(status_code)
    if not isinstance(detail, str):
        return default
    compact = "".join(ch.lower() if ch.isalnum() else "_" for ch in detail).strip("_")
    while "__" in compact:
        compact = compact.replace("__", "_")
    if not compact or len(compact) > 80:
        return default
    return compact


def parse_json_field(value: str, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _abs_skool_url(path_or_url: str) -> str:
    value = str(path_or_url or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/"):
        return f"https://www.skool.com{value}"
    return f"https://www.skool.com/{value.lstrip('/')}"


def _normalize_proxy_key(proxy_value: Optional[str]) -> str:
    return re.sub(r"\s+", "", str(proxy_value or "").strip().lower())


def _save_proxy_cache_entry(proxy_key: str, status: str, message: str) -> None:
    key = str(proxy_key or "").strip()
    if not key:
        return
    checked_at = now_display_time()
    with get_db() as db:
        _db_execute_with_retry(
            db,
            """
            INSERT INTO proxy_status_cache (proxyKey, status, message, checkedAt)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(proxyKey) DO UPDATE SET
                status = excluded.status,
                message = excluded.message,
                checkedAt = excluded.checkedAt
            """,
            (key, str(status or "").strip(), str(message or "").strip(), checked_at),
        )
        _db_commit_with_retry(db)
    _PROXY_STATUS_CACHE[key] = {
        "status": str(status or "").strip(),
        "message": str(message or "").strip(),
        "checkedAt": checked_at,
    }


def _load_proxy_cache_from_db(db: sqlite3.Connection) -> None:
    rows = db.execute(
        "SELECT proxyKey, status, message, checkedAt FROM proxy_status_cache"
    ).fetchall()
    cache: Dict[str, Dict[str, str]] = {}
    for row in rows:
        key = str(row["proxyKey"] or "").strip()
        if not key:
            continue
        cache[key] = {
            "status": str(row["status"] or "").strip(),
            "message": str(row["message"] or "").strip(),
            "checkedAt": str(row["checkedAt"] or "").strip(),
        }
    _PROXY_STATUS_CACHE.clear()
    _PROXY_STATUS_CACHE.update(cache)


def _normalize_origin_group_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text).lower()
    if compact in {"inbox0", "skoolinbox0"}:
        return "Skool Inbox"
    return text


def _slugify_profile_identity(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.split("@", 1)[0]
    text = re.sub(r"[^a-z0-9._-]+", "", text)
    return text


def _extract_logged_in_profile_slug(page: Any) -> str:
    dom_slug = ""
    try:
        slug = page.evaluate(
            """
            () => {
              const pickSlug = (href) => {
                const raw = String(href || "");
                if (!raw.startsWith("/@")) return "";
                const rest = raw.slice(2);
                return (rest.split(/[/?#]/)[0] || "").trim();
              };

              // Only trust account/nav areas first; feed/chat content can contain other users.
              const prioritizedSelectors = [
                "header a[href^='/@']",
                "nav a[href^='/@']",
                "[class*='TopNav'] a[href^='/@']",
                "[class*='top-nav'] a[href^='/@']",
                "[data-testid*='top'] a[href^='/@']",
                "[aria-label*='account' i] a[href^='/@']",
              ];
              for (const selector of prioritizedSelectors) {
                const links = Array.from(document.querySelectorAll(selector));
                for (const link of links) {
                  const clean = pickSlug(link.getAttribute("href"));
                  if (clean) return clean;
                }
              }
              return "";
            }
            """
        )
        dom_slug = _slugify_profile_identity(str(slug or ""))
    except Exception:
        dom_slug = ""
    if dom_slug:
        return dom_slug

    try:
        api_slug = page.evaluate(
            """
            async () => {
              const isSlug = (value) => /^[a-z0-9._-]{3,}$/i.test(String(value || "").trim());
              const pick = (obj, depth = 0) => {
                if (!obj || typeof obj !== "object" || depth > 3) return "";
                const direct = [obj.username, obj.slug, obj.handle];
                for (const value of direct) {
                  const text = String(value || "").trim();
                  if (isSlug(text)) return text;
                }
                const nestedKeys = [
                  "user",
                  "member",
                  "profile",
                  "self",
                  "currentUser",
                  "current_user",
                  "account",
                  "data",
                ];
                for (const key of nestedKeys) {
                  if (obj[key]) {
                    const nested = pick(obj[key], depth + 1);
                    if (nested) return nested;
                  }
                }
                return "";
              };
              const fetchJsonWithTimeout = async (url, timeoutMs = 8000) => {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), timeoutMs);
                try {
                  const response = await fetch(url, {
                    method: "GET",
                    credentials: "include",
                    headers: { "accept": "*/*" },
                    signal: controller.signal,
                  });
                  if (!response.ok) return null;
                  return await response.json().catch(() => null);
                } finally {
                  clearTimeout(timer);
                }
              };
              const endpoints = [
                "https://api2.skool.com/self",
                "https://api2.skool.com/self/member",
                "https://api2.skool.com/self/profile",
                "https://api2.skool.com/self/account",
              ];
              for (const url of endpoints) {
                try {
                  const payload = await fetchJsonWithTimeout(url, 8000);
                  if (!payload) continue;
                  const slug = pick(payload);
                  if (slug) return slug;
                } catch (e) {}
              }
              return "";
            }
            """
        )
        return _slugify_profile_identity(str(api_slug or ""))
    except Exception:
        return ""


def _pick_keyword_context(
    db: sqlite3.Connection,
    profile_id: str,
    origin_group: str,
    message_text: str,
    messages: List[Dict[str, Any]],
) -> Dict[str, str]:
    rows = db.execute(
        "SELECT keyword, persona, promptPreview, assignedProfileIds, active FROM keyword_rules WHERE active = 1"
    ).fetchall()
    if not rows:
        return {
            "keyword": "—",
            "persona": "",
            "promptPreview": "—",
            "isFallback": True,
        }

    searchable_messages = " ".join(str(item.get("text") or "") for item in messages[-12:])
    haystack = f"{origin_group} {message_text} {searchable_messages}".lower()
    selected: Optional[sqlite3.Row] = None
    selected_score = -1

    for row in rows:
        assigned_raw = parse_json_field(str(row["assignedProfileIds"] or "[]"), [])
        assigned_ids = {str(item).strip() for item in assigned_raw if str(item).strip()}
        if assigned_ids and profile_id not in assigned_ids:
            continue

        keyword = str(row["keyword"] or "").strip()
        if not keyword:
            continue

        keyword_l = keyword.lower()
        tokens = [token for token in re.findall(r"[a-z0-9]+", keyword_l) if len(token) >= 3]
        score = 0
        if keyword_l in haystack:
            score += 5
        score += sum(1 for token in tokens if token in haystack)
        if score > selected_score:
            selected = row
            selected_score = score

    chosen = selected if selected is not None and selected_score > 0 else None
    if chosen is None:
        return {
            "keyword": "—",
            "persona": "",
            "promptPreview": "—",
            "isFallback": True,
        }

    return {
        "keyword": str(chosen["keyword"] or "—").strip() or "—",
        "persona": str(chosen["persona"] or "").strip(),
        "promptPreview": str(chosen["promptPreview"] or "—").strip() or "—",
        "isFallback": False,
    }


def _start_playwright_safe(max_attempts: int = 3):
    last_error: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            return sync_playwright().start()
        except AttributeError as exc:
            # Intermittent Playwright bug in some envs: context manager may miss internal _playwright field.
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


def bool_to_int(value: bool) -> int:
    return 1 if value else 0


def _clean_html_text(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("РІР‚Сћ", "•")
    return text


def _parse_chat_datetime(value: str) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("•", "").strip()
    normalized = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(\d)(am|pm)\b", r"\1 \2", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    formats = [
        "%b %d %Y %I:%M %p",
        "%b %d %Y %I:%M%p",
        "%B %d %Y %I:%M %p",
        "%B %d %Y %I:%M%p",
        "%b %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(normalized, fmt)
        except Exception:
            continue
    try:
        iso_value = normalized.replace("Z", "+00:00")
        return datetime.fromisoformat(iso_value)
    except Exception:
        return None


def _safe_datetime_timestamp(value: Optional[datetime]) -> float:
    if not value:
        return 0.0
    try:
        return float(value.timestamp())
    except (OSError, OverflowError, ValueError):
        return 0.0
    except Exception:
        return 0.0


def _reserve_skool_send_dedupe(conversation_id: str, text: str, ttl_seconds: float = 45.0) -> bool:
    key = f"{str(conversation_id or '').strip()}|{str(text or '').strip()}"
    if not key.strip("|"):
        return True
    now_mono = time.monotonic()
    with _SKOOL_DM_SEND_DEDUPE_LOCK:
        # lightweight cleanup
        stale_keys = [k for k, ts in _SKOOL_DM_SEND_DEDUPE.items() if now_mono - float(ts) > max(30.0, ttl_seconds * 4)]
        for k in stale_keys:
            _SKOOL_DM_SEND_DEDUPE.pop(k, None)
        last = _SKOOL_DM_SEND_DEDUPE.get(key)
        if last is not None and (now_mono - float(last)) <= ttl_seconds:
            return False
        _SKOOL_DM_SEND_DEDUPE[key] = now_mono
    return True


def _release_skool_send_dedupe(conversation_id: str, text: str) -> None:
    key = f"{str(conversation_id or '').strip()}|{str(text or '').strip()}"
    if not key.strip("|"):
        return
    with _SKOOL_DM_SEND_DEDUPE_LOCK:
        _SKOOL_DM_SEND_DEDUPE.pop(key, None)


def _format_first_interaction_date(value: str) -> str:
    dt = _parse_chat_datetime(value)
    if not dt:
        return ""
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _read_skool_chat_file(path: Path) -> str:
    for encoding in ("utf-8", "cp1251", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except Exception:
            continue
    return ""


def _find_skool_chat_source() -> Optional[Path]:
    # File-based chat import is disabled. Live profile parsing only.
    return None


def _parse_proxy_to_playwright(proxy_str: Optional[str]) -> Optional[Dict[str, str]]:
    if not proxy_str:
        return None
    raw = proxy_str.strip()
    if not raw:
        return None
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    if "@" in raw:
        auth, host_part = raw.rsplit("@", 1)
        host, port = host_part.split(":", 1) if ":" in host_part else (host_part, "8080")
        username, password = (auth.split(":", 1) + [""])[:2]
        return {"server": f"http://{host}:{port}", "username": username, "password": password}
    host, port = raw.split(":", 1) if ":" in raw else (raw, "8080")
    return {"server": f"http://{host}:{port}"}


def _parse_skool_chat_cards(raw_html: str) -> List[Dict[str, Any]]:
    if not raw_html:
        return []

    cards: Dict[str, Dict[str, Any]] = {}
    anchor_pattern = re.compile(
        r'<a[^>]+href="/chat\?ch=([a-zA-Z0-9]+)[^"]*"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    name_pattern = re.compile(r'<span class="styled__UserNameText[^"]*"><span>(.*?)</span></span>', re.IGNORECASE | re.DOTALL)
    msg_pattern = re.compile(r'<div class="styled__MessageContent[^"]*">(.*?)</div>', re.IGNORECASE | re.DOTALL)
    time_pattern = re.compile(r'<div class="styled__LastMessageTime[^"]*">(.*?)</div>', re.IGNORECASE | re.DOTALL)
    user_link_pattern = re.compile(r'href="/@([^"?]+)"', re.IGNORECASE)

    for match in anchor_pattern.finditer(raw_html):
        chat_id = (match.group(1) or "").strip()
        block = match.group(2) or ""
        if not chat_id:
            continue

        name_match = name_pattern.search(block)
        msg_match = msg_pattern.search(block)
        time_match = time_pattern.search(block)
        user_link_match = user_link_pattern.search(block)

        contact_name = _clean_html_text(name_match.group(1) if name_match else "") or f"Skool Chat {chat_id[:6]}"
        message_text = _clean_html_text(msg_match.group(1) if msg_match else "") or "New message in Skool chat"
        last_message_time = _clean_html_text(time_match.group(1) if time_match else "")
        if last_message_time.startswith("•"):
            last_message_time = last_message_time.lstrip("•").strip()
        username_slug = (user_link_match.group(1).strip() if user_link_match else "")
        post_url = f"https://www.skool.com/chat?ch={chat_id}"
        if username_slug:
            post_url = f"{post_url}&user={username_slug}"

        cards[chat_id] = {
            "chat_id": chat_id,
            "contact_name": contact_name,
            "message_text": message_text,
            "last_message_time": last_message_time or now_display_time(),
            "post_url": post_url,
            "messages": [],
        }

    return list(cards.values())


def _parse_skool_chat_messages(raw_html: str, contact_name: str) -> List[Dict[str, str]]:
    if not raw_html:
        return []

    pattern = re.compile(
        r'<span class="styled__UserNameText[^"]*"><span>(.*?)</span></span>.*?'
        r'<div class="styled__TypographyWrapper[^"]*">([^<]+)</div>\s*</div>\s*'
        r'<div title="([^"]+)">\s*<div class="styled__Wrapper-sc-y5pp90-0[^"]*">(.*?)</div>\s*</div>',
        re.IGNORECASE | re.DOTALL,
    )
    span_text_pattern = re.compile(r"<span>(.*?)</span>", re.IGNORECASE | re.DOTALL)

    messages: List[Dict[str, str]] = []
    normalized_contact = _clean_html_text(contact_name).lower()
    for match in pattern.finditer(raw_html):
        sender_name = _clean_html_text(match.group(1) or "")
        time_label = _clean_html_text(match.group(2) or "")
        title_timestamp = _clean_html_text(match.group(3) or "")
        wrapper = match.group(4) or ""
        span_parts = [_clean_html_text(m.group(1) or "") for m in span_text_pattern.finditer(wrapper)]
        span_parts = [part for part in span_parts if part]
        text = "\n".join(span_parts).strip() if span_parts else _clean_html_text(wrapper)
        if not text:
            continue
        sender = "inbound"
        if normalized_contact and sender_name and _clean_html_text(sender_name).lower() != normalized_contact:
            sender = "outbound"
        messages.append(
            {
                "text": text,
                "sender": sender,
                "timestamp": time_label or title_timestamp or now_display_time(),
            }
        )

    return messages[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:]


def _extract_chat_ids_from_page(page: Any) -> List[str]:
    if not PLAYWRIGHT_AVAILABLE:
        return []
    try:
        raw_ids = page.evaluate(
            """
            () => {
              const out = [];
              const seen = new Set();
              const anchors = Array.from(document.querySelectorAll('a[href*="/chat?ch="]'));
              for (const anchor of anchors) {
                const href = anchor.getAttribute("href") || "";
                try {
                  const url = new URL(href, window.location.origin);
                  const chatId = (url.searchParams.get("ch") || "").trim();
                  if (!chatId || seen.has(chatId)) continue;
                  seen.add(chatId);
                  out.push(chatId);
                } catch (_) {
                  continue;
                }
              }
              return out;
            }
            """
        )
    except Exception:
        return []
    if not isinstance(raw_ids, list):
        return []
    return [str(chat_id).strip() for chat_id in raw_ids if str(chat_id).strip()]


def _extract_chat_targets_from_dropdown(page: Any) -> List[Dict[str, str]]:
    if not PLAYWRIGHT_AVAILABLE:
        return []
    try:
        rows = page.evaluate(
            """
            () => {
              const out = [];
              const seen = new Set();
              const anchors = Array.from(document.querySelectorAll("a[href*='/chat?ch=']"));
              for (const anchor of anchors) {
                const href = (anchor.getAttribute("href") || "").trim();
                if (!href) continue;
                let url = null;
                try {
                  url = new URL(href, window.location.origin);
                } catch (_) {
                  continue;
                }
                const chatId = (url.searchParams.get("ch") || "").trim();
                if (!chatId || seen.has(chatId)) continue;
                seen.add(chatId);
                const clr = (url.searchParams.get("clr") || "").trim();
                const nameNode = anchor.querySelector("span[class*='styled__UserNameText'] span");
                const messageNode = anchor.querySelector("div[class*='styled__MessageContent']");
                const timeNode = anchor.querySelector("div[class*='styled__LastMessageTime']");
                const pickCommunityLink = (scope) => Array.from(scope?.querySelectorAll?.("a[href]") || []).find((el) => {
                  const h = (el.getAttribute("href") || "").trim();
                  return h.startsWith("/") && !h.startsWith("/@") && !h.includes("/chat?ch=");
                }) || null;
                const groupLink = pickCommunityLink(anchor) || pickCommunityLink(anchor.parentElement) || null;
                const groupHref = (groupLink?.getAttribute("href") || "").trim();
                const groupTitleNode = groupLink?.querySelector("[title]") || groupLink?.querySelector("img[alt]");
                const groupName = (
                  groupTitleNode?.getAttribute?.("title") ||
                  groupTitleNode?.getAttribute?.("alt") ||
                  groupLink?.textContent ||
                  ""
                ).trim();
                out.push({
                  chat_id: chatId,
                  clr,
                  href: url.pathname + url.search,
                  contact_name: (nameNode?.textContent || "").trim(),
                  preview_message: (messageNode?.textContent || "").trim(),
                  preview_time: (timeNode?.textContent || "").replace(/^•\\s*/, "").trim(),
                  origin_group_name: groupName,
                  origin_group_href: groupHref,
                });
              }
              return out;
            }
            """
        )
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        chat_id = str(row.get("chat_id") or "").strip()
        href = str(row.get("href") or "").strip()
        if not chat_id or not href:
            continue
        full_url = f"https://www.skool.com{href}"
        out.append(
            {
                "chat_id": chat_id,
                "href": href,
                "target_url": full_url,
                "clr": str(row.get("clr") or "").strip(),
                "contact_name": str(row.get("contact_name") or "").strip(),
                "preview_message": str(row.get("preview_message") or "").strip(),
                "preview_time": str(row.get("preview_time") or "").strip(),
                "origin_group_name": str(row.get("origin_group_name") or "").strip(),
                "origin_group_href": str(row.get("origin_group_href") or "").strip(),
            }
        )
    return out


def _collect_chat_targets_from_notification_button(page: Any, max_chats: int) -> List[Dict[str, str]]:
    if not PLAYWRIGHT_AVAILABLE:
        return []
    try:
        rows = page.evaluate(
            """
            async ({ maxChats }) => {
              const out = [];
              const toAbs = (href) => {
                try { return new URL(href, window.location.origin).toString(); } catch (_) { return ""; }
              };
              let btn = null;
              const buttons = Array.from(document.querySelectorAll("button"));
              for (const b of buttons) {
                const svg = b.querySelector("svg[viewBox='0 0 40 34']");
                if (svg) { btn = b; break; }
              }
              if (!btn) {
                btn =
                  document.querySelector("button[class*='ChatNotificationsIconButton']") ||
                  document.querySelector("div[class*='NotificationButtonWrapper'] button[type='button']");
              }
              if (!btn) return out;
              const clickBtn = () => btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
              clickBtn();
              await new Promise((r) => setTimeout(r, 420));

              const container =
                document.querySelector("div.styled__PopoverItems-sc-1ucrcaa-2") ||
                document.querySelector("div[class*='PopoverItems']") ||
                document.querySelector("div[class*='Dropdown'][class*='Content']") ||
                document.querySelector("div[role='menu']");
              // If popover container is not found, still try globally visible chat links.
              if (!container) {
                const seen = new Set();
                const anchors = Array.from(document.querySelectorAll("a[href^='/chat?']"));
                for (const a of anchors) {
                  const href = (a.getAttribute("href") || "").trim();
                  if (!href) continue;
                  let url;
                  try { url = new URL(href, window.location.origin); } catch (_) { continue; }
                  const chatId = (url.searchParams.get("ch") || "").trim();
                  if (!chatId || seen.has(chatId)) continue;
                  seen.add(chatId);
                  const clr = (url.searchParams.get("clr") || "").trim();
                  out.push({
                    chat_id: chatId,
                    href: url.pathname + url.search,
                    target_url: toAbs(url.pathname + url.search),
                    clr,
                    contact_name: "",
                    preview_message: "",
                    preview_time: "",
                    origin_group_name: "",
                    origin_group_href: "",
                  });
                  if (out.length >= Math.max(1, Number(maxChats || 1))) break;
                }
                return out;
              }

              let previous = -1;
              let stable = 0;
              for (let i = 0; i < 40; i += 1) {
                const current = container.querySelectorAll("a[href^='/chat?']").length;
                if (current === previous) stable += 1;
                else stable = 0;
                previous = current;
                if (stable >= 2) break;
                container.scrollTo(0, container.scrollHeight);
                await new Promise((r) => setTimeout(r, 350));
              }

              const seen = new Set();
              let anchors = Array.from(container.querySelectorAll("a[href^='/chat?']"));
              if (!anchors.length) {
                anchors = Array.from(document.querySelectorAll("a[href^='/chat?']"));
              }
              for (const a of anchors) {
                const href = (a.getAttribute("href") || "").trim();
                if (!href) continue;
                let url;
                try { url = new URL(href, window.location.origin); } catch (_) { continue; }
                const chatId = (url.searchParams.get("ch") || "").trim();
                if (!chatId || seen.has(chatId)) continue;
                seen.add(chatId);
                const clr = (url.searchParams.get("clr") || "").trim();
                out.push({
                  chat_id: chatId,
                  href: url.pathname + url.search,
                  target_url: toAbs(url.pathname + url.search),
                  clr,
                  contact_name: "",
                  preview_message: "",
                  preview_time: "",
                  origin_group_name: "",
                  origin_group_href: "",
                });
                if (out.length >= Math.max(1, Number(maxChats || 1))) break;
              }
              return out;
            }
            """,
            {"maxChats": max(1, int(max_chats))},
        )
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        chat_id = str(row.get("chat_id") or "").strip()
        if not chat_id:
            continue
        out.append(
            {
                "chat_id": chat_id,
                "href": str(row.get("href") or "").strip(),
                "target_url": str(row.get("target_url") or "").strip() or f"https://www.skool.com/chat?ch={chat_id}",
                "clr": str(row.get("clr") or "").strip(),
                "contact_name": "",
                "preview_message": "",
                "preview_time": "",
                "origin_group_name": "",
                "origin_group_href": "",
            }
        )
    return out[: max(1, int(max_chats))]


def _collect_chat_targets_with_scroll(page: Any, max_rounds: int = 60) -> List[Dict[str, str]]:
    if not PLAYWRIGHT_AVAILABLE:
        return []
    all_targets: Dict[str, Dict[str, str]] = {}
    stable_rounds = 0
    bottom_rounds = 0
    prev_count = -1

    for _ in range(max_rounds):
        for item in _extract_chat_targets_from_dropdown(page):
            chat_id = str(item.get("chat_id") or "").strip()
            if not chat_id:
                continue
            all_targets[chat_id] = item

        current_count = len(all_targets)
        if current_count == prev_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
        prev_count = current_count

        try:
            state = page.evaluate(
                """
                () => {
                  const allAnchors = Array.from(document.querySelectorAll("a[href*='/chat?ch=']"));
                  const nearestScrollable = (start) => {
                    let node = start;
                    for (let i = 0; i < 16 && node; i += 1) {
                      const style = window.getComputedStyle(node);
                      const oy = style?.overflowY || "";
                      if ((oy.includes("auto") || oy.includes("scroll")) && node.scrollHeight > node.clientHeight + 8) {
                        return node;
                      }
                      node = node.parentElement;
                    }
                    return null;
                  };

                  let target = null;
                  const strongRoots = Array.from(
                    document.querySelectorAll("div[class*='PopoverItems'], div[class*='PopoverListWrapper'], div[class*='DropdownContent']")
                  );
                  if (strongRoots.length > 0) {
                    let bestScore = -1;
                    for (const root of strongRoots) {
                      const anchorCount = root.querySelectorAll("a[href*='/chat?ch=']").length;
                      if (anchorCount <= 0) continue;
                      const sc = nearestScrollable(root) || root;
                      const score = anchorCount * 5 + Math.min((sc.scrollHeight || 0) / 200, 40);
                      if (score > bestScore) {
                        bestScore = score;
                        target = sc;
                      }
                    }
                  }

                  if (!target && allAnchors.length > 0) {
                    target = nearestScrollable(allAnchors[0].parentElement);
                  }

                  if (!target) {
                    const roots = Array.from(document.querySelectorAll("div"));
                    let score = -1;
                    for (const el of roots) {
                      const style = window.getComputedStyle(el);
                      if (!style) continue;
                      const oy = style.overflowY || "";
                      if (!(oy.includes("auto") || oy.includes("scroll"))) continue;
                      if (el.scrollHeight <= el.clientHeight + 8) continue;
                      const aCount = el.querySelectorAll("a[href*='/chat?ch=']").length;
                      if (aCount <= 0) continue;
                      const s = aCount * 3 + Math.min(el.scrollHeight / 300, 20);
                      if (s > score) {
                        score = s;
                        target = el;
                      }
                    }
                  }
                  if (!target) return { found: false, moved: false, atBottom: true };
                  const before = target.scrollTop;
                  const step = Math.max(320, Math.floor(target.clientHeight * 0.95));
                  target.scrollTop = Math.min(target.scrollTop + step, target.scrollHeight);
                  target.dispatchEvent(new Event("scroll", { bubbles: true }));
                  const moved = target.scrollTop > before;
                  const atBottom = target.scrollTop + target.clientHeight >= target.scrollHeight - 4;
                  const rect = target.getBoundingClientRect();
                  return {
                    found: true,
                    moved,
                    atBottom,
                    x: rect.left + rect.width / 2,
                    y: rect.top + Math.min(rect.height - 8, rect.height / 2),
                    visibleCount: target.querySelectorAll("a[href*='/chat?ch=']").length,
                    scrollTop: target.scrollTop,
                    scrollHeight: target.scrollHeight,
                    clientHeight: target.clientHeight
                  };
                }
                """
            )
            if isinstance(state, dict) and state.get("found"):
                try:
                    if state.get("x") is not None and state.get("y") is not None:
                        page.mouse.move(float(state["x"]), float(state["y"]))
                        page.mouse.wheel(0, 900)
                except Exception:
                    pass
                if state.get("atBottom"):
                    bottom_rounds += 1
                else:
                    bottom_rounds = 0
                if state.get("atBottom") and not state.get("moved") and stable_rounds >= 6 and bottom_rounds >= 2:
                    break
            elif stable_rounds >= 6:
                break
        except Exception:
            break
        if stable_rounds >= 8:
            break
        page.wait_for_timeout(650)

    return list(all_targets.values())


def _wait_for_chat_targets(page: Any, timeout_ms: int = 10000) -> List[Dict[str, str]]:
    if not PLAYWRIGHT_AVAILABLE:
        return []
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        targets = _extract_chat_targets_from_dropdown(page)
        if targets:
            return targets
        page.wait_for_timeout(400)
    return []


def _wait_for_chat_button_ready(page: Any, timeout_ms: int = 15000) -> bool:
    if not PLAYWRIGHT_AVAILABLE:
        return False
    selectors = [
        "button[class*='ChatNotificationsIconButton']",
        "div[class*='NotificationButtonWrapper'] button[type='button']",
    ]
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for selector in selectors:
            try:
                btn = page.locator(selector).first
                if btn.count() > 0 and btn.is_visible(timeout=350):
                    return True
            except Exception:
                continue
        page.wait_for_timeout(350)
    return False


def _goto_skool_entry_page(page: Any, timeout_ms: int) -> tuple[bool, str]:
    if not PLAYWRIGHT_AVAILABLE:
        return False, "Playwright is not available"
    attempts = [
        "https://www.skool.com/",
    ]
    last_error = ""
    for url in attempts:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=9000)
            except Exception:
                pass
            page.wait_for_timeout(1800)
            return True, url
        except Exception as exc:
            last_error = str(exc or "")
            continue
    return False, (last_error[:220] or "navigation failed")


def _has_authenticated_markers(page: Any) -> bool:
    if not PLAYWRIGHT_AVAILABLE:
        return False
    try:
        result = page.evaluate(
            """
            () => {
              const selectors = [
                "button[class*='ChatNotificationsIconButton']",
                "a[href*='/chat?ch=']",
                "a[href^='/@']",
                "div[class*='TopNav']",
                "div[class*='NotificationButtonWrapper'] button[type='button']",
              ];
              for (const selector of selectors) {
                if (document.querySelector(selector)) return true;
              }
              const text = (document.body?.innerText || "").toLowerCase();
              if (text.includes("log out") || text.includes("logout")) return true;
              return false;
            }
            """
        )
        return bool(result)
    except Exception:
        return False


def _page_debug_state(page: Any) -> Dict[str, Any]:
    if not PLAYWRIGHT_AVAILABLE:
        return {"url": "", "auth_markers": False, "chat_links": 0, "title": ""}
    try:
        payload = page.evaluate(
            """
            () => {
              const authSelectors = [
                "button[class*='ChatNotificationsIconButton']",
                "a[href*='/chat?ch=']",
                "a[href^='/@']",
                "div[class*='TopNav']",
                "div[class*='NotificationButtonWrapper'] button[type='button']",
              ];
              let auth = false;
              for (const selector of authSelectors) {
                if (document.querySelector(selector)) {
                  auth = true;
                  break;
                }
              }
              return {
                auth_markers: auth,
                chat_links: document.querySelectorAll("a[href*='/chat?ch=']").length,
                title: (document.title || "").trim(),
              };
            }
            """
        )
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "url": str(getattr(page, "url", "") or "").strip(),
        "auth_markers": bool(payload.get("auth_markers")),
        "chat_links": int(payload.get("chat_links") or 0),
        "title": str(payload.get("title") or "").strip(),
    }


def _open_chat_dropdown(page: Any) -> bool:
    if not PLAYWRIGHT_AVAILABLE:
        return False
    # Primary selector from real DOM snippet + resilient fallbacks.
    button_selectors = [
        "button[class*='ChatNotificationsIconButton']",
        "div[class*='NotificationButtonWrapper'] button[type='button']",
        "button:has(svg)",
    ]
    for selector in button_selectors:
        try:
            btn = page.locator(selector).first
            if btn.count() == 0:
                continue
            if not btn.is_visible(timeout=1200):
                continue
            try:
                btn.click(timeout=2500)
            except Exception:
                try:
                    btn.click(timeout=2500, force=True)
                except Exception:
                    page.evaluate(
                        """
                        (sel) => {
                          const el = document.querySelector(sel);
                          if (el) el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                        }
                        """,
                        selector,
                    )
            targets = _wait_for_chat_targets(page, timeout_ms=2500)
            if targets:
                return True
        except Exception:
            continue
    return False


# Open the Skool community switcher dropdown from top navigation.
def _open_community_switcher_dropdown(page: Any) -> bool:
    if not PLAYWRIGHT_AVAILABLE:
        return False
    # Prefer exact switcher button (double-arrow icon in top nav) before generic fallbacks.
    try:
        clicked_exact = bool(
            page.evaluate(
                """
                () => {
                  const buttons = Array.from(document.querySelectorAll("button[type='button'], button"));
                  const target = buttons.find((btn) => {
                    const svg = btn.querySelector("svg[viewBox='0 0 12 20']");
                    return !!svg;
                  });
                  if (!target) return false;
                  target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                  return true;
                }
                """
            )
        )
        if clicked_exact:
            page.wait_for_timeout(400)
            opened = bool(
                page.evaluate(
                    """
                    () => {
                      const container =
                        document.querySelector("div[class*='DropdownContent']") ||
                        document.querySelector("div[role='menu']");
                      if (!container) return false;
                      return container.querySelector("div[class*='SwitcherItem'], [data-testid^='dropdown-item-']") !== null;
                    }
                    """
                )
            )
            if opened:
                return True
    except Exception:
        pass

    button_selectors = [
        "div[class*='DropdownMenuWrapper'] button[class*='DropdownButton']",
        "button[class*='DropdownButton']",
        "div[class*='DropdownWrapper'] button[type='button']",
        "header button[type='button']",
    ]
    for selector in button_selectors:
        try:
            btn = page.locator(selector).first
            if btn.count() == 0:
                continue
            if not btn.is_visible(timeout=1200):
                continue
            try:
                btn.click(timeout=2500)
            except Exception:
                btn.click(timeout=2500, force=True)
            page.wait_for_timeout(350)
            opened = bool(
                page.evaluate(
                    """
                    () => {
                      const container =
                        document.querySelector("div[class*='DropdownContent']") ||
                        document.querySelector("div[role='menu']");
                      if (!container) return false;
                      return container.querySelector("div[class*='SwitcherItem'], [data-testid^='dropdown-item-']") !== null;
                    }
                    """
                )
            )
            if opened:
                return True
        except Exception:
            continue
    return False


# Read candidate community options from switcher dropdown.
def _extract_community_switcher_items(page: Any) -> List[Dict[str, str]]:
    if not PLAYWRIGHT_AVAILABLE:
        return []
    try:
        rows = page.evaluate(
            """
            () => {
              const container =
                document.querySelector("div[class*='DropdownContent']") ||
                document.querySelector("div[role='menu']");
              if (!container) return [];
              const blocked = new Set(["create a community", "discover communities", "search"]);
              const out = [];
              const seen = new Set();
              const switches = Array.from(container.querySelectorAll("div[class*='SwitcherItem']"));
              for (let idx = 0; idx < switches.length; idx += 1) {
                const node = switches[idx];
                const name = (node.querySelector("span")?.textContent || "").trim();
                const low = name.toLowerCase();
                if (!name || blocked.has(low)) continue;
                if (seen.has(name)) continue;
                seen.add(name);
                const root =
                  node.closest("[data-testid^='dropdown-item-']") ||
                  node.closest("[data-rbd-draggable-id]") ||
                  node;
                const href = (root.querySelector("a[href]")?.getAttribute("href") || "").trim();
                const hrefLow = href.toLowerCase();
                if (hrefLow.includes("/signup") || hrefLow.includes("discover")) continue;
                out.push({ name, idx: String(idx), href });
              }
              return out;
            }
            """
        )
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    items: List[Dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        idx = str(row.get("idx") or "").strip()
        href = str(row.get("href") or "").strip()
        if not name or not idx:
            continue
        items.append({"name": name, "idx": idx, "href": href})
    return items


# Click a specific switcher row by community display name.
def _click_community_switcher_item_by_name(page: Any, name: str) -> bool:
    if not PLAYWRIGHT_AVAILABLE:
        return False
    target_name = str(name or "").strip().lower()
    if not target_name:
        return False
    try:
        clicked = page.evaluate(
            """
            ({ targetName }) => {
              const container =
                document.querySelector("div[class*='DropdownContent']") ||
                document.querySelector("div[role='menu']");
              if (!container) return false;
              const nodes = Array.from(container.querySelectorAll("div[class*='SwitcherItem']"));
              const target = nodes.find((node) => {
                const text = (node.querySelector("span")?.textContent || "").trim().toLowerCase();
                return text === targetName;
              });
              if (!target) return false;
              const clickable =
                target.closest("[data-testid^='dropdown-item-']") ||
                target.closest("[data-rbd-draggable-id]") ||
                target;
              const fire = (type) => clickable.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
              fire("pointerdown");
              fire("mousedown");
              fire("mouseup");
              fire("click");
              return true;
            }
            """,
            {"targetName": target_name},
        )
    except Exception:
        return False
    return bool(clicked)


# Normalize a Skool community URL to stable absolute form.
def _normalize_skool_community_url(path_or_url: str) -> str:
    raw = _abs_skool_url(path_or_url)
    if not raw:
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(raw)
        path = (parsed.path or "").rstrip("/")
        if not path:
            return "https://www.skool.com"
        return f"https://www.skool.com{path}"
    except Exception:
        return raw


def _is_community_active_member(page, url: str, timeout_ms: int = 8000) -> bool:
    """Check if profile is active member (exclude pending/join-request). Matches community-join-manager logic."""
    try:
        page.set_default_timeout(timeout_ms)
        resp = page.goto(url, timeout=timeout_ms)
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1500)
        if resp and (resp.status == 404 or resp.status >= 500):
            return False
        if "/login" in str(page.url or "").lower():
            return False
        txt = page.inner_text("body") or ""
        txt_lower = txt.lower()
        if re.search(r"invite\s+people|invite\s+others|invite\s+members|leave\s+group", txt_lower):
            return True
        if re.search(r"pending|you requested membership|cancel request", txt_lower):
            return False
        return False
    except Exception:
        return False


SKOOL_GROUPS_URL = "https://api2.skool.com/self/groups?offset={offset}&limit=30&prefs=false&members=true"


def _cookies_from_json(cookie_json) -> str:
    if not cookie_json or not str(cookie_json).strip():
        return ""
    try:
        c = json.loads(cookie_json) if isinstance(cookie_json, str) else cookie_json
        arr = c if isinstance(c, list) else [c]
        return "; ".join(str(x.get("name", "")) + "=" + str(x.get("value", "")) for x in arr if x.get("name"))
    except Exception:
        return ""


def skool_api_get_groups(cookie_json, timeout=15, profile_id=None, _retry_count=0):
    """Fetch /self/groups with cookie_json. On 401/403, one retry via ensure_profile_auth if profile_id given."""
    import urllib.request
    import urllib.error
    cookies = _cookies_from_json(cookie_json)
    if not cookies:
        raise ValueError("No cookies / not logged in")
    headers = {
        "Cookie": cookies,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://www.skool.com",
        "Referer": "https://www.skool.com/",
    }
    all_groups = []
    offset = 0
    for _ in range(50):
        url = SKOOL_GROUPS_URL.format(offset=offset)
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (401, 403):
                    raise ValueError("cookies invalid/expired")
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and profile_id and _retry_count == 0:
                auth = ensure_profile_auth(profile_id)
                LOGGER.info("[FETCH] auth_check_ok=%s refresh_attempted=%s refresh_result=%s", auth.get("ok"), auth.get("refresh_attempted"), auth.get("refresh_result"))
                if auth.get("ok") and auth.get("cookie_json"):
                    return skool_api_get_groups(auth["cookie_json"], timeout, profile_id, _retry_count=1)
            raise ValueError("cookies invalid/expired" if e.code in (401, 403) else f"API HTTP {e.code}")
        except Exception as e:
            err = str(e).lower()
            if ("401" in err or "403" in err) and profile_id and _retry_count == 0:
                auth = ensure_profile_auth(profile_id)
                LOGGER.info("[FETCH] auth_check_ok=%s refresh_attempted=%s refresh_result=%s", auth.get("ok"), auth.get("refresh_attempted"), auth.get("refresh_result"))
                if auth.get("ok") and auth.get("cookie_json"):
                    return skool_api_get_groups(auth["cookie_json"], timeout, profile_id, _retry_count=1)
            if "401" in err or "403" in err:
                raise ValueError("cookies invalid/expired")
            raise
        groups = data.get("groups") or data.get("data") or (data if isinstance(data, list) else [])
        all_groups.extend(groups)
        if not data.get("has_more", False):
            break
        offset += 30
    return all_groups


def _membership_status(group):
    try:
        meta = group.get("metadata")
        if isinstance(meta, str):
            meta = json.loads(meta) if meta.strip() else {}
        meta = meta or {}
        member = meta.get("member")
        if isinstance(member, str):
            member = json.loads(member) if member.strip() else {}
        member = member or {}
        role = (member.get("role") or "").strip().lower()
        if role == "pending":
            return "pending"
        if role == "member" or (role and role != "pending"):
            return "joined"
        return "unknown"
    except Exception:
        return "unknown"

# Fetch communities (API-driven) and resolving each community URL.
def _fetch_skool_communities_for_profile(profile_id, profile_name, proxy, cookie_json=None):
    auth = ensure_profile_auth(profile_id)
    if not auth.get("ok"):
        status = auth.get("status", "login_required")
        msg = auth.get("message", "Login required")
        stats = {"auth_status": status, "refresh_attempted": auth.get("refresh_attempted"), "refresh_result": auth.get("refresh_result")}
        return [], msg, stats
    cookie_json = auth.get("cookie_json") or cookie_json
    if not cookie_json or not str(cookie_json).strip():
        return [], "No cookies / not logged in", None
    try:
        groups = skool_api_get_groups(cookie_json, profile_id=profile_id)
    except ValueError as e:
        return [], str(e), None
    except Exception as e:
        return [], f"API fetch failed: {str(e)[:180]}", None
    total = len(groups)
    joined_count = pending_count = unknown_count = 0
    discovered = []
    for g in groups:
        status = _membership_status(g)
        if status == "pending":
            pending_count += 1
            continue
        if status == "unknown":
            unknown_count += 1
            continue
        joined_count += 1
        slug = str(g.get("slug") or g.get("groupSlug") or g.get("name") or "").strip()
        if not slug:
            continue
        meta = g.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta) if meta.strip() else {}
            except Exception:
                meta = {}
        meta = meta or {}
        name = str(meta.get("display_name") or g.get("name") or g.get("title") or slug or "").strip()
        discovered.append({"name": name, "url": f"https://www.skool.com/{slug}"})
    LOGGER.info(f"[FETCH] profile={profile_name} total={total} joined={joined_count} pending_excluded={pending_count} unknown_excluded={unknown_count}")
    return discovered, None, {"total": total, "joined": joined_count, "pending_excluded": pending_count, "unknown_excluded": unknown_count}

def _upsert_profile_communities_from_sync(
    db: sqlite3.Connection,
    profile_id: str,
    discovered: List[Dict[str, str]],
) -> tuple[int, int, int]:
    created = 0
    updated = 0
    skipped = 0
    existing_rows = db.execute(
        "SELECT * FROM communities WHERE profileId = ?",
        (profile_id,),
    ).fetchall()
    by_url: Dict[str, sqlite3.Row] = {}
    for row in existing_rows:
        normalized = _normalize_skool_community_url(str(row["url"] or ""))
        if normalized:
            by_url[normalized] = row

    for item in discovered:
        name = str(item.get("name") or "").strip()
        normalized_url = _normalize_skool_community_url(str(item.get("url") or ""))
        if not name or not normalized_url:
            skipped += 1
            continue
        existing = by_url.get(normalized_url)
        if existing:
            db.execute(
                "UPDATE communities SET name = ?, url = ?, lastScanned = ? WHERE id = ?",
                (name, normalized_url, now_display_time(), str(existing["id"])),
            )
            updated += 1
            continue
        community_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO communities (
                id, profileId, name, url, dailyLimit, maxPostAgeDays, lastScanned, status,
                matchesToday, actionsToday, totalScannedPosts, totalKeywordMatches
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                community_id,
                profile_id,
                name,
                normalized_url,
                5,
                0,
                now_display_time(),
                "active",
                0,
                0,
                0,
                0,
            ),
        )
        created += 1
    return created, updated, skipped


def _prune_profile_communities_from_sync(
    db: sqlite3.Connection,
    profile_id: str,
    discovered: List[Dict[str, str]],
) -> int:
    discovered_urls: Set[str] = set()
    for item in discovered:
        normalized_url = _normalize_skool_community_url(str(item.get("url") or ""))
        if normalized_url:
            discovered_urls.add(normalized_url)

    rows = db.execute(
        "SELECT id, url FROM communities WHERE profileId = ?",
        (profile_id,),
    ).fetchall()
    to_delete: List[str] = []
    for row in rows:
        community_id = str(row["id"] or "").strip()
        normalized_url = _normalize_skool_community_url(str(row["url"] or ""))
        if community_id and normalized_url and normalized_url not in discovered_urls:
            to_delete.append(community_id)

    if not to_delete:
        return 0

    for community_id in to_delete:
        db.execute("DELETE FROM queue_items WHERE communityId = ?", (community_id,))
        db.execute("DELETE FROM communities WHERE id = ?", (community_id,))
    return len(to_delete)


def _wait_for_chat_view(page: Any, timeout_ms: int = 12000) -> bool:
    selectors = [
        "textarea[placeholder^='Message ']",
        "textarea[placeholder*='Message']",
        "div[class*='styled__ChatContainer']",
    ]
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=timeout_ms, state="visible")
            return True
        except Exception:
            continue
    return False


def _scroll_chat_history_to_top(page: Any, max_rounds: int = 24) -> None:
    if not PLAYWRIGHT_AVAILABLE:
        return
    stable_rounds = 0
    prev_state = None
    for _ in range(max_rounds):
        try:
            state = page.evaluate(
                """
                () => {
                  const messageRoot = document.querySelector("div[class*='styled__ChatContainer']") || document.body;
                  const candidates = Array.from(messageRoot.querySelectorAll("div")).filter((el) => {
                    const style = window.getComputedStyle(el);
                    if (!style) return false;
                    const overflowY = style.overflowY || "";
                    const scrollable = (overflowY.includes("auto") || overflowY.includes("scroll")) && el.scrollHeight > el.clientHeight + 16;
                    if (!scrollable) return false;
                    return el.querySelector("a[href^='/@']") || el.querySelector("div[class*='styled__Wrapper-sc-y5pp90-0']");
                  });
                  let target = null;
                  let score = -1;
                  for (const el of candidates) {
                    const currentScore =
                      el.querySelectorAll("div[class*='styled__Wrapper-sc-y5pp90-0']").length * 4 +
                      el.querySelectorAll("a[href^='/@']").length * 2 +
                      Math.min(el.scrollHeight / 1000, 5);
                    if (currentScore > score) {
                      score = currentScore;
                      target = el;
                    }
                  }
                  if (!target) {
                    return { found: false, atTop: true, top: 0, height: 0, msgCount: 0 };
                  }
                  const msgCount = target.querySelectorAll("div[class*='styled__Wrapper-sc-y5pp90-0']").length;
                  const before = target.scrollTop;
                  target.scrollTop = 0;
                  return {
                    found: true,
                    atTop: target.scrollTop <= 2,
                    top: target.scrollTop,
                    height: target.scrollHeight,
                    msgCount,
                    before
                  };
                }
                """
            )
        except Exception:
            return

        if not isinstance(state, dict):
            return
        signature = (state.get("top"), state.get("height"), state.get("msgCount"))
        if state.get("atTop") and signature == prev_state:
            stable_rounds += 1
        else:
            stable_rounds = 0
        prev_state = signature
        if stable_rounds >= 2:
            return
        page.wait_for_timeout(350)


def _extract_skool_chat_view(page: Any) -> Dict[str, Any]:
    if not PLAYWRIGHT_AVAILABLE:
        return {"account": {}, "messages": []}
    try:
        payload = page.evaluate(
            """
            () => {
              const root = document.querySelector("div[class*='styled__ChatContainer']") || document.body;
              const headerRoot =
                root.querySelector("header") ||
                root.querySelector("div[class*='styled__Header']") ||
                root;
              const profileLink = headerRoot.querySelector("a[href^='/@']");
              const accountDisplayNameEl =
                headerRoot.querySelector("span[class*='styled__UserNameText'] span") ||
                headerRoot.querySelector("span[class*='styled__UserNameText']") ||
                profileLink?.querySelector("span");
              const accountUsernameEl =
                headerRoot.querySelector("div[class*='styled__Username']") ||
                Array.from(headerRoot.querySelectorAll("div")).find((el) => (el.textContent || "").trim().startsWith("@"));
              const statusEl =
                headerRoot.querySelector("div[class*='styled__Status']") ||
                Array.from(headerRoot.querySelectorAll("div")).find((el) => {
                  const txt = (el.textContent || "").trim().toLowerCase();
                  return txt.includes("online") || txt.includes("last seen") || txt.includes("timezone");
                });
              const pickCommunityLink = (scope) => Array.from(scope?.querySelectorAll?.("a[href]") || []).find((el) => {
                const href = (el.getAttribute("href") || "").trim();
                return href.startsWith("/") && !href.startsWith("/@") && !href.includes("/chat?");
              }) || null;
              const groupLink = pickCommunityLink(headerRoot) || pickCommunityLink(root) || null;
              const groupHref = (groupLink?.getAttribute("href") || "").trim();
              const groupTitleNode = groupLink?.querySelector("[title]") || groupLink?.querySelector("img[alt]");
              const groupName = (
                groupTitleNode?.getAttribute?.("title") ||
                groupTitleNode?.getAttribute?.("alt") ||
                groupLink?.textContent ||
                ""
              ).trim();
              const account = {
                account_profile_link: profileLink?.getAttribute("href") || "",
                account_display_name: (accountDisplayNameEl?.textContent || "").trim(),
                account_username: (accountUsernameEl?.textContent || "").trim(),
                account_status: (statusEl?.textContent || "").trim(),
                origin_group_name: groupName,
                origin_group_href: groupHref,
              };

              const messageNodes = Array.from(root.querySelectorAll("div[class*='styled__Wrapper-sc-y5pp90-0']"));
              const dateRegex = /(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\\s+\\d{1,2}(?:st|nd|rd|th)?(?:\\s+\\d{4})?/i;
              const timeRegex = /\\b\\d{1,2}:\\d{2}\\s?(?:am|pm)\\b/i;
              const messages = [];
              for (const node of messageNodes) {
                const text = (node.innerText || node.textContent || "").replace(/\\u00a0/g, " ").trim();
                if (!text) continue;

                let scope = node;
                for (let i = 0; i < 8 && scope; i += 1) {
                  if (scope.querySelector("a[href^='/@']") && scope.querySelector("div[class*='styled__TypographyWrapper']")) break;
                  scope = scope.parentElement;
                }
                const resolvedScope = scope || node.parentElement || node;
                const authorLink = resolvedScope.querySelector("a[href^='/@']");
                const authorNameEl =
                  resolvedScope.querySelector("span[class*='styled__UserNameText'] span") ||
                  resolvedScope.querySelector("span[class*='styled__UserNameText']") ||
                  authorLink?.querySelector("span");
                const timeEl = resolvedScope.querySelector("div[class*='styled__TypographyWrapper']");
                let timestampText = (timeEl?.textContent || "").trim();
                if (!timestampText) {
                  const withTime = Array.from(resolvedScope.querySelectorAll("div, span")).find((el) => timeRegex.test((el.textContent || "").trim()));
                  timestampText = (withTime?.textContent || "").trim();
                }
                let timestampFull = "";
                let t = node;
                for (let i = 0; i < 6 && t; i += 1) {
                  if (t.getAttribute && t.getAttribute("title")) {
                    timestampFull = (t.getAttribute("title") || "").trim();
                    if (timestampFull) break;
                  }
                  t = t.parentElement;
                }

                let dateGroup = "";
                let cursor = resolvedScope;
                for (let level = 0; level < 10 && cursor; level += 1) {
                  let prev = cursor.previousElementSibling;
                  while (prev) {
                    const txt = (prev.textContent || "").replace(/\\u00a0/g, " ").trim();
                    if (txt && dateRegex.test(txt) && !prev.querySelector("a[href^='/@']")) {
                      dateGroup = txt;
                      break;
                    }
                    prev = prev.previousElementSibling;
                  }
                  if (dateGroup) break;
                  cursor = cursor.parentElement;
                }

                const messageLinks = Array.from(node.querySelectorAll("a[href]"))
                  .map((a) => (a.getAttribute("href") || "").trim())
                  .filter(Boolean);

                messages.push({
                  author_username: ((authorLink?.getAttribute("href") || "").replace(/^\\/@/, "").split(/[?#]/)[0] || "").trim(),
                  author_display_name: (authorNameEl?.textContent || "").trim(),
                  timestamp_text: timestampText,
                  timestamp_full: timestampFull,
                  date_group: dateGroup,
                  message_text: text,
                  message_links: Array.from(new Set(messageLinks)),
                });
              }

              return { account, messages };
            }
            """
        )
    except Exception:
        return {"account": {}, "messages": []}
    if not isinstance(payload, dict):
        return {"account": {}, "messages": []}
    account_raw = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    messages_raw = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    normalized_messages: List[Dict[str, Any]] = []
    for item in messages_raw[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:]:
        if not isinstance(item, dict):
            continue
        normalized_messages.append(
            {
                "author_username": str(item.get("author_username") or "").strip(),
                "author_display_name": str(item.get("author_display_name") or "").strip(),
                "timestamp_text": str(item.get("timestamp_text") or "").strip(),
                "timestamp_full": str(item.get("timestamp_full") or "").strip(),
                "date_group": str(item.get("date_group") or "").strip(),
                "message_text": str(item.get("message_text") or "").strip(),
                "message_links": [str(link).strip() for link in (item.get("message_links") or []) if str(link).strip()],
            }
        )
    normalized_messages = [msg for msg in normalized_messages if msg["message_text"]]
    account = {
        "account_profile_link": str(account_raw.get("account_profile_link") or "").strip(),
        "account_display_name": str(account_raw.get("account_display_name") or "").strip(),
        "account_username": str(account_raw.get("account_username") or "").strip(),
        "account_status": str(account_raw.get("account_status") or "").strip(),
        "origin_group_name": str(account_raw.get("origin_group_name") or "").strip(),
        "origin_group_href": str(account_raw.get("origin_group_href") or "").strip(),
    }
    return {"account": account, "messages": normalized_messages}


def _normalize_skool_messages(
    message_rows: List[Dict[str, Any]],
    contact_name: str,
    account_username: str,
) -> List[Dict[str, str]]:
    normalized_contact = _clean_html_text(contact_name).lower()
    normalized_account_username = account_username.strip().lstrip("@").lower()
    items: List[Dict[str, str]] = []
    for item in message_rows[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:]:
        text = str(item.get("message_text") or "").strip()
        if not text:
            continue
        author_name = _clean_html_text(str(item.get("author_display_name") or ""))
        author_username = str(item.get("author_username") or "").strip().lstrip("@").lower()
        sender = "inbound"
        normalized_author_name = _clean_html_text(author_name).lower()
        if normalized_author_name and normalized_contact:
            sender = "inbound" if normalized_author_name == normalized_contact else "outbound"
        elif author_username and normalized_account_username:
            sender = "outbound" if author_username == normalized_account_username else "inbound"
        timestamp = str(item.get("timestamp_full") or item.get("timestamp_text") or now_display_time()).strip()
        items.append(
            {
                "text": text,
                "sender": sender,
                "timestamp": timestamp or now_display_time(),
            }
        )
    return items[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:]


def _extract_skool_api_message_text(message_payload: Any) -> str:
    if not isinstance(message_payload, dict):
        return ""
    metadata = message_payload.get("metadata")
    if isinstance(metadata, dict):
        text = str(metadata.get("content") or "").strip()
        if text:
            return text
    return str(message_payload.get("content") or "").strip()


def _build_skool_chat_targets_from_api_channels(channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        chat_id = str(channel.get("id") or "").strip()
        if not chat_id or chat_id in seen:
            continue
        seen.add(chat_id)

        user = channel.get("user") if isinstance(channel.get("user"), dict) else {}
        first_name = str(user.get("first_name") or "").strip()
        last_name = str(user.get("last_name") or "").strip()
        contact_name = f"{first_name} {last_name}".strip() or str(user.get("name") or "").strip()

        last_message = channel.get("last_message") if isinstance(channel.get("last_message"), dict) else {}
        preview_message = _extract_skool_api_message_text(last_message)
        preview_time = str(last_message.get("created_at") or "").strip() or str(channel.get("last_message_at") or "").strip()
        clr = str(last_message.get("id") or channel.get("last_message_id") or "").strip()
        target_url = f"https://www.skool.com/chat?ch={chat_id}"
        href = f"/chat?ch={chat_id}"
        if clr:
            target_url = f"{target_url}&clr={clr}"
            href = f"{href}&clr={clr}"

        targets.append(
            {
                "chat_id": chat_id,
                "target_url": target_url,
                "href": href,
                "clr": clr,
                "contact_name": contact_name,
                "preview_message": preview_message,
                "preview_time": preview_time,
                "origin_group_name": "",
                "origin_group_href": "",
            }
        )
    return targets


def _request_skool_api_json(context: Any, url: str, timeout_ms: int = 12000) -> tuple[bool, Any, int, str]:
    if not context:
        return False, None, 0, "missing_context"
    try:
        request_ctx = getattr(context, "request", None)
        if request_ctx is None:
            return False, None, 0, "missing_request_context"
        response = request_ctx.get(
            url,
            headers={"accept": "*/*"},
            timeout=max(4000, int(timeout_ms)),
            fail_on_status_code=False,
        )
        status = int(getattr(response, "status", 0) or 0)
        if status < 200 or status >= 300:
            body = ""
            try:
                body = str(response.text() or "")[:220]
            except Exception:
                body = ""
            return False, None, status, body
        try:
            payload = response.json()
        except Exception:
            payload = None
        return True, payload, status, ""
    except Exception as exc:
        return False, None, 0, str(exc)[:220]


def _fetch_skool_chat_targets_via_context_api(context: Any, max_chats: int) -> List[Dict[str, Any]]:
    channels: List[Dict[str, Any]] = []
    offset = 0
    page_limit = 30
    limit = min(page_limit, max(10, int(max_chats)))
    while len(channels) < max_chats:
        remaining = max_chats - len(channels)
        current_limit = min(limit, remaining)
        if current_limit <= 0:
            break
        url = (
            "https://api2.skool.com/self/chat-channels"
            f"?offset={offset}&limit={current_limit}&last=true&unread-only=false"
        )
        ok, payload, _, _ = _request_skool_api_json(context, url, timeout_ms=12000)
        if not ok:
            break
        data = payload if isinstance(payload, dict) else {}
        items = data.get("channels") if isinstance(data.get("channels"), list) else []
        if not items:
            break
        channels.extend([x for x in items if isinstance(x, dict)])
        if len(items) < current_limit:
            break
        offset += len(items)
    return _build_skool_chat_targets_from_api_channels(channels)[:max_chats]


def _fetch_skool_chat_detail_via_context_api(context: Any, chat_id: str, message_anchor_id: str = "") -> Dict[str, Any]:
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return {}

    def _run(params: str) -> Dict[str, Any]:
        url = f"https://api2.skool.com/channels/{chat_id}/messages?{params}"
        ok, payload, _, _ = _request_skool_api_json(context, url, timeout_ms=12000)
        if not ok:
            return {}
        return payload if isinstance(payload, dict) else {}

    window = max(1, int(SKOOL_CHAT_MESSAGE_WINDOW))
    base_params = f"before={window}&after={window}"
    if message_anchor_id:
        base_params += f"&msg={str(message_anchor_id).strip()}"
    best = _run(base_params)
    if not best:
        best = _run("before=1&after=0")
    messages = best.get("messages") if isinstance(best.get("messages"), list) else []
    if not messages:
        return best if isinstance(best, dict) else {}

    # page backward to collect full history quickly
    merged: Dict[str, Dict[str, Any]] = {}
    for item in messages:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        if mid:
            merged[mid] = item
    rounds = 0
    has_more_before = bool(best.get("has_more_before"))
    while has_more_before and rounds < max(4, int(SKOOL_CHAT_MESSAGE_PAGE_FETCH_LIMIT)) and len(merged) < SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:
        oldest = sorted(merged.values(), key=lambda x: str(x.get("created_at") or ""))[0]
        oldest_id = str(oldest.get("id") or "").strip()
        if not oldest_id:
            break
        data = _run(f"before={window}&after=0&msg={oldest_id}")
        page_msgs = data.get("messages") if isinstance(data.get("messages"), list) else []
        inserted = 0
        for item in page_msgs:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or "").strip()
            if not mid or mid in merged:
                continue
            merged[mid] = item
            inserted += 1
        has_more_before = bool(data.get("has_more_before"))
        rounds += 1
        if inserted <= 0:
            break
    best["messages"] = sorted(merged.values(), key=lambda x: str(x.get("created_at") or ""))[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:]
    return best


def _fetch_skool_chat_targets_via_api(page: Any, max_chats: int) -> List[Dict[str, Any]]:
    try:
        payload = page.evaluate(
            """
            async ({ maxChats }) => {
              try {
                const pageLimit = 30;
                const channels = [];
                let offset = 0;
                while (channels.length < maxChats) {
                  const remaining = maxChats - channels.length;
                  const limit = Math.min(pageLimit, remaining);
                  if (limit <= 0) break;
                  const url = `https://api2.skool.com/self/chat-channels?offset=${offset}&limit=${limit}&last=true&unread-only=false`;
                  const response = await fetch(url, {
                    method: "GET",
                    credentials: "include",
                    headers: { "accept": "*/*" },
                  });
                  if (!response.ok) {
                    const bodyText = await response.text().catch(() => "");
                    return { ok: false, error: `status=${response.status} body=${bodyText.slice(0, 180)}`, channels };
                  }
                  const data = await response.json().catch(() => ({}));
                  const pageChannels = Array.isArray(data?.channels) ? data.channels : [];
                  if (!pageChannels.length) break;
                  channels.push(...pageChannels);
                  if (pageChannels.length < limit) break;
                  offset += pageChannels.length;
                }
                return { ok: true, channels };
              } catch (e) {
                return { ok: false, error: String(e || "network error"), channels: [] };
              }
            }
            """,
            {"maxChats": max_chats},
        )
        if not isinstance(payload, dict):
            return []
        api_channels = payload.get("channels")
        if not isinstance(api_channels, list):
            return []
        return _build_skool_chat_targets_from_api_channels(api_channels)[:max_chats]
    except Exception:
        return []


def _probe_skool_chat_api_status(page: Any) -> Dict[str, Any]:
    try:
        payload = page.evaluate(
            """
            async () => {
              const readCookie = (name) => {
                const raw = document.cookie || "";
                const parts = raw.split(";").map((x) => x.trim());
                for (const part of parts) {
                  if (!part) continue;
                  const idx = part.indexOf("=");
                  if (idx <= 0) continue;
                  const k = part.slice(0, idx).trim();
                  if (k !== name) continue;
                  return decodeURIComponent(part.slice(idx + 1) || "");
                }
                return "";
              };
              const wafToken = readCookie("aws-waf-token");
              const headers = { "accept": "*/*" };
              if (wafToken) headers["x-aws-waf-token"] = wafToken;
              const url = "https://api2.skool.com/self/chat-channels?offset=0&limit=10&last=true&unread-only=false";
              const response = await fetch(url, {
                method: "GET",
                credentials: "include",
                headers,
              });
              const bodyText = await response.text().catch(() => "");
              return {
                ok: response.ok,
                status: response.status,
                body: String(bodyText || "").slice(0, 180),
              };
            }
            """
        )
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _extract_skool_group_name(group_payload: Any) -> str:
    if not isinstance(group_payload, dict):
        return ""
    metadata = group_payload.get("metadata")
    if isinstance(metadata, dict):
        display_name = str(metadata.get("display_name") or "").strip()
        if display_name:
            return display_name
    return str(group_payload.get("name") or "").strip()


def _extract_skool_group_href(group_payload: Any) -> str:
    if not isinstance(group_payload, dict):
        return ""
    raw_name = str(group_payload.get("name") or "").strip()
    if not raw_name:
        return ""
    if raw_name.startswith("/"):
        return raw_name
    return f"/{raw_name}"


def _normalize_skool_api_messages(
    messages_payload: Any,
    self_user_id: str,
    other_user_id: str,
) -> List[Dict[str, str]]:
    if not isinstance(messages_payload, list):
        return []
    sorted_payload = sorted(
        [item for item in messages_payload if isinstance(item, dict)],
        key=lambda item: str(item.get("created_at") or ""),
    )
    messages: List[Dict[str, str]] = []
    for item in sorted_payload[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        text = str(metadata.get("content") or item.get("content") or "").strip()
        if not text:
            continue
        remote_id = str(item.get("id") or "").strip()
        src = str(metadata.get("src") or "").strip()
        sender = "inbound"
        if self_user_id and src == self_user_id:
            sender = "outbound"
        elif other_user_id and src == other_user_id:
            sender = "inbound"
        timestamp = str(item.get("created_at") or "").strip() or now_display_time()
        messages.append(
            {
                "text": text,
                "sender": sender,
                "timestamp": timestamp,
                "remote_id": remote_id,
            }
        )
    return messages[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:]


def _dedupe_imported_messages(db: sqlite3.Connection, conversation_id: Optional[str] = None) -> int:
    where_clause = "newer.id LIKE ? AND older.id LIKE ?"
    params: List[Any] = [f"{SKOOL_CHAT_IMPORT_MESSAGE_PREFIX}%", f"{SKOOL_CHAT_IMPORT_MESSAGE_PREFIX}%"]
    if conversation_id:
        where_clause += " AND newer.conversationId = ? AND older.conversationId = ?"
        params.extend([conversation_id, conversation_id])

    delete_sql = f"""
        DELETE FROM messages
        WHERE rowid IN (
            SELECT newer.rowid
            FROM messages newer
            JOIN messages older
              ON newer.conversationId = older.conversationId
             AND newer.sender = older.sender
             AND newer.timestamp = older.timestamp
             AND newer.text = older.text
             AND older.rowid < newer.rowid
            WHERE {where_clause}
        )
    """
    before = int(db.total_changes)
    db.execute(delete_sql, params)
    return int(db.total_changes - before)


def _dedupe_adjacent_imported_messages(db: sqlite3.Connection, conversation_id: Optional[str] = None) -> int:
    query = """
        SELECT rowid, conversationId, sender, text, timestamp
        FROM messages
        WHERE isDeletedUi = 0
          AND conversationId LIKE ?
          AND id LIKE ?
    """
    params: List[Any] = [f"{SKOOL_CHAT_IMPORT_PREFIX}%", f"{SKOOL_CHAT_IMPORT_MESSAGE_PREFIX}%"]
    if conversation_id:
        query += " AND conversationId = ?"
        params.append(conversation_id)
    query += " ORDER BY conversationId, rowid"

    rows = db.execute(query, params).fetchall()
    to_delete: List[int] = []
    last_by_conversation: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        conv_id = str(row["conversationId"] or "")
        sender = str(row["sender"] or "")
        text = str(row["text"] or "").strip()
        if not conv_id or not text:
            continue
        prev = last_by_conversation.get(conv_id)
        if (
            prev
            and prev.get("sender") == sender
            and prev.get("text") == text
            and prev.get("timestamp") == str(row["timestamp"] or "")
            and int(row["rowid"] or 0) - int(prev.get("rowid") or 0) <= 3
        ):
            to_delete.append(int(row["rowid"]))
        else:
            last_by_conversation[conv_id] = {
                "rowid": int(row["rowid"] or 0),
                "sender": sender,
                "text": text,
                "timestamp": str(row["timestamp"] or ""),
            }
    if not to_delete:
        return 0
    db.executemany("DELETE FROM messages WHERE rowid = ?", [(rid,) for rid in to_delete])
    return len(to_delete)


def _prune_orphan_imported_conversations(db: sqlite3.Connection) -> int:
    rows = db.execute(
        """
        SELECT c.id
        FROM conversations c
        LEFT JOIN profiles p ON p.id = c.profileId
        WHERE c.id LIKE ? AND p.id IS NULL
        """,
        (f"{SKOOL_CHAT_IMPORT_PREFIX}%",),
    ).fetchall()
    stale_ids = [str(row["id"] or "").strip() for row in rows if str(row["id"] or "").strip()]
    if not stale_ids:
        return 0
    db.executemany("DELETE FROM messages WHERE conversationId = ?", [(conv_id,) for conv_id in stale_ids])
    db.executemany("DELETE FROM conversations WHERE id = ?", [(conv_id,) for conv_id in stale_ids])
    return len(stale_ids)


def _purge_imported_conversations_for_profile(db: sqlite3.Connection, profile_id: str) -> int:
    pid = str(profile_id or "").strip()
    if not pid:
        return 0
    rows = db.execute(
        "SELECT id FROM conversations WHERE profileId = ? AND id LIKE ?",
        (pid, f"{SKOOL_CHAT_IMPORT_PREFIX}%"),
    ).fetchall()
    conv_ids = [str(row["id"] or "").strip() for row in rows if str(row["id"] or "").strip()]
    if not conv_ids:
        return 0
    db.executemany("DELETE FROM messages WHERE conversationId = ?", [(conv_id,) for conv_id in conv_ids])
    db.executemany("DELETE FROM conversations WHERE id = ?", [(conv_id,) for conv_id in conv_ids])
    return len(conv_ids)


def _fetch_skool_chat_detail_via_api(page: Any, chat_id: str, message_anchor_id: str = "") -> Dict[str, Any]:
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return {}
    try:
        payload = page.evaluate(
            """
            async ({ chatId, messageAnchorId, messageWindow, maxMessages, maxPages }) => {
              const fetchJsonWithTimeout = async (url, timeoutMs = 12000) => {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), timeoutMs);
                try {
                  const response = await fetch(url, {
                    method: "GET",
                    credentials: "include",
                    headers: { "accept": "*/*" },
                    signal: controller.signal,
                  });
                  return response;
                } finally {
                  clearTimeout(timer);
                }
              };
              const run = async (params) => {
                const url = `https://api2.skool.com/channels/${chatId}/messages?${params.toString()}`;
                let response;
                try {
                  response = await fetchJsonWithTimeout(url, 12000);
                } catch (e) {
                  return { ok: false, error: String(e || "network_error") };
                }
                if (!response.ok) {
                  const bodyText = await response.text().catch(() => "");
                  return { ok: false, error: `status=${response.status} body=${bodyText.slice(0, 180)}` };
                }
                const data = await response.json().catch(() => ({}));
                return { ok: true, data };
              };

              const candidates = [];
              if (messageAnchorId) {
                const aroundAnchor = new URLSearchParams();
                aroundAnchor.set("before", String(messageWindow));
                aroundAnchor.set("after", String(messageWindow));
                aroundAnchor.set("msg", String(messageAnchorId));
                candidates.push(aroundAnchor);
              }

              // Default window around current chat cursor.
              const aroundCurrent = new URLSearchParams();
              aroundCurrent.set("before", String(messageWindow));
              aroundCurrent.set("after", String(messageWindow));
              candidates.push(aroundCurrent);

              // Fallback for sessions where msg/cursor mode is unavailable.
              const legacy = new URLSearchParams();
              legacy.set("before", "1");
              legacy.set("after", "0");
              candidates.push(legacy);

              let best = null;
              let bestCount = -1;
              let firstError = "";
              for (const params of candidates) {
                const result = await run(params);
                if (!result.ok) {
                  if (!firstError) firstError = String(result.error || "");
                  continue;
                }
                const data = result.data && typeof result.data === "object" ? result.data : {};
                const count = Array.isArray(data.messages) ? data.messages.length : 0;
                if (count > bestCount) {
                  best = data;
                  bestCount = count;
                }
              }

              if (!best) return { ok: false, error: firstError || "messages request failed" };

              const dedupeKey = (item) => {
                if (!item || typeof item !== "object") return "";
                const id = String(item.id || "").trim();
                if (id) return `id:${id}`;
                const md = (item.metadata && typeof item.metadata === "object") ? item.metadata : {};
                const createdAt = String(item.created_at || "").trim();
                const src = String(md.src || "").trim();
                const dst = String(md.dst || "").trim();
                const content = String(md.content || item.content || "").trim();
                return `fallback:${createdAt}|${src}|${dst}|${content}`;
              };

              const mergedMap = new Map();
              const mergeMessages = (list) => {
                let inserted = 0;
                if (!Array.isArray(list)) return inserted;
                for (const item of list) {
                  if (!item || typeof item !== "object") continue;
                  const key = dedupeKey(item);
                  if (!key || mergedMap.has(key)) continue;
                  mergedMap.set(key, item);
                  inserted += 1;
                }
                return inserted;
              };
              const toSortedList = () =>
                Array.from(mergedMap.values()).sort((a, b) =>
                  String(a?.created_at || "").localeCompare(String(b?.created_at || ""))
                );

              mergeMessages(Array.isArray(best.messages) ? best.messages : []);

              let hasMoreBefore = !!best.has_more_before;
              let hasMoreAfter = !!best.has_more_after;
              let beforeRounds = 0;
              let afterRounds = 0;

              // Walk backward in history.
              while (hasMoreBefore && beforeRounds < maxPages && mergedMap.size < maxMessages) {
                const current = toSortedList();
                const oldest = current.length ? String(current[0]?.id || "").trim() : "";
                if (!oldest) break;
                const params = new URLSearchParams();
                params.set("before", String(messageWindow));
                params.set("after", "0");
                params.set("msg", oldest);
                const result = await run(params);
                if (!result.ok) break;
                const data = (result.data && typeof result.data === "object") ? result.data : {};
                const pageMessages = Array.isArray(data.messages) ? data.messages : [];
                const inserted = mergeMessages(pageMessages);
                hasMoreBefore = !!data.has_more_before;
                beforeRounds += 1;
                if (!pageMessages.length || inserted <= 0) break;
              }

              // Walk forward in history.
              while (hasMoreAfter && afterRounds < maxPages && mergedMap.size < maxMessages) {
                const current = toSortedList();
                const newest = current.length ? String(current[current.length - 1]?.id || "").trim() : "";
                if (!newest) break;
                const params = new URLSearchParams();
                params.set("before", "0");
                params.set("after", String(messageWindow));
                params.set("msg", newest);
                const result = await run(params);
                if (!result.ok) break;
                const data = (result.data && typeof result.data === "object") ? result.data : {};
                const pageMessages = Array.isArray(data.messages) ? data.messages : [];
                const inserted = mergeMessages(pageMessages);
                hasMoreAfter = !!data.has_more_after;
                afterRounds += 1;
                if (!pageMessages.length || inserted <= 0) break;
              }

              const mergedMessages = toSortedList().slice(-Math.max(1, maxMessages));
              return {
                ok: true,
                data: {
                  ...best,
                  messages: mergedMessages,
                  has_more_before: hasMoreBefore,
                  has_more_after: hasMoreAfter,
                }
              };
            }
            """,
            {
                "chatId": chat_id,
                "messageAnchorId": str(message_anchor_id or "").strip(),
                "messageWindow": max(1, int(SKOOL_CHAT_MESSAGE_WINDOW)),
                "maxMessages": max(1, int(SKOOL_CHAT_MAX_MESSAGES_PER_CHAT)),
                "maxPages": max(4, int(SKOOL_CHAT_MESSAGE_PAGE_FETCH_LIMIT)),
            },
        )
    except Exception:
        return {}
    if not isinstance(payload, dict) or not payload.get("ok"):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _fetch_live_skool_chat_cards(
    profile_id: str,
    profile_name: str,
    proxy: Optional[str],
    expected_identities: Optional[List[str]] = None,
    known_profile_slugs: Optional[Set[str]] = None,
    cached_cards_by_chat: Optional[Dict[str, Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    if not PLAYWRIGHT_AVAILABLE:
        return [], "Playwright is not available on backend"

    browser_dir = Path(__file__).parent / "skool_accounts" / profile_id / "browser"

    if not browser_dir.exists():
        browser_dir.mkdir(parents=True, exist_ok=True)
        LOGGER.info(f"[FETCH] Created missing browser session directory for profile {profile_id}: {browser_dir}")

    last_error_message: Optional[str] = None
    nav_timeout_ms = SKOOL_CHAT_NAV_TIMEOUT_MS
    for attempt in range(1):
        slot_handle: Optional[Tuple[str, str]] = None
        try:
            slot_handle = acquire_proxy_slot("chat", proxy)
            with _PLAYWRIGHT_SYNC_LOCK:
                playwright = None
                context = None
                cards: List[Dict[str, Any]] = []
                try:
                    playwright = _start_playwright_safe()
                    launch_kwargs: Dict[str, Any] = {
                        "user_data_dir": str(browser_dir),
                        "headless": True,
                        "viewport": {"width": 1600, "height": 1100},
                        "args": [
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                        ],
                    }
                    proxy_cfg = _parse_proxy_to_playwright(proxy)
                    if proxy_cfg:
                        launch_kwargs["proxy"] = proxy_cfg
    
                    try:
                        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
                    except Exception as launch_exc:
                        launch_err = str(launch_exc)
                        recoverable = "connection closed while reading from the driver" in launch_err.lower()
                        if recoverable:
                            try:
                                playwright.stop()
                            except Exception:
                                pass
                            playwright = _start_playwright_safe()
                            context = playwright.chromium.launch_persistent_context(**launch_kwargs)
                        elif proxy_cfg:
                            # Proxy/browser crash fallback: retry once without proxy for this sync run.
                            fallback_kwargs = dict(launch_kwargs)
                            fallback_kwargs.pop("proxy", None)
                            context = playwright.chromium.launch_persistent_context(**fallback_kwargs)
                        else:
                            raise
                    page = context.pages[0] if context.pages else context.new_page()
                    page.set_default_timeout(12000)
                    page_ready = False
                    try:
                        nav_ok, _ = _goto_skool_entry_page(page, nav_timeout_ms)
                        page_ready = bool(nav_ok)
                    except Exception:
                        page_ready = False
                    expected_slug_candidates: List[str] = []
                    for raw_identity in (expected_identities or [profile_name]):
                        candidate = _slugify_profile_identity(raw_identity)
                        if candidate and candidate not in expected_slug_candidates:
                            expected_slug_candidates.append(candidate)
                    if expected_slug_candidates:
                        actual_slug = _extract_logged_in_profile_slug(page)
                        if actual_slug:
                            if actual_slug not in expected_slug_candidates:
                                if SKOOL_CHAT_STRICT_IDENTITY_CHECK:
                                    return [], (
                                        "Profile identity mismatch during chat sync "
                                        f"(expected=@{','.join(expected_slug_candidates)}, actual=@{actual_slug})"
                                    )
                                LOGGER.info(
                                    "Skool chat sync identity mismatch warning for profile '%s' (expected=%s, actual=@%s)",
                                    profile_id,
                                    ",".join(expected_slug_candidates),
                                    actual_slug,
                                )
                        elif SKOOL_CHAT_STRICT_IDENTITY_CHECK:
                            LOGGER.info(
                                "Skool chat sync identity verification skipped for profile '%s' (expected=%s, actual=unknown)",
                                profile_id,
                                ",".join(expected_slug_candidates),
                            )
                    chat_targets: List[Dict[str, Any]] = []
                    # Fast in-cycle retries to avoid false failures on transient popover/API load glitches.
                    for discover_attempt in range(3):
                        merged_targets: Dict[str, Dict[str, Any]] = {}
                        if page_ready:
                            for item in _collect_chat_targets_from_notification_button(page, SKOOL_CHAT_MAX_CHATS_PER_PROFILE):
                                chat_id = str(item.get("chat_id") or "").strip()
                                if chat_id:
                                    merged_targets[chat_id] = item
                        if page_ready:
                            for item in _fetch_skool_chat_targets_via_api(page, SKOOL_CHAT_MAX_CHATS_PER_PROFILE):
                                chat_id = str(item.get("chat_id") or "").strip()
                                if chat_id:
                                    merged_targets[chat_id] = item
                        for item in _fetch_skool_chat_targets_via_context_api(context, SKOOL_CHAT_MAX_CHATS_PER_PROFILE):
                            chat_id = str(item.get("chat_id") or "").strip()
                            if chat_id:
                                merged_targets[chat_id] = item
                        chat_targets = list(merged_targets.values())[:SKOOL_CHAT_MAX_CHATS_PER_PROFILE]
                        if chat_targets:
                            break
                        if page_ready and discover_attempt < 2:
                            try:
                                _goto_skool_entry_page(page, min(nav_timeout_ms, 15000))
                            except Exception:
                                pass
                            page.wait_for_timeout(500)
                    dropdown_opened = bool(chat_targets)
                    if not chat_targets:
                        current_url = page.url
                        dbg = _page_debug_state(page)
                        return [], (
                            "Could not discover chat links "
                            f"(url={current_url}, auth={dbg.get('auth_markers')}, links={dbg.get('chat_links')}, title={dbg.get('title')})"
                        )
        
                    detail_fetch_limit = SKOOL_CHAT_DETAIL_FETCH_LIMIT if SKOOL_CHAT_DETAIL_FETCH_LIMIT > 0 else len(chat_targets)
                    profile_fetch_deadline = time.monotonic() + max(
                        20.0,
                        float(SKOOL_CHAT_PROFILE_SYNC_TIMEOUT_SECONDS - 8),
                    )
                    for idx_target, target in enumerate(chat_targets):
                        chat_id = str(target.get("chat_id") or "").strip()
                        target_url = str(target.get("target_url") or "").strip() or f"https://www.skool.com/chat?ch={chat_id}"
                        if not chat_id:
                            continue
                        cached_card = (cached_cards_by_chat or {}).get(chat_id)
                        target_preview_message = str(target.get("preview_message") or "").strip()
                        target_preview_time = str(target.get("preview_time") or "").strip()
                        if (
                            cached_card
                            and isinstance(cached_card, dict)
                            and isinstance(cached_card.get("messages"), list)
                            and len(cached_card.get("messages") or []) > 0
                            and target_preview_message
                            and target_preview_time
                            and str(cached_card.get("message_text") or "").strip() == target_preview_message
                            and str(cached_card.get("last_message_time") or "").strip() == target_preview_time
                        ):
                            reused = dict(cached_card)
                            reused["profile_id"] = profile_id
                            reused["profile_name"] = profile_name
                            reused["chat_id"] = chat_id
                            # keep latest target URL flavor (user/clr params can change)
                            reused["post_url"] = str(cached_card.get("post_url") or "").strip() or target_url
                            cards.append(reused)
                            continue
                        extracted: Dict[str, Any] = {"account": {}, "messages": []}
                        chat_page_loaded = False
                        can_fetch_detail = (
                            idx_target < detail_fetch_limit
                            and time.monotonic() < profile_fetch_deadline
                        )
                        api_chat_payload: Dict[str, Any] = {}
                        if can_fetch_detail:
                            anchor = str(target.get("clr") or "").strip()
                            for detail_attempt in range(2):
                                api_chat_payload = _fetch_skool_chat_detail_via_api(page, chat_id, anchor)
                                messages_probe = (
                                    api_chat_payload.get("messages")
                                    if isinstance(api_chat_payload.get("messages"), list)
                                    else []
                                ) if isinstance(api_chat_payload, dict) else []
                                if api_chat_payload and len(messages_probe) > 1:
                                    break
                                if detail_attempt == 0:
                                    context_payload = _fetch_skool_chat_detail_via_context_api(context, chat_id, anchor)
                                    context_messages = (
                                        context_payload.get("messages")
                                        if isinstance(context_payload.get("messages"), list)
                                        else []
                                    ) if isinstance(context_payload, dict) else []
                                    if context_payload and len(context_messages) >= len(messages_probe):
                                        api_chat_payload = context_payload
                                        if len(context_messages) > 1:
                                            break
                                page.wait_for_timeout(250)
    
                        raw_messages_for_card: List[Dict[str, Any]] = []
                        account: Dict[str, Any] = {}
                        if api_chat_payload:
                            channel = api_chat_payload.get("channel") if isinstance(api_chat_payload.get("channel"), dict) else {}
                            channel_user = channel.get("user") if isinstance(channel.get("user"), dict) else {}
                            channel_meta = channel.get("metadata") if isinstance(channel.get("metadata"), dict) else {}
                            request_group = channel.get("request_group") if isinstance(channel.get("request_group"), dict) else {}
    
                            first_name = str(channel_user.get("first_name") or "").strip()
                            last_name = str(channel_user.get("last_name") or "").strip()
                            account_display_name = f"{first_name} {last_name}".strip() or str(channel_user.get("name") or "").strip()
                            account_username = str(channel_user.get("name") or "").strip()
                            account_status = str(channel_meta.get("other_user_tz") or "").strip()
                            origin_group_name = _extract_skool_group_name(request_group)
                            origin_group_href = _extract_skool_group_href(request_group)
    
                            account = {
                                "account_display_name": account_display_name,
                                "account_username": account_username,
                                "account_status": account_status,
                                "origin_group_name": origin_group_name,
                                "origin_group_href": origin_group_href,
                            }
                            raw_messages_for_card = (
                                api_chat_payload.get("messages")
                                if isinstance(api_chat_payload.get("messages"), list)
                                else []
                            )
                        if (
                            can_fetch_detail
                            and (not api_chat_payload or len(raw_messages_for_card) <= 1)
                            and (time.monotonic() + 8.0) < profile_fetch_deadline
                        ):
                            try:
                                page.goto(target_url, wait_until="domcontentloaded", timeout=12000)
                                if _wait_for_chat_view(page):
                                    chat_page_loaded = True
                                    page.wait_for_timeout(500)
                                    _scroll_chat_history_to_top(page)
                                    extracted = _extract_skool_chat_view(page)
                            except PlaywrightError as exc:
                                if "has been closed" in str(exc).lower():
                                    # Recover page for this profile and continue with next/this chat.
                                    try:
                                        page = context.new_page() if context else page
                                        page.set_default_timeout(12000)
                                        page.goto(target_url, wait_until="domcontentloaded", timeout=12000)
                                        if _wait_for_chat_view(page):
                                            chat_page_loaded = True
                                            page.wait_for_timeout(500)
                                            _scroll_chat_history_to_top(page)
                                            extracted = _extract_skool_chat_view(page)
                                    except Exception:
                                        pass
                                else:
                                    pass
                            except Exception:
                                pass
                            extracted_account = extracted.get("account") if isinstance(extracted.get("account"), dict) else {}
                            extracted_messages = extracted.get("messages") if isinstance(extracted.get("messages"), list) else []
                            if extracted_messages and len(extracted_messages) > len(raw_messages_for_card):
                                raw_messages_for_card = extracted_messages
                            if extracted_account:
                                merged_account = dict(extracted_account)
                                for key, value in account.items():
                                    if key not in merged_account or not str(merged_account.get(key) or "").strip():
                                        merged_account[key] = value
                                account = merged_account
    
                        account_display_name = str(account.get("account_display_name") or "").strip()
                        account_username = str(account.get("account_username") or "").strip()
                        account_status = str(account.get("account_status") or "").strip()
                        contact_name = str(target.get("contact_name") or "").strip() or account_display_name
                        origin_group_name = (
                            str(account.get("origin_group_name") or "").strip()
                            or str(target.get("origin_group_name") or "").strip()
                            or "Skool Inbox"
                        )
                        origin_group_name = _normalize_origin_group_name(origin_group_name) or "Skool Inbox"
                        origin_group_href = (
                            str(account.get("origin_group_href") or "").strip()
                            or str(target.get("origin_group_href") or "").strip()
                        )
                        origin_group_url = _abs_skool_url(origin_group_href) if origin_group_href else ""
    
                        messages: List[Dict[str, str]]
                        if api_chat_payload:
                            channel = api_chat_payload.get("channel") if isinstance(api_chat_payload.get("channel"), dict) else {}
                            channel_user = channel.get("user") if isinstance(channel.get("user"), dict) else {}
                            other_user_id = str(channel_user.get("id") or "").strip()
                            user_ids = channel.get("user_ids") if isinstance(channel.get("user_ids"), list) else []
                            self_user_id = ""
                            for uid in user_ids:
                                uid_str = str(uid or "").strip()
                                if uid_str and uid_str != other_user_id:
                                    self_user_id = uid_str
                                    break
                            messages = _normalize_skool_api_messages(
                                raw_messages_for_card,
                                self_user_id=self_user_id,
                                other_user_id=other_user_id,
                            )
                        else:
                            messages = []
                            if raw_messages_for_card:
                                messages = _normalize_skool_messages(
                                    message_rows=raw_messages_for_card,
                                    contact_name=contact_name,
                                    account_username=account_username,
                                )
                            if not messages and chat_page_loaded:
                                try:
                                    raw_fallback = _parse_skool_chat_messages(page.content(), contact_name)
                                    messages = raw_fallback[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:]
                                except PlaywrightError:
                                    # During shutdown/context recycle the page can close mid-parse; keep sync resilient.
                                    messages = []
    
                        last_message = messages[-1] if messages else None
                        message_text = (
                            (last_message or {}).get("text")
                            or str(target.get("preview_message") or "").strip()
                            or "New message in Skool chat"
                        )
                        last_message_time = (
                            (last_message or {}).get("timestamp")
                            or str(target.get("preview_time") or "").strip()
                            or now_display_time()
                        )
                        normalized_username = account_username.lstrip("@").strip()
                        post_url = target_url
                        if normalized_username and "user=" not in post_url:
                            joiner = "&" if "?" in post_url else "?"
                            post_url = f"{post_url}{joiner}user={normalized_username}"
        
                        cards.append(
                            {
                                "chat_id": chat_id,
                                "contact_name": contact_name or f"Skool Chat {chat_id[:6]}",
                                "account_username": account_username,
                                "account_status": account_status,
                                "message_text": message_text,
                                "last_message_time": last_message_time,
                                "post_url": post_url,
                                "profile_id": profile_id,
                                "profile_name": profile_name,
                                "origin_group_name": origin_group_name,
                                "origin_group_url": origin_group_url,
                                "messages": messages[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:],
                                "raw_messages": raw_messages_for_card,
                            }
                    )
                    return cards, None
                except PlaywrightError as exc:
                    err_text = str(exc)
                    err_lower = err_text.lower()
                    is_timeout = (
                        isinstance(exc, PlaywrightTimeoutError)
                        or "timeout" in err_lower
                        or "timed_out" in err_lower
                        or "err_timed_out" in err_lower
                    )
                    is_closed = (
                        "has been closed" in err_lower
                        or "target page, context or browser has been closed" in err_lower
                        or "targetclosederror" in err_lower
                    )
                    is_aborted = "err_aborted" in err_lower or "net::err_aborted" in err_lower
                    last_error_message = err_text[:220] or "unknown playwright error"
                    if is_aborted:
                        if cards:
                            return cards, None
                        return [], "Skool navigation aborted (browser/context interrupted)"
                    if is_timeout and attempt == 0:
                        continue
                    if is_closed and attempt == 0:
                        continue
                    if cards:
                        return cards, None
                    if is_timeout:
                        LOGGER.warning(
                            "Skool chat fetch timed out for profile '%s' (attempt %s/2): %s",
                            profile_id,
                            attempt + 1,
                            last_error_message,
                        )
                        return [], "Live DM sync failed: timed out while opening Skool page"
                    if is_closed:
                        LOGGER.info(
                            "Skool chat fetch interrupted for profile '%s' because browser context closed",
                            profile_id,
                        )
                        return [], "Skool navigation aborted (browser/context interrupted)"
                    LOGGER.exception("Failed to fetch live Skool chats for profile '%s'", profile_id)
                    return [], f"Live DM sync failed: {last_error_message}"
                except Exception as exc:
                    if cards:
                        return cards, None
                    err_text = str(exc)
                    err_lower = err_text.lower()
                    if "timeout" in err_lower or "timed_out" in err_lower or "err_timed_out" in err_lower:
                        LOGGER.warning(
                            "Skool chat fetch timed out for profile '%s' (attempt %s/2): %s",
                            profile_id,
                            attempt + 1,
                            err_text[:220] or "timeout",
                        )
                        return [], "Live DM sync failed: timed out while opening Skool page"
                    LOGGER.exception("Failed to fetch live Skool chats for profile '%s'", profile_id)
                    return [], f"Live DM sync failed: {str(exc)[:220] or 'unknown error'}"
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
        finally:
            release_proxy_slot(slot_handle)
    return [], f"Live DM sync failed: {last_error_message or 'target closed before page load'}"


def _fetch_live_skool_chat_cards_with_timeout(
    *,
    profile_id: str,
    profile_name: str,
    proxy: Optional[str],
    expected_identities: Optional[List[str]] = None,
    known_profile_slugs: Optional[Set[str]] = None,
    cached_cards_by_chat: Optional[Dict[str, Dict[str, Any]]] = None,
    timeout_seconds: int = SKOOL_CHAT_PROFILE_SYNC_TIMEOUT_SECONDS,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        # Called from sync worker thread already; avoid nested executor timeouts that can leak stuck workers.
        return _fetch_live_skool_chat_cards(
            profile_id,
            profile_name,
            proxy,
            expected_identities,
            known_profile_slugs,
            cached_cards_by_chat,
        )
    except Exception as exc:
        return [], f"Live DM sync failed: {str(exc)[:220] or 'unknown error'}"


def _load_skool_chat_cards_from_file(profile_rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    source = _find_skool_chat_source()
    if source is None:
        return []

    raw_html = _read_skool_chat_file(source)
    file_cards = _parse_skool_chat_cards(raw_html)
    if not file_cards:
        return []

    profile_row = profile_rows[0] if profile_rows else None
    profile_id = profile_row["id"] if profile_row else "imported-profile"
    profile_name = profile_row["name"] if profile_row else "Imported"

    cards: List[Dict[str, Any]] = []
    for card in file_cards:
        cards.append(
            {
                **card,
                "profile_id": profile_id,
                "profile_name": profile_name,
                "messages": [
                    {
                        "text": card.get("message_text") or "New message in Skool chat",
                        "sender": "inbound",
                        "timestamp": card.get("last_message_time") or now_display_time(),
                    }
                ],
            }
        )
    return cards


def _upsert_skool_chat_card(db: sqlite3.Connection, card: Dict[str, Any]) -> Optional[str]:
    chat_id = (card.get("chat_id") or "").strip()
    profile_id = (card.get("profile_id") or "imported-profile").strip()
    profile_name = (card.get("profile_name") or "Imported").strip()
    if not chat_id:
        return None

    conversation_id = f"{SKOOL_CHAT_IMPORT_PREFIX}{profile_id}-{chat_id}"
    existing = db.execute(
        "SELECT id, lastMessage, lastMessageTime, unread, keyword, originGroup, contactInfo, commentAttribution, keywordContext FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()

    raw_messages = card.get("messages") or []
    if not raw_messages:
        raw_messages = [{"text": str(card.get("message_text") or "New message in Skool chat"), "sender": "inbound", "timestamp": str(card.get("last_message_time") or now_display_time())}]

    message_text = str(card.get("message_text") or "New message in Skool chat")
    last_message_time = str(card.get("last_message_time") or now_display_time())
    post_url = str(card.get("post_url") or f"https://www.skool.com/chat?ch={chat_id}")
    origin_group_url = _abs_skool_url(str(card.get("origin_group_url") or "")) or ""
    origin_group = _normalize_origin_group_name(str(card.get("origin_group_name") or "").strip()) or "Skool Inbox"
    account_username = str(card.get("account_username") or "").strip()
    account_status = str(card.get("account_status") or "").strip()
    message_changed = not existing or (
        (existing["lastMessage"] or "") != message_text
        or (existing["lastMessageTime"] or "") != last_message_time
    )
    unread_value = 1 if message_changed else (existing["unread"] if existing else 1)

    first_message = raw_messages[0] if raw_messages else {}
    first_message_time = str((first_message or {}).get("timestamp") or last_message_time).strip() or last_message_time
    first_interaction = _format_first_interaction_date(first_message_time)
    original_comment = ""
    keyword_ctx = _pick_keyword_context(
        db=db,
        profile_id=profile_id,
        origin_group=origin_group,
        message_text=message_text,
        messages=raw_messages,
    )

    existing_contact_info = parse_json_field(str(existing["contactInfo"] if existing else ""), {}) if existing else {}
    existing_attribution = parse_json_field(str(existing["commentAttribution"] if existing else ""), {}) if existing else {}
    existing_keyword_context = parse_json_field(str(existing["keywordContext"] if existing else ""), {}) if existing else {}
    existing_keyword = str(existing["keyword"] or "").strip() if existing else ""

    should_apply_event_attribution = (
        not existing
        or _is_generic_chat_metadata(existing_keyword, existing_attribution, existing_keyword_context)
    )
    comment_event = (
        _find_comment_event_for_chat(
            db=db,
            profile_id=profile_id,
            origin_group=origin_group,
            origin_group_url=origin_group_url,
            last_message_time=last_message_time,
        )
        if should_apply_event_attribution
        else None
    )

    if existing:
        existing_origin_group = str(existing["originGroup"] or "").strip()
        if origin_group == "Skool Inbox" and existing_origin_group and existing_origin_group != "Skool Inbox":
            origin_group = existing_origin_group

        if keyword_ctx.get("isFallback"):
            if existing_keyword and existing_keyword.lower() != "chat":
                keyword_ctx["keyword"] = existing_keyword
            existing_persona = str(existing_keyword_context.get("persona") or "").strip()
            if existing_persona and existing_persona.lower() != "chat_import":
                keyword_ctx["persona"] = existing_persona
            existing_prompt = str(existing_keyword_context.get("promptPreview") or "").strip()
            if existing_prompt and "Imported from live Skool inbox" not in existing_prompt:
                keyword_ctx["promptPreview"] = existing_prompt

        existing_original = str(existing_attribution.get("originalComment") or "").strip()
        if (
            existing_original
            and existing_original != "—"
            and not _is_generic_chat_metadata(existing_keyword, existing_attribution, existing_keyword_context)
        ):
            original_comment = existing_original
        if not origin_group_url:
            origin_group_url = _abs_skool_url(str(existing_attribution.get("postUrl") or ""))
        if not first_message_time:
            first_message_time = str(existing_attribution.get("timestamp") or "").strip() or last_message_time
        if not first_interaction:
            preserved_first = str(existing_contact_info.get("firstInteraction") or "").strip()
            if preserved_first:
                first_interaction = preserved_first

    if comment_event:
        event_keyword = str(comment_event.get("keyword") or "").strip()
        event_prompt = str(comment_event.get("prompt") or "").strip()
        event_comment = str(comment_event.get("commentText") or "").strip()
        event_post_url = str(comment_event.get("postUrl") or "").strip()
        event_ts = str(comment_event.get("createdAt") or "").strip()
        if event_keyword:
            keyword_ctx["keyword"] = event_keyword
        if event_prompt:
            keyword_ctx["promptPreview"] = event_prompt
        keyword_ctx["persona"] = "comment_trigger"
        if event_comment:
            original_comment = event_comment
        if event_post_url:
            origin_group_url = event_post_url
        if event_ts:
            first_message_time = event_ts
        if not first_interaction:
            first_interaction = _format_first_interaction_date(first_message_time)

    if not str(keyword_ctx.get("keyword") or "").strip():
        keyword_ctx["keyword"] = "—"
    if keyword_ctx.get("keyword") == "chat":
        keyword_ctx["keyword"] = "—"
    if not str(keyword_ctx.get("promptPreview") or "").strip() or "Imported from live Skool inbox" in str(keyword_ctx.get("promptPreview") or ""):
        keyword_ctx["promptPreview"] = "—"

    if not original_comment:
        original_comment = "—"
    if original_comment.strip() == "":
        original_comment = "—"
    if not origin_group_url or "/chat?ch=" in origin_group_url:
        origin_group_url = ""

    if not first_interaction:
        first_interaction = _format_first_interaction_date(last_message_time) or "Imported from Skool Inbox sync"

    contact_info = json.dumps({"firstInteraction": first_interaction}, ensure_ascii=False)
    attribution = json.dumps(
        {
            "postUrl": origin_group_url or "—",
            "originalComment": original_comment,
            "timestamp": first_message_time,
            "postTitle": origin_group if origin_group and origin_group != "Skool Inbox" else "—",
        },
        ensure_ascii=False,
    )
    keyword_context = json.dumps(
        {
            "persona": keyword_ctx["persona"],
            "promptPreview": keyword_ctx["promptPreview"],
        },
        ensure_ascii=False,
    )

    if existing:
        db.execute(
            """
            UPDATE conversations
            SET contactName = ?, profileId = ?, profileName = ?, keyword = ?, originGroup = ?,
                lastMessage = ?, lastMessageTime = ?, unread = ?, contactInfo = ?, commentAttribution = ?, keywordContext = ?
            WHERE id = ?
            """,
            (
                str(card.get("contact_name") or f"Skool Chat {chat_id[:6]}"),
                profile_id,
                profile_name,
                keyword_ctx["keyword"],
                origin_group,
                message_text,
                last_message_time,
                unread_value,
                contact_info,
                attribution,
                keyword_context,
                conversation_id,
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO conversations (
                id, contactName, profileId, profileName, keyword, originGroup, lastMessage, lastMessageTime,
                unread, labelId, isArchived, isDeletedUi, aiAutoEnabled, contactInfo, commentAttribution, keywordContext
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 0, 0, ?, ?, ?)
            """,
            (
                conversation_id,
                str(card.get("contact_name") or f"Skool Chat {chat_id[:6]}"),
                profile_id,
                profile_name,
                keyword_ctx["keyword"],
                origin_group,
                message_text,
                last_message_time,
                unread_value,
                contact_info,
                attribution,
                keyword_context,
            ),
        )

    # Replace imported message snapshot for this conversation to avoid stale/incorrect carry-over
    # from previous fallback parses when API detail was unavailable.
    db.execute("DELETE FROM messages WHERE conversationId = ?", (conversation_id,))

    for item in raw_messages:
        msg_text = str(item.get("text") or "").strip()
        if not msg_text:
            continue
        msg_sender = str(item.get("sender") or "inbound").lower()
        if msg_sender not in {"inbound", "outbound"}:
            msg_sender = "inbound"
        msg_time = str(item.get("timestamp") or last_message_time)
        remote_id = str(item.get("remote_id") or item.get("id") or "").strip()
        if remote_id:
            message_key = f"{conversation_id}|remote|{remote_id}"
        else:
            message_key = f"{conversation_id}|{msg_sender}|{msg_time}|{msg_text}"
        message_id = f"{SKOOL_CHAT_IMPORT_MESSAGE_PREFIX}{uuid.uuid5(uuid.NAMESPACE_URL, message_key)}"
        db.execute(
            "INSERT OR IGNORE INTO messages (id, conversationId, text, sender, timestamp, isDeletedUi) VALUES (?, ?, ?, ?, ?, 0)",
            (message_id, conversation_id, msg_text, msg_sender, msg_time),
        )
    _dedupe_imported_messages(db, conversation_id)
    _dedupe_adjacent_imported_messages(db, conversation_id)

    _try_ai_auto_reply(
        db=db,
        conversation_id=conversation_id,
        require_message_changed=True,
        message_changed=message_changed,
        chat_id_hint=chat_id,
        trigger_reason="sync_new_inbound",
    )
    return conversation_id


def _extract_skool_chat_ids(conversation_id: str, post_url: str) -> tuple[Optional[str], Optional[str]]:
    profile_id: Optional[str] = None
    chat_id: Optional[str] = None
    if conversation_id.startswith(SKOOL_CHAT_IMPORT_PREFIX):
        suffix = conversation_id[len(SKOOL_CHAT_IMPORT_PREFIX):]
        if "-" in suffix:
            profile_id, chat_id = suffix.rsplit("-", 1)
    if post_url and not chat_id:
        try:
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(post_url)
            chat_id = (parse_qs(parsed.query).get("ch") or [None])[0]
        except Exception:
            pass
    return (
        profile_id.strip() if isinstance(profile_id, str) and profile_id.strip() else None,
        chat_id.strip() if isinstance(chat_id, str) and chat_id.strip() else None,
    )


def _try_ai_auto_reply(
    db: sqlite3.Connection,
    conversation_id: str,
    *,
    require_message_changed: bool,
    message_changed: bool,
    chat_id_hint: str = "",
    trigger_reason: str = "unknown",
) -> bool:
    try:
        conversation_row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not conversation_row:
            return False
        if not bool(conversation_row["aiAutoEnabled"]):
            return False
        if bool(conversation_row["isArchived"]) or bool(conversation_row["isDeletedUi"]):
            return False
        if require_message_changed and not message_changed:
            return False

        latest_row = db.execute(
            """
            SELECT sender, text
            FROM messages
            WHERE conversationId = ? AND isDeletedUi = 0
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        latest_sender = str((latest_row["sender"] if latest_row else "") or "").strip().lower()
        if latest_sender != "inbound":
            return False

        ai_reply = _generate_conversation_ai_suggest(db, conversation_id, "Friendly")
        profile_name = str(conversation_row["profileName"] or "SYSTEM")
        contact_name = str(conversation_row["contactName"] or "").strip() or "contact"
        if str(ai_reply.source or "").startswith("suggest_only_no_send"):
            _insert_backend_log(
                db,
                profile_name,
                "info",
                f"AI Auto ({trigger_reason}): suggest-only mode (no keyword DM rule and no global DM fallback), skipping send",
            )
            return False

        reply_text = str(ai_reply.text or "").strip()
        if not reply_text:
            _insert_backend_log(db, profile_name, "retry", f"AI Auto ({trigger_reason}): generated empty reply, skipping send")
            return False
        if not _reserve_skool_send_dedupe(conversation_id, reply_text, ttl_seconds=180.0):
            _insert_backend_log(
                db,
                profile_name,
                "info",
                f"AI Auto ({trigger_reason}): duplicate send suppressed for {contact_name}",
            )
            return False

        profile_id = str(conversation_row["profileId"] or "").strip()
        comment_attribution = parse_json_field(str(conversation_row["commentAttribution"] or ""), {})
        post_url = str(comment_attribution.get("postUrl") or "")
        derived_profile_id, derived_chat_id = _extract_skool_chat_ids(conversation_id, post_url)
        if not profile_id:
            profile_id = str(derived_profile_id or "").strip()
        chat_id_value = str(derived_chat_id or "").strip() or str(chat_id_hint or "").strip()
        if not profile_id or not chat_id_value:
            _insert_backend_log(db, profile_name, "error", f"AI Auto ({trigger_reason}): missing profile/chat id, skipping send")
            return False

        profile_row = db.execute("SELECT id, name, proxy FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if not profile_row:
            _insert_backend_log(db, profile_name, "error", f"AI Auto ({trigger_reason}): profile session missing, skipping send")
            return False

        _insert_backend_log(
            db,
            str(profile_row["name"] or profile_name),
            "info",
            f"AI Auto ({trigger_reason}): sending reply to {contact_name}",
        )
        sent_card = _send_message_to_skool_chat(
            profile_id=str(profile_row["id"]),
            profile_name=str(profile_row["name"]),
            proxy=str(profile_row["proxy"] or ""),
            chat_id=chat_id_value,
            text=reply_text,
            contact_name=str(conversation_row["contactName"] or ""),
        )
        if not sent_card:
            _release_skool_send_dedupe(conversation_id, reply_text)
            _insert_backend_log(db, str(profile_row["name"] or profile_name), "error", f"AI Auto ({trigger_reason}): failed to send reply")
            return False

        _upsert_skool_chat_card(db, sent_card)
        _update_live_cache_with_skool_card(sent_card)
        db.execute("UPDATE conversations SET unread = 0 WHERE id = ?", (conversation_id,))
        _insert_backend_log(
            db,
            str(profile_row["name"] or profile_name),
            "success",
            f"AI Auto ({trigger_reason}): reply sent to {contact_name}",
        )
        try:
            origin_group = str(conversation_row["originGroup"] or "Skool Inbox")
            activity_post_url = (
                f"https://www.skool.com/chat?ch={chat_id_value}"
                if chat_id_value
                else (str(comment_attribution.get("postUrl") or "").strip() or "https://www.skool.com/")
            )
            activity_profile = str(profile_row["name"] or profile_name or "SYSTEM")
            activity_action = f"DM sent to {contact_name}"
            existing_activity = db.execute(
                """
                SELECT 1
                FROM activity_feed
                WHERE profile = ? AND action = ? AND postUrl = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (activity_profile, activity_action, activity_post_url),
            ).fetchone()
            if not existing_activity:
                db.execute(
                    "INSERT INTO activity_feed (id, profile, groupName, action, timestamp, postUrl) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        activity_profile,
                        origin_group,
                        activity_action,
                        datetime.now(timezone.utc).isoformat(),
                        activity_post_url,
                    ),
                )
        except Exception:
            pass
        return True
    except Exception:
        LOGGER.exception("AI Auto failed for conversation '%s' (%s)", conversation_id, trigger_reason)
        return False


def _backfill_dm_activity_from_logs(db: sqlite3.Connection, *, limit: int = 5000) -> Dict[str, int]:
    pattern_ai_auto_sent = re.compile(r"AI Auto \([^)]*\): reply sent to\s+(.+)$", re.IGNORECASE)
    pattern_dm_sent = re.compile(r"DM sent to\s+(.+)$", re.IGNORECASE)

    rows = db.execute(
        """
        SELECT rowid, timestamp, profile, message
        FROM logs
        WHERE lower(message) LIKE '%reply sent to %' OR lower(message) LIKE '%dm sent to %'
        ORDER BY rowid DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()

    inserted = 0
    skipped_existing = 0
    skipped_unparsed = 0
    scanned = 0

    # Process older -> newer to keep feed chronology stable when backfilling.
    for row in reversed(rows):
        scanned += 1
        profile = str(row["profile"] or "SYSTEM").strip() or "SYSTEM"
        timestamp = str(row["timestamp"] or "").strip() or datetime.now(timezone.utc).isoformat()
        message = str(row["message"] or "").strip()

        match = pattern_ai_auto_sent.search(message) or pattern_dm_sent.search(message)
        if not match:
            skipped_unparsed += 1
            continue
        contact_name = str(match.group(1) or "").strip() or "contact"
        action = f"DM sent to {contact_name}"

        existing = db.execute(
            """
            SELECT 1
            FROM activity_feed
            WHERE profile = ? AND action = ? AND timestamp = ?
            LIMIT 1
            """,
            (profile, action, timestamp),
        ).fetchone()
        if existing:
            skipped_existing += 1
            continue

        conv = db.execute(
            """
            SELECT id, originGroup, commentAttribution
            FROM conversations
            WHERE profileName = ? AND contactName = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (profile, contact_name),
        ).fetchone()

        group_name = "Skool Inbox"
        post_url = "https://www.skool.com/"
        if conv:
            group_name = str(conv["originGroup"] or "").strip() or "Skool Inbox"
            raw_attr = str(conv["commentAttribution"] or "").strip()
            if raw_attr:
                try:
                    parsed_attr = json.loads(raw_attr)
                except Exception:
                    parsed_attr = {}
                post_from_attr = str((parsed_attr or {}).get("postUrl") or "").strip()
                if post_from_attr:
                    post_url = post_from_attr
            conv_id = str(conv["id"] or "").strip()
            if conv_id.startswith(SKOOL_CHAT_IMPORT_PREFIX):
                suffix = conv_id[len(SKOOL_CHAT_IMPORT_PREFIX):]
                if "-" in suffix:
                    chat_id = suffix.rsplit("-", 1)[-1].strip()
                    if chat_id:
                        post_url = f"https://www.skool.com/chat?ch={chat_id}"

        db.execute(
            "INSERT INTO activity_feed (id, profile, groupName, action, timestamp, postUrl) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), profile, group_name, action, timestamp, post_url),
        )
        inserted += 1

    if inserted > 0:
        db.commit()

    return {
        "scanned": scanned,
        "inserted": inserted,
        "skippedExisting": skipped_existing,
        "skippedUnparsed": skipped_unparsed,
    }


def _normalize_match_url(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text)
    text = text.split("#", 1)[0]
    text = text.rstrip("/")
    return text


def _is_generic_chat_metadata(existing_keyword: str, existing_attribution: Dict[str, Any], existing_keyword_context: Dict[str, Any]) -> bool:
    keyword = str(existing_keyword or "").strip().lower()
    prompt_preview = str(existing_keyword_context.get("promptPreview") or "").strip().lower()
    post_url = str(existing_attribution.get("postUrl") or "").strip().lower()
    post_title = str(existing_attribution.get("postTitle") or "").strip().lower()
    if keyword in {"", "chat"}:
        return True
    if "imported from live skool inbox" in prompt_preview:
        return True
    if "/chat?ch=" in post_url:
        return True
    if post_title in {"", "skool inbox"}:
        return True
    return False


def _find_comment_event_for_chat(
    db: sqlite3.Connection,
    profile_id: str,
    origin_group: str,
    origin_group_url: str,
    last_message_time: str,
) -> Optional[Dict[str, Any]]:
    if not profile_id:
        return None
    try:
        rows = db.execute(
            """
            SELECT profileId, profile, community, postUrl, keyword, prompt, commentText, createdAt
            FROM automation_comment_events
            WHERE profileId = ?
            ORDER BY rowid DESC
            LIMIT 300
            """,
            (profile_id,),
        ).fetchall()
    except Exception:
        return None
    if not rows:
        return None

    group_l = str(origin_group or "").strip().lower()
    group_url_n = _normalize_match_url(origin_group_url)
    chat_dt = _parse_chat_datetime(last_message_time) or _parse_analytics_datetime(last_message_time)

    best: Optional[sqlite3.Row] = None
    best_score = -1e9
    for row in rows:
        event_post = str(row["postUrl"] or "").strip()
        if not event_post:
            continue
        score = 0.0

        community = str(row["community"] or "").strip()
        community_l = community.lower()
        community_n = _normalize_match_url(community)
        if group_url_n and community_n and (group_url_n in community_n or community_n in group_url_n):
            score += 8.0
        if group_l and community_l and (group_l in community_l or community_l in group_l):
            score += 4.0
        if str(row["keyword"] or "").strip():
            score += 0.5
        if str(row["prompt"] or "").strip():
            score += 0.5

        event_dt = _parse_chat_datetime(str(row["createdAt"] or "")) or _parse_analytics_datetime(str(row["createdAt"] or ""))
        if chat_dt and event_dt:
            # Compare via timestamps to avoid naive/aware timezone subtraction errors.
            chat_ts = _safe_datetime_timestamp(chat_dt)
            event_ts = _safe_datetime_timestamp(event_dt)
            if chat_ts and event_ts:
                delta_hours = (chat_ts - event_ts) / 3600.0
            else:
                delta_hours = 0.0
            if delta_hours < -24:
                score -= 100.0
            elif delta_hours < 0:
                score -= 2.0
            elif delta_hours <= 24 * 14:
                score += max(0.0, 6.0 - (delta_hours / 24.0))
            else:
                score -= min(6.0, (delta_hours - 24 * 14) / 24.0)
        elif event_dt:
            score += max(0.0, min(2.0, _safe_datetime_timestamp(event_dt) / 1e10))

        if score > best_score:
            best_score = score
            best = row

    if best is None or best_score < 2.0:
        return None
    return {
        "postUrl": str(best["postUrl"] or "").strip(),
        "keyword": str(best["keyword"] or "").strip(),
        "prompt": str(best["prompt"] or "").strip(),
        "commentText": str(best["commentText"] or "").strip(),
        "createdAt": str(best["createdAt"] or "").strip(),
    }


def _normalize_legacy_chat_metadata(db: sqlite3.Connection) -> int:
    rows = db.execute(
        "SELECT id, keyword, commentAttribution, keywordContext FROM conversations WHERE id LIKE ?",
        (f"{SKOOL_CHAT_IMPORT_PREFIX}%",),
    ).fetchall()
    changed = 0
    for row in rows:
        conv_id = str(row["id"] or "").strip()
        if not conv_id:
            continue
        attribution = parse_json_field(str(row["commentAttribution"] or ""), {})
        keyword_ctx = parse_json_field(str(row["keywordContext"] or ""), {})
        keyword = str(row["keyword"] or "").strip()

        post_url = str((attribution or {}).get("postUrl") or "").strip()
        original_comment = str((attribution or {}).get("originalComment") or "").strip()
        prompt_preview = str((keyword_ctx or {}).get("promptPreview") or "").strip()

        generic_keyword = keyword.lower() in {"", "chat", "—"}
        generic_prompt = (not prompt_preview) or (prompt_preview == "—") or ("imported from live skool inbox" in prompt_preview.lower())
        generic_post = (not post_url) or (post_url == "—") or ("/chat?ch=" in post_url.lower())
        generic_original = (not original_comment) or (original_comment == "—")

        if not (generic_keyword or generic_prompt or generic_post or generic_original):
            continue

        updated_keyword = "—" if generic_keyword else keyword
        updated_prompt = "—" if generic_prompt else prompt_preview
        updated_post_url = "—" if generic_post else post_url
        updated_original = "—" if (generic_original or generic_keyword or generic_prompt or generic_post) else original_comment
        updated_title = str((attribution or {}).get("postTitle") or "").strip()
        if not updated_title or updated_title.lower() == "skool inbox":
            updated_title = "—"

        db.execute(
            "UPDATE conversations SET keyword = ?, commentAttribution = ?, keywordContext = ? WHERE id = ?",
            (
                updated_keyword,
                json.dumps(
                    {
                        "postUrl": updated_post_url,
                        "originalComment": updated_original,
                        "timestamp": str((attribution or {}).get("timestamp") or "").strip(),
                        "postTitle": updated_title,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "persona": str((keyword_ctx or {}).get("persona") or "").strip(),
                        "promptPreview": updated_prompt,
                    },
                    ensure_ascii=False,
                ),
                conv_id,
            ),
        )
        changed += 1
    return changed


def _conversation_chat_identity(row: sqlite3.Row) -> Optional[Tuple[str, str]]:
    conversation_id = str(row["id"] or "").strip()
    profile_id = str(row["profileId"] or "").strip()
    post_url = ""
    try:
        attribution = parse_json_field(str(row["commentAttribution"] or ""), {})
        if isinstance(attribution, dict):
            post_url = str(attribution.get("postUrl") or "").strip()
    except Exception:
        post_url = ""
    parsed_profile_id, chat_id = _extract_skool_chat_ids(conversation_id, post_url)
    resolved_profile_id = (parsed_profile_id or profile_id).strip()
    resolved_chat_id = str(chat_id or "").strip()
    if not resolved_profile_id or not resolved_chat_id:
        return None
    return (resolved_profile_id, resolved_chat_id)


def _normalize_live_cards_cache(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for card in cards:
        profile_id = str(card.get("profile_id") or "").strip()
        chat_id = str(card.get("chat_id") or "").strip()
        if not profile_id or not chat_id:
            continue
        key = (profile_id, chat_id)
        prev = deduped.get(key)
        if prev is None:
            deduped[key] = card
            continue
        prev_dt = _parse_chat_datetime(str(prev.get("last_message_time") or "")) or _parse_analytics_datetime(str(prev.get("last_message_time") or ""))
        cur_dt = _parse_chat_datetime(str(card.get("last_message_time") or "")) or _parse_analytics_datetime(str(card.get("last_message_time") or ""))
        prev_ts = _safe_datetime_timestamp(prev_dt)
        cur_ts = _safe_datetime_timestamp(cur_dt)
        if cur_ts >= prev_ts:
            deduped[key] = card

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for (profile_id, _), card in deduped.items():
        grouped.setdefault(profile_id, []).append(card)

    normalized: List[Dict[str, Any]] = []
    for profile_id, items in grouped.items():
        ranked = sorted(
            items,
            key=lambda item: _safe_datetime_timestamp(
                _parse_chat_datetime(str(item.get("last_message_time") or ""))
                or _parse_analytics_datetime(str(item.get("last_message_time") or ""))
            ),
            reverse=True,
        )
        normalized.extend(ranked[:SKOOL_CHAT_MAX_CHATS_PER_PROFILE])
    return normalized


def _dedupe_imported_conversations(db: sqlite3.Connection) -> int:
    rows = db.execute(
        "SELECT rowid, id, profileId, lastMessageTime, commentAttribution FROM conversations WHERE id LIKE ?",
        (f"{SKOOL_CHAT_IMPORT_PREFIX}%",),
    ).fetchall()
    grouped: Dict[Tuple[str, str], List[sqlite3.Row]] = {}
    for row in rows:
        identity = _conversation_chat_identity(row)
        if identity is None:
            continue
        grouped.setdefault(identity, []).append(row)

    deduped = 0
    for _, group_rows in grouped.items():
        if len(group_rows) <= 1:
            continue
        ranked = sorted(
            group_rows,
            key=lambda item: (
                _safe_datetime_timestamp(
                    _parse_chat_datetime(str(item["lastMessageTime"] or ""))
                    or _parse_analytics_datetime(str(item["lastMessageTime"] or ""))
                ),
                int(item["rowid"] or 0),
            ),
            reverse=True,
        )
        keeper_id = str(ranked[0]["id"])
        for duplicate in ranked[1:]:
            dup_id = str(duplicate["id"])
            if not dup_id or dup_id == keeper_id:
                continue
            db.execute("UPDATE messages SET conversationId = ? WHERE conversationId = ?", (keeper_id, dup_id))
            db.execute("DELETE FROM conversations WHERE id = ?", (dup_id,))
            deduped += 1

    if deduped > 0:
        _dedupe_imported_messages(db)
    return deduped


def _update_live_cache_with_skool_card(card: Dict[str, Any]) -> None:
    chat_id = str(card.get("chat_id") or "").strip()
    profile_id = str(card.get("profile_id") or "").strip()
    if not chat_id or not profile_id:
        return
    current_cards = list(_SKOOL_CHAT_IMPORT_CACHE.get("live_cards") or [])
    replaced = False
    for idx, existing in enumerate(current_cards):
        if (
            str(existing.get("chat_id") or "").strip() == chat_id
            and str(existing.get("profile_id") or "").strip() == profile_id
        ):
            current_cards[idx] = card
            replaced = True
            break
    if not replaced:
        current_cards.insert(0, card)
    _SKOOL_CHAT_IMPORT_CACHE["live_cards"] = _normalize_live_cards_cache(current_cards)
    _SKOOL_CHAT_IMPORT_CACHE["live_synced_at"] = time.time()


def _send_message_to_skool_chat(
    profile_id: str,
    profile_name: str,
    proxy: Optional[str],
    chat_id: str,
    text: str,
    contact_name: str = "",
) -> Optional[Dict[str, Any]]:
    if not PLAYWRIGHT_AVAILABLE:
        return None

    browser_dir = Path(__file__).parent / "skool_accounts" / profile_id / "browser"
    if not browser_dir.exists():
        return None

    slot_handle: Optional[Tuple[str, str]] = None
    try:
        slot_handle = acquire_proxy_slot("chat", proxy)
        with _PLAYWRIGHT_SYNC_LOCK:
            playwright = None
            context = None
            try:
                playwright = _start_playwright_safe()
                launch_kwargs: Dict[str, Any] = {
                    "user_data_dir": str(browser_dir),
                    "headless": True,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                }
                proxy_cfg = _parse_proxy_to_playwright(proxy)
                if proxy_cfg:
                    launch_kwargs["proxy"] = proxy_cfg
    
                try:
                    context = playwright.chromium.launch_persistent_context(**launch_kwargs)
                except Exception as launch_exc:
                    launch_err = str(launch_exc)
                    recoverable = "connection closed while reading from the driver" in launch_err.lower()
                    if recoverable:
                        try:
                            playwright.stop()
                        except Exception:
                            pass
                        playwright = _start_playwright_safe()
                        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
                    elif proxy_cfg:
                        fallback_kwargs = dict(launch_kwargs)
                        fallback_kwargs.pop("proxy", None)
                        context = playwright.chromium.launch_persistent_context(**fallback_kwargs)
                    else:
                        raise
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(15000)
                try:
                    _goto_skool_entry_page(page, min(SKOOL_CHAT_NAV_TIMEOUT_MS, 15000))
                except Exception:
                    pass
                if "/login" in str(page.url or "").lower():
                    return None
    
                sent = False
                send_url = f"https://api2.skool.com/channels/{chat_id}/messages?ct=wdm"
                send_body = {"content": text, "attachments": []}
    
                # Primary path: send directly via authenticated request context (no chat navigation required).
                try:
                    response = context.request.post(
                        send_url,
                        data=send_body,
                        headers={"accept": "*/*", "content-type": "application/json"},
                        timeout=18000,
                        fail_on_status_code=False,
                    )
                    sent = 200 <= int(getattr(response, "status", 0) or 0) < 300
                except Exception:
                    sent = False
    
                # Secondary path: browser fetch API.
                if not sent:
                    try:
                        api_send_result = page.evaluate(
                            """
                            async ({ url, body }) => {
                              const response = await fetch(url, {
                                method: "POST",
                                credentials: "include",
                                headers: { "accept": "*/*", "content-type": "application/json" },
                                body: JSON.stringify(body),
                              });
                              return { ok: response.ok, status: response.status };
                            }
                            """,
                            {"url": send_url, "body": send_body},
                        )
                        sent = bool(isinstance(api_send_result, dict) and api_send_result.get("ok"))
                    except Exception:
                        sent = False
    
                # Last-resort path: open chat UI and submit through controls.
                if not sent:
                    chat_opened = False
                    for _ in range(2):
                        try:
                            page.goto(
                                f"https://www.skool.com/chat?ch={chat_id}",
                                wait_until="domcontentloaded",
                                timeout=20000,
                            )
                            if _wait_for_chat_view(page):
                                chat_opened = True
                                break
                        except Exception:
                            continue
                    if chat_opened:
                        input_locator = page.locator("textarea[placeholder^='Message '], textarea[placeholder*='Message']").first
                        input_locator.click()
                        input_locator.fill(text)
                        input_locator.dispatch_event("input")
    
                        send_selectors = [
                            "button[aria-label*='Send']",
                            "button:has-text('Send')",
                            "button[type='submit']",
                        ]
                        for selector in send_selectors:
                            btn = page.locator(selector).first
                            try:
                                if btn.is_visible(timeout=700):
                                    btn.click()
                                    sent = True
                                    break
                            except Exception:
                                continue
                        if not sent:
                            try:
                                input_locator.press("Enter")
                                sent = True
                            except Exception:
                                sent = False
                if not sent:
                    raise RuntimeError("Skool send request failed")
    
                page.wait_for_timeout(600)
    
                # Prefer API detail fetch to build a stable card even when chat UI is slow.
                detail_payload = _fetch_skool_chat_detail_via_context_api(context, chat_id, "")
                if not detail_payload:
                    detail_payload = _fetch_skool_chat_detail_via_api(page, chat_id, "")
    
                account_display_name = ""
                account_username = ""
                account_status = ""
                origin_group_name = "Skool Inbox"
                origin_group_url = ""
                messages: List[Dict[str, str]] = []
                raw_messages: List[Dict[str, Any]] = []
    
                if detail_payload:
                    channel = detail_payload.get("channel") if isinstance(detail_payload.get("channel"), dict) else {}
                    channel_user = channel.get("user") if isinstance(channel.get("user"), dict) else {}
                    channel_meta = channel.get("metadata") if isinstance(channel.get("metadata"), dict) else {}
                    request_group = channel.get("request_group") if isinstance(channel.get("request_group"), dict) else {}
                    other_user_id = str(channel_user.get("id") or "").strip()
                    user_ids = channel.get("user_ids") if isinstance(channel.get("user_ids"), list) else []
                    self_user_id = ""
                    for uid in user_ids:
                        uid_str = str(uid or "").strip()
                        if uid_str and uid_str != other_user_id:
                            self_user_id = uid_str
                            break
                    first_name = str(channel_user.get("first_name") or "").strip()
                    last_name = str(channel_user.get("last_name") or "").strip()
                    account_display_name = f"{first_name} {last_name}".strip() or str(channel_user.get("name") or "").strip()
                    account_username = str(channel_user.get("name") or "").strip()
                    account_status = str(channel_meta.get("other_user_tz") or "").strip()
                    origin_group_name = _normalize_origin_group_name(_extract_skool_group_name(request_group)) or "Skool Inbox"
                    origin_group_href = _extract_skool_group_href(request_group)
                    origin_group_url = _abs_skool_url(origin_group_href) if origin_group_href else ""
                    raw_messages = (
                        detail_payload.get("messages")
                        if isinstance(detail_payload.get("messages"), list)
                        else []
                    )
                    messages = _normalize_skool_api_messages(
                        raw_messages,
                        self_user_id=self_user_id,
                        other_user_id=other_user_id,
                    )
    
                # Ensure sent outbound text is reflected even if API detail is lagging.
                sent_text = text.strip()
                if sent_text and not any(
                    str(msg.get("text") or "").strip() == sent_text and str(msg.get("sender") or "") == "outbound"
                    for msg in messages[-8:]
                ):
                    messages.append(
                        {
                            "text": text,
                            "sender": "outbound",
                            "timestamp": now_display_time(),
                        }
                    )
    
                resolved_contact_name = str(contact_name or "").strip() or account_display_name
                last_message = messages[-1] if messages else {"text": text, "timestamp": now_display_time()}
                normalized_username = account_username.lstrip("@").strip()
                post_url = f"https://www.skool.com/chat?ch={chat_id}"
                if normalized_username:
                    post_url = f"{post_url}&user={normalized_username}"
                return {
                    "chat_id": chat_id,
                    "contact_name": resolved_contact_name or f"Skool Chat {chat_id[:6]}",
                    "account_username": account_username,
                    "account_status": account_status,
                    "message_text": str(last_message.get("text") or text).strip() or text,
                    "last_message_time": str(last_message.get("timestamp") or now_display_time()).strip() or now_display_time(),
                    "post_url": post_url,
                    "profile_id": profile_id,
                    "profile_name": profile_name,
                    "origin_group_name": origin_group_name,
                    "origin_group_url": origin_group_url,
                    "messages": messages[-SKOOL_CHAT_MAX_MESSAGES_PER_CHAT:],
                    "raw_messages": raw_messages,
                }
            except Exception:
                LOGGER.exception("Failed to send message to Skool chat '%s' for profile '%s'", chat_id, profile_id)
                return None
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
    finally:
        release_proxy_slot(slot_handle)


def _sync_skool_chats_to_inbox(db: sqlite3.Connection, force: bool = False) -> None:
    if not _SKOOL_CHAT_SYNC_LOCK.acquire(blocking=False):
        return
    profile_rows = db.execute(
        """
        SELECT id, name, proxy, email
        FROM profiles
        WHERE lower(trim(coalesce(status, ''))) IN ('ready', 'running')
        ORDER BY name
        """
    ).fetchall()
    known_profile_slugs: Set[str] = set()
    for row in profile_rows:
        name_slug = _slugify_profile_identity(str(row["name"] or ""))
        email_slug = _slugify_profile_identity(str(row["email"] or ""))
        if name_slug:
            known_profile_slugs.add(name_slug)
        if email_slug:
            known_profile_slugs.add(email_slug)
    try:
        if not profile_rows:
            _emit_dm_sync_log_once(
                db=db,
                profile_id="SYSTEM",
                profile_name="SYSTEM",
                status="info",
                dedupe_key="no_active_profiles",
                message="Inbox sync skipped: no active profiles (paused or missing). Existing imported chats were kept.",
                cooldown_sec=300,
            )
            db.commit()
            return

        now_ts = time.time()
        should_refresh_live = force or (
            (now_ts - float(_SKOOL_CHAT_IMPORT_CACHE.get("live_synced_at") or 0)) >= SKOOL_CHAT_SYNC_TTL_SECONDS
        )

        cards: List[Dict[str, Any]] = []
        profiles_with_successful_fetch: set[str] = set()
        if should_refresh_live:
            total_profiles = len(profile_rows)
            profile_last_attempt = {
                str(k): float(v)
                for k, v in dict(_SKOOL_CHAT_IMPORT_CACHE.get("profile_last_attempt") or {}).items()
            }
            ordered_profiles = sorted(
                list(profile_rows),
                key=lambda row: (
                    float(profile_last_attempt.get(str(row["id"]), 0.0)),
                    str(row["name"] or ""),
                ),
            )
            selected_profiles = ordered_profiles[: min(total_profiles, SKOOL_CHAT_PROFILES_PER_SYNC)]
            previous_live_cards = _normalize_live_cards_cache(list(_SKOOL_CHAT_IMPORT_CACHE.get("live_cards") or []))
            selected_profile_ids = {str(item["id"]) for item in selected_profiles}
            live_cards: List[Dict[str, Any]] = [
                card
                for card in previous_live_cards
                if str(card.get("profile_id") or "").strip() not in selected_profile_ids
            ]
            selected_names = ", ".join(str(item["name"] or "") for item in selected_profiles) or "-"
            _emit_dm_sync_log_once(
                db=db,
                profile_id="SYSTEM",
                profile_name="SYSTEM",
                status="info",
                dedupe_key="sync_started",
                message=f"Inbox sync started: checking {len(selected_profiles)}/{len(profile_rows)} profile(s) [{selected_names}].",
                cooldown_sec=15,
            )
            for idx, profile_row in enumerate(selected_profiles):
                profile_id = str(profile_row["id"])
                profile_name = str(profile_row["name"])
                cached_cards_by_chat: Dict[str, Dict[str, Any]] = {}
                for cached_card in previous_live_cards:
                    if str(cached_card.get("profile_id") or "").strip() != profile_id:
                        continue
                    cached_chat_id = str(cached_card.get("chat_id") or "").strip()
                    if not cached_chat_id:
                        continue
                    cached_cards_by_chat[cached_chat_id] = cached_card
                profile_last_attempt[profile_id] = now_ts
                profile_started_at = time.monotonic()
                profile_cards: List[Dict[str, Any]] = []
                sync_error: Optional[str] = None
                for fetch_attempt in range(max(1, int(SKOOL_CHAT_PROFILE_RETRY_ATTEMPTS))):
                    profile_cards, sync_error = _fetch_live_skool_chat_cards_with_timeout(
                        profile_id=profile_id,
                        profile_name=profile_name,
                        proxy=profile_row["proxy"],
                        expected_identities=[str(profile_row["name"] or ""), str(profile_row["email"] or "")],
                        known_profile_slugs=known_profile_slugs,
                        cached_cards_by_chat=cached_cards_by_chat,
                    )
                    if not sync_error:
                        break
                    err_l = str(sync_error).lower()
                    transient_local = (
                        "could not discover chat links" in err_l
                        or "timed out" in err_l
                        or "timeout" in err_l
                        or "network" in err_l
                    )
                    if not transient_local or fetch_attempt >= int(SKOOL_CHAT_PROFILE_RETRY_ATTEMPTS) - 1:
                        break
                    time.sleep(0.8 + (fetch_attempt * 0.7))
                if sync_error:
                    sync_error_lower = str(sync_error).lower()
                    if "identity mismatch during chat sync" in sync_error_lower:
                        removed = _purge_imported_conversations_for_profile(db, profile_id)
                        live_cards = [
                            card
                            for card in live_cards
                            if str(card.get("profile_id") or "").strip() != profile_id
                        ]
                        _emit_dm_sync_log_once(
                            db=db,
                            profile_id=profile_id,
                            profile_name=profile_name,
                            status="error",
                            dedupe_key=f"identity_mismatch_{profile_id}",
                            message=(
                                f"Inbox sync blocked: {sync_error}. "
                                f"Purged {removed} imported chat(s) for this profile."
                            ),
                            cooldown_sec=15,
                        )
                        continue
                    try:
                        status_row = db.execute("SELECT status FROM profiles WHERE id = ?", (profile_id,)).fetchone()
                        profile_status = str(status_row["status"] if status_row else "").strip().lower()
                    except Exception:
                        profile_status = ""
                    if "not logged in to skool" in str(sync_error).lower() and profile_status == "ready":
                        sync_error = "Login page detected during DM sync (transient/network issue)"
                    friendly_error = _humanize_dm_sync_error(str(sync_error))
                    is_transient = _is_transient_dm_sync_error(str(sync_error))
                    _emit_dm_sync_log_once(
                        db=db,
                        profile_id=profile_id,
                        profile_name=profile_name,
                        status="retry" if is_transient else "error",
                        dedupe_key=friendly_error.lower()[:80],
                        message=f"{'Inbox sync retry' if is_transient else 'Inbox sync failed'}: {friendly_error}",
                        cooldown_sec=20,
                    )
                    continue

                live_cards = [
                    card
                    for card in live_cards
                    if str(card.get("profile_id") or "").strip() != profile_id
                ]
                live_cards.extend(profile_cards)
                if not profile_cards:
                    profiles_with_successful_fetch.add(str(profile_row["id"]))
                    _emit_dm_sync_log_once(
                        db=db,
                        profile_id=profile_id,
                        profile_name=profile_name,
                        status="info",
                        dedupe_key="no_chats",
                        message="Inbox sync complete: no chats found for this profile.",
                        cooldown_sec=20,
                    )
                else:
                    profiles_with_successful_fetch.add(profile_id)
                    elapsed_sec = max(0.0, time.monotonic() - profile_started_at)
                    _emit_dm_sync_log_once(
                        db=db,
                        profile_id=profile_id,
                        profile_name=profile_name,
                        status="info",
                        dedupe_key=f"chats_{len(profile_cards)}",
                        message=f"Inbox sync complete: {len(profile_cards)} chat(s) imported for this profile in {elapsed_sec:.1f}s.",
                        cooldown_sec=20,
                    )
                if SKOOL_CHAT_PROFILE_FETCH_DELAY_SECONDS > 0 and idx < len(selected_profiles) - 1:
                    time.sleep(SKOOL_CHAT_PROFILE_FETCH_DELAY_SECONDS)
            _SKOOL_CHAT_IMPORT_CACHE["live_synced_at"] = now_ts
            live_cards = _normalize_live_cards_cache(live_cards)
            _SKOOL_CHAT_IMPORT_CACHE["live_cards"] = live_cards
            _SKOOL_CHAT_IMPORT_CACHE["successful_profiles"] = list(profiles_with_successful_fetch)
            _SKOOL_CHAT_IMPORT_CACHE["profile_last_attempt"] = profile_last_attempt
            cards = live_cards
        else:
            cards = _normalize_live_cards_cache(list(_SKOOL_CHAT_IMPORT_CACHE.get("live_cards") or []))
            profiles_with_successful_fetch = set(str(x) for x in (_SKOOL_CHAT_IMPORT_CACHE.get("successful_profiles") or []))

        existing_ids = {
            row["id"]
            for row in db.execute("SELECT id FROM conversations WHERE id LIKE ?", (f"{SKOOL_CHAT_IMPORT_PREFIX}%",)).fetchall()
        }
        existing_by_profile: Dict[str, int] = {}
        for conv_id in existing_ids:
            suffix = conv_id[len(SKOOL_CHAT_IMPORT_PREFIX):] if conv_id.startswith(SKOOL_CHAT_IMPORT_PREFIX) else ""
            profile_part = suffix.rsplit("-", 1)[0] if "-" in suffix else ""
            if not profile_part:
                continue
            existing_by_profile[profile_part] = int(existing_by_profile.get(profile_part, 0)) + 1
        seen_ids: set[str] = set()
        seen_by_profile: Dict[str, int] = {}

        for card in cards:
            chat_id = (card.get("chat_id") or "").strip()
            if not chat_id:
                continue
            profile_part = (card.get("profile_id") or "imported-profile").strip()
            conversation_id = f"{SKOOL_CHAT_IMPORT_PREFIX}{profile_part}-{chat_id}"
            seen_ids.add(conversation_id)
            seen_by_profile[profile_part] = int(seen_by_profile.get(profile_part, 0)) + 1
            _upsert_skool_chat_card(db, card)

    # Remove legacy file-imported records and any stale DM records not present in current live snapshot.
        legacy_ids = {
            row["id"]
            for row in db.execute("SELECT id FROM conversations WHERE profileId = 'imported-profile' AND id LIKE ?", (f"{SKOOL_CHAT_IMPORT_PREFIX}%",)).fetchall()
        }
        stale_ids: List[str] = []
        stale_miss_counts = dict(_SKOOL_CHAT_IMPORT_CACHE.get("stale_miss_counts") or {})
        trustworthy_profiles: set[str] = set()
        for profile_part in profiles_with_successful_fetch:
            old_count = int(existing_by_profile.get(profile_part, 0))
            new_count = int(seen_by_profile.get(profile_part, 0))
            if new_count <= 0:
                continue
            # Consider sync trustworthy only when scrape is near full size for this profile.
            if old_count <= 5 or new_count >= int(old_count * 0.9):
                trustworthy_profiles.add(profile_part)

        # Reset miss counters for chats seen in current cycle.
        for conv_id in seen_ids:
            if conv_id in stale_miss_counts:
                stale_miss_counts.pop(conv_id, None)

        for conv_id in existing_ids:
            if conv_id in seen_ids:
                continue
            suffix = conv_id[len(SKOOL_CHAT_IMPORT_PREFIX):] if conv_id.startswith(SKOOL_CHAT_IMPORT_PREFIX) else ""
            profile_part = suffix.rsplit("-", 1)[0] if "-" in suffix else ""
            if not profile_part or profile_part not in trustworthy_profiles:
                # Keep chats for partial/failed profile sync and clear accidental counters.
                stale_miss_counts.pop(conv_id, None)
                continue
            misses = int(stale_miss_counts.get(conv_id, 0)) + 1
            stale_miss_counts[conv_id] = misses
            if misses >= 3:
                stale_ids.append(conv_id)
                stale_miss_counts.pop(conv_id, None)
        stale_ids.extend([conv_id for conv_id in legacy_ids if conv_id not in stale_ids])
        if stale_ids:
            db.executemany("DELETE FROM messages WHERE conversationId = ?", [(conv_id,) for conv_id in stale_ids])
            db.executemany("DELETE FROM conversations WHERE id = ?", [(conv_id,) for conv_id in stale_ids])
        _SKOOL_CHAT_IMPORT_CACHE["stale_miss_counts"] = stale_miss_counts
        deduped_conversations = _dedupe_imported_conversations(db)
        pruned_orphans = _prune_orphan_imported_conversations(db)
        _dedupe_imported_messages(db)
        _dedupe_adjacent_imported_messages(db)

        _emit_dm_sync_log_once(
            db=db,
            profile_id="SYSTEM",
            profile_name="SYSTEM",
            status="info",
            dedupe_key=f"sync_done_{len(cards)}_{len(stale_ids)}_{deduped_conversations}_{pruned_orphans}",
            message=f"Inbox sync complete: active chats={len(cards)}, stale removed={len(stale_ids)}, deduped={deduped_conversations}, orphan removed={pruned_orphans}.",
            cooldown_sec=15,
        )

        db.commit()
    finally:
        _SKOOL_CHAT_SYNC_LOCK.release()


def _load_or_create_automation_settings(db: sqlite3.Connection) -> AutomationSettingsModel:
    rows = db.execute("SELECT key, value FROM automation_settings ORDER BY key").fetchall()
    if not rows:
        default_payload = AUTOMATION_SETTINGS_DEFAULT.model_dump_json()
        db.execute("INSERT INTO automation_settings (key, value) VALUES ('default', ?)", (default_payload,))
        db.commit()
        return AUTOMATION_SETTINGS_DEFAULT

    default_row = next((row for row in rows if row["key"] == "default"), None)
    if default_row is None:
        first_payload = rows[0]["value"]
        db.execute("DELETE FROM automation_settings")
        db.execute("INSERT INTO automation_settings (key, value) VALUES ('default', ?)", (first_payload,))
        db.commit()
        default_row = db.execute("SELECT value FROM automation_settings WHERE key = 'default'").fetchone()

    if len(rows) > 1:
        db.execute("DELETE FROM automation_settings WHERE key != 'default'")
        db.commit()

    try:
        stored_payload = json.loads(default_row["value"])
        if isinstance(stored_payload, dict):
            # Merge with defaults so new settings keys are added without wiping existing values.
            merged_payload = {**AUTOMATION_SETTINGS_DEFAULT.model_dump(), **stored_payload}
        else:
            merged_payload = AUTOMATION_SETTINGS_DEFAULT.model_dump()
        return AutomationSettingsModel(**merged_payload)
    except Exception:
        fallback_payload = AUTOMATION_SETTINGS_DEFAULT.model_dump_json()
        db.execute("UPDATE automation_settings SET value = ? WHERE key = 'default'", (fallback_payload,))
        db.commit()
        return AUTOMATION_SETTINGS_DEFAULT


def ensure_tables() -> None:
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY,name TEXT NOT NULL,username TEXT NOT NULL,password TEXT NOT NULL,email TEXT,proxy TEXT,avatar TEXT NOT NULL,status TEXT NOT NULL,dailyUsage INTEGER NOT NULL,groupsConnected INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS communities (id TEXT PRIMARY KEY,profileId TEXT NOT NULL,name TEXT NOT NULL,url TEXT NOT NULL,dailyLimit INTEGER NOT NULL,maxPostAgeDays INTEGER NOT NULL DEFAULT 0,lastScanned TEXT NOT NULL,status TEXT NOT NULL,matchesToday INTEGER NOT NULL,actionsToday INTEGER NOT NULL,totalScannedPosts INTEGER NOT NULL,totalKeywordMatches INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS labels (id TEXT PRIMARY KEY,name TEXT NOT NULL,color TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS keyword_rules (id TEXT PRIMARY KEY,keyword TEXT NOT NULL,persona TEXT NOT NULL,promptPreview TEXT NOT NULL,commentPrompt TEXT,dmPrompt TEXT,dmMaxReplies INTEGER,dmReplyDelay INTEGER,active INTEGER NOT NULL,assignedProfileIds TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS automation_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS queue_items (id TEXT PRIMARY KEY,profile TEXT NOT NULL,profileId TEXT NOT NULL,community TEXT NOT NULL,communityId TEXT NOT NULL,postId TEXT NOT NULL,keyword TEXT NOT NULL,keywordId TEXT NOT NULL,scheduledTime TEXT NOT NULL,scheduledFor TEXT NOT NULL,priorityScore INTEGER NOT NULL,countdown INTEGER NOT NULL)""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_queue_items_scheduled_for ON queue_items(scheduledFor)""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_queue_items_profile_scheduled_for ON queue_items(profileId, scheduledFor)""")
        db.execute("""CREATE TABLE IF NOT EXISTS logs (id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,profile TEXT NOT NULL,status TEXT NOT NULL,module TEXT NOT NULL DEFAULT 'system',action TEXT NOT NULL DEFAULT 'event',message TEXT NOT NULL,fallbackLevelUsed TEXT)""")
        log_cols = {str(row["name"] or "").strip() for row in db.execute("PRAGMA table_info(logs)").fetchall()}
        if "module" not in log_cols:
            db.execute("ALTER TABLE logs ADD COLUMN module TEXT NOT NULL DEFAULT 'system'")
        if "action" not in log_cols:
            db.execute("ALTER TABLE logs ADD COLUMN action TEXT NOT NULL DEFAULT 'event'")
        db.execute("""CREATE TABLE IF NOT EXISTS proxy_status_cache (proxyKey TEXT PRIMARY KEY,status TEXT NOT NULL,message TEXT,checkedAt TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS activity_feed (id TEXT PRIMARY KEY,profile TEXT NOT NULL,groupName TEXT NOT NULL,action TEXT NOT NULL,timestamp TEXT NOT NULL,postUrl TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS automation_comment_events (id TEXT PRIMARY KEY,profileId TEXT NOT NULL,profile TEXT NOT NULL,community TEXT NOT NULL,postUrl TEXT NOT NULL,keyword TEXT NOT NULL,prompt TEXT NOT NULL,commentText TEXT NOT NULL,createdAt TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY,contactName TEXT NOT NULL,profileId TEXT NOT NULL,profileName TEXT NOT NULL,keyword TEXT NOT NULL,originGroup TEXT NOT NULL,lastMessage TEXT NOT NULL,lastMessageTime TEXT NOT NULL,unread INTEGER NOT NULL,labelId TEXT,isArchived INTEGER NOT NULL,isDeletedUi INTEGER NOT NULL,contactInfo TEXT NOT NULL,commentAttribution TEXT NOT NULL,keywordContext TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY,conversationId TEXT NOT NULL,text TEXT NOT NULL,sender TEXT NOT NULL,timestamp TEXT NOT NULL,isDeletedUi INTEGER NOT NULL,FOREIGN KEY(conversationId) REFERENCES conversations(id))""")
        _dedupe_imported_conversations(db)
        _prune_orphan_imported_conversations(db)
        _normalize_legacy_chat_metadata(db)
        _dedupe_imported_messages(db)
        _dedupe_adjacent_imported_messages(db)
        db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_skool_unique_payload
            ON messages (conversationId, sender, timestamp, text)
            WHERE id LIKE 'skool-msg-%'
            """
        )
        db.execute("""CREATE TABLE IF NOT EXISTS analytics (key TEXT PRIMARY KEY,value TEXT NOT NULL)""")
        # Stores identity history for deleted profiles to reconnect historical actions on re-add.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS deleted_profile_history (
                oldProfileId TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                email TEXT,
                name TEXT,
                deletedAt TEXT NOT NULL
            )
            """
        )
        conversation_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(conversations)").fetchall()}
        if "aiAutoEnabled" not in conversation_columns:
            db.execute("ALTER TABLE conversations ADD COLUMN aiAutoEnabled INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE conversations SET aiAutoEnabled = 0 WHERE aiAutoEnabled IS NULL")
        community_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(communities)").fetchall()}
        if "maxPostAgeDays" not in community_columns:
            db.execute("ALTER TABLE communities ADD COLUMN maxPostAgeDays INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE communities SET maxPostAgeDays = 0 WHERE maxPostAgeDays IS NULL OR maxPostAgeDays < 0")
        pc = {str(r["name"]) for r in db.execute("PRAGMA table_info(profiles)").fetchall()}
        if "cookie_json" not in pc:
            db.execute("ALTER TABLE profiles ADD COLUMN cookie_json TEXT")
        profile_rows = db.execute("SELECT id, password FROM profiles").fetchall()
        for row in profile_rows:
            raw_password = str(row["password"] or "")
            if not raw_password or is_encrypted_secret(raw_password):
                continue
            db.execute(
                "UPDATE profiles SET password = ? WHERE id = ?",
                (encrypt_secret(raw_password), row["id"]),
            )
        _load_proxy_cache_from_db(db)
        db.commit()


# Keep browser profile storage clean by pruning orphan account dirs and transient browser caches.
def _cleanup_skool_accounts_storage() -> None:
    if not SKOOL_ACCOUNTS_CLEANUP_ENABLED:
        return

    accounts_dir = Path(__file__).parent / "skool_accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True)

    try:
        with get_db() as db:
            rows = db.execute("SELECT id FROM profiles").fetchall()
        active_profile_ids = {str(row["id"] or "").strip() for row in rows if str(row["id"] or "").strip()}
    except Exception:
        LOGGER.exception("Failed to load profiles for skool_accounts cleanup")
        return

    removed_orphan_dirs = 0
    removed_cache_dirs = 0

    for child in accounts_dir.iterdir():
        if not child.is_dir():
            continue
        profile_id = child.name.strip()

        if SKOOL_ACCOUNTS_PRUNE_ORPHANS_ENABLED and profile_id and profile_id not in active_profile_ids:
            try:
                shutil.rmtree(child, ignore_errors=True)
                removed_orphan_dirs += 1
            except Exception:
                LOGGER.exception("Failed to remove orphan skool account dir '%s'", child)
            continue

        if SKOOL_ACCOUNTS_PRUNE_TRANSIENT_CACHE_ENABLED and profile_id in active_profile_ids:
            for rel_path in SKOOL_TRANSIENT_CACHE_RELATIVE_DIRS:
                cache_dir = child / rel_path
                if not cache_dir.exists():
                    continue
                try:
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    removed_cache_dirs += 1
                except Exception:
                    LOGGER.exception("Failed to remove transient cache dir '%s'", cache_dir)

    if removed_orphan_dirs or removed_cache_dirs:
        LOGGER.info(
            "skool_accounts cleanup: orphan_dirs=%s transient_cache_dirs=%s",
            removed_orphan_dirs,
            removed_cache_dirs,
        )


class ProfileModel(BaseModel):
    id: str
    name: str
    password: Optional[str] = None
    email: Optional[str]
    proxy: Optional[str]
    avatar: str
    status: str
    dailyUsage: int
    groupsConnected: int
    hasPassword: bool = False
    proxyStatus: Optional[str] = None


class ProfileCreateModel(BaseModel):
    name: str
    password: str
    email: Optional[str] = None
    proxy: Optional[str] = None
    avatar: Optional[str] = None
    status: str = "checking"
    dailyUsage: int = 0
    groupsConnected: int = 0


class ProfileUpdateModel(BaseModel):
    name: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None
    proxy: Optional[str] = None
    avatar: Optional[str] = None
    status: Optional[str] = None
    dailyUsage: Optional[int] = None
    groupsConnected: Optional[int] = None


class CommunityModel(BaseModel):
    id: str
    profileId: str
    name: str
    url: str
    dailyLimit: int
    maxPostAgeDays: int = 0
    lastScanned: str
    status: str
    matchesToday: int
    actionsToday: int
    totalScannedPosts: int
    totalKeywordMatches: int


class CommunityCreateModel(BaseModel):
    profileId: str
    name: str
    url: str
    dailyLimit: int = 5
    maxPostAgeDays: int = 0
    lastScanned: str = ""
    status: str = "active"
    matchesToday: int = 0
    actionsToday: int = 0
    totalScannedPosts: int = 0
    totalKeywordMatches: int = 0


class CommunityUpdateModel(BaseModel):
    profileId: Optional[str] = None
    name: Optional[str] = None
    url: Optional[str] = None
    dailyLimit: Optional[int] = None
    maxPostAgeDays: Optional[int] = None
    lastScanned: Optional[str] = None
    status: Optional[str] = None
    matchesToday: Optional[int] = None
    actionsToday: Optional[int] = None
    totalScannedPosts: Optional[int] = None
    totalKeywordMatches: Optional[int] = None


# Per-profile sync stats for community fetch run.
class CommunityFetchProfileResultModel(BaseModel):
    profileId: str
    profileName: str
    discovered: int
    created: int
    updated: int
    skipped: int
    total: Optional[int] = None
    joined: Optional[int] = None
    pendingExcluded: Optional[int] = None
    unknownExcluded: Optional[int] = None
    error: Optional[str] = None


# Aggregated sync response for "Fetch Communities" action.
class CommunityFetchResponseModel(BaseModel):
    success: bool
    profilesProcessed: int
    discovered: int
    created: int
    updated: int
    skipped: int
    totalFetched: Optional[int] = None
    totalJoined: Optional[int] = None
    totalPendingExcluded: Optional[int] = None
    totalUnknownExcluded: Optional[int] = None
    results: List[CommunityFetchProfileResultModel]


# Runtime status for background community sync job.
class CommunityFetchStatusModel(BaseModel):
    running: bool
    startedAt: str
    finishedAt: str
    profilesTotal: int
    profilesDone: int
    currentProfileId: str
    currentProfileName: str
    lastError: str
    lastResult: Optional[CommunityFetchResponseModel] = None


class LabelModel(BaseModel):
    id: str
    name: str
    color: str


class LabelCreateModel(BaseModel):
    name: str
    color: str


class LabelUpdateModel(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None

class KeywordRuleModel(BaseModel):
    id: str
    keyword: str
    persona: str
    promptPreview: str
    commentPrompt: Optional[str]
    dmPrompt: Optional[str]
    dmMaxReplies: Optional[int]
    dmReplyDelay: Optional[int]
    active: bool
    assignedProfileIds: List[str]


class KeywordRuleCreateModel(BaseModel):
    keyword: str
    persona: str
    promptPreview: str
    commentPrompt: Optional[str] = None
    dmPrompt: Optional[str] = None
    dmMaxReplies: Optional[int] = None
    dmReplyDelay: Optional[int] = None
    active: bool = True
    assignedProfileIds: List[str] = []


class KeywordRuleUpdateModel(BaseModel):
    keyword: Optional[str] = None
    persona: Optional[str] = None
    promptPreview: Optional[str] = None
    commentPrompt: Optional[str] = None
    dmPrompt: Optional[str] = None
    dmMaxReplies: Optional[int] = None
    dmReplyDelay: Optional[int] = None
    active: Optional[bool] = None
    assignedProfileIds: Optional[List[str]] = None


class AutomationSettingsModel(BaseModel):
    masterEnabled: bool
    globalDailyCapPerAccount: int
    delayMin: int
    delayMax: int
    roundsBeforeConnectionRest: int = 5
    connectionRestMinutes: int = 5
    activeDays: List[str]
    runFrom: str
    runTo: str
    postsPerCommunityScanLimit: int
    preScanEnabled: bool
    commentFallbackEnabled: bool
    commentFallbackPrompt: str
    dmFallbackPrompt: str
    keywordScanningEnabled: bool
    scanIntervalMinutes: int
    postsPerCommunityPerScan: int
    scanConcurrency: int
    executionConcurrency: int
    # Max queued tasks per profile during one scheduler prefill pass.
    queuePrefillMaxPerProfilePerPass: int = 2
    blacklistEnabled: bool
    blacklistTerms: List[str]


AUTOMATION_SETTINGS_DEFAULT = AutomationSettingsModel(
    masterEnabled=False,
    globalDailyCapPerAccount=20,
    delayMin=3,
    delayMax=10,
    roundsBeforeConnectionRest=5,
    connectionRestMinutes=5,
    activeDays=["Mon", "Tue", "Wed", "Thu", "Fri"],
    runFrom="09:00",
    runTo="18:00",
    postsPerCommunityScanLimit=25,
    preScanEnabled=True,
    commentFallbackEnabled=True,
    commentFallbackPrompt="",
    dmFallbackPrompt="",
    keywordScanningEnabled=True,
    scanIntervalMinutes=5,
    postsPerCommunityPerScan=20,
    scanConcurrency=2,
    executionConcurrency=1,
    queuePrefillMaxPerProfilePerPass=max(1, int(os.environ.get("AUTOMATION_QUEUE_PREFILL_MAX_PER_PROFILE", "2"))),
    blacklistEnabled=False,
    blacklistTerms=[],
)


class QueueItemModel(BaseModel):
    id: str
    profile: str
    profileId: str
    community: str
    communityId: str
    postId: str
    keyword: str
    keywordId: str
    scheduledTime: str
    scheduledFor: str
    priorityScore: int
    countdown: int


class QueueItemUpdateModel(BaseModel):
    profile: Optional[str] = None
    profileId: Optional[str] = None
    community: Optional[str] = None
    communityId: Optional[str] = None
    postId: Optional[str] = None
    keyword: Optional[str] = None
    keywordId: Optional[str] = None
    scheduledTime: Optional[str] = None
    scheduledFor: Optional[str] = None
    priorityScore: Optional[int] = None
    countdown: Optional[int] = None


class LogEntryModel(BaseModel):
    id: str
    timestamp: str
    profile: str
    status: Literal["success", "retry", "error", "info"]
    module: str = "system"
    action: str = "event"
    message: str
    fallbackLevelUsed: Optional[str] = None


class ActivityEntryModel(BaseModel):
    id: str
    profile: str
    groupName: str
    action: str
    timestamp: str
    postUrl: str


class ContactInfoModel(BaseModel):
    firstInteraction: str = ""


class CommentAttributionModel(BaseModel):
    postUrl: str = ""
    originalComment: str = ""
    timestamp: str = ""
    postTitle: str = ""


class KeywordContextModel(BaseModel):
    persona: str = ""
    promptPreview: str = ""


class MessageModel(BaseModel):
    id: str
    text: str
    sender: Literal["outbound", "inbound"]
    timestamp: str
    isDeletedUi: bool


class MessageCreateModel(BaseModel):
    text: str
    sender: Literal["outbound", "inbound"] = "outbound"
    timestamp: Optional[str] = None


class ConversationModel(BaseModel):
    id: str
    contactName: str
    profileId: str
    profileName: str
    keyword: str
    originGroup: str
    lastMessage: str
    lastMessageTime: str
    unread: bool
    labelId: Optional[str]
    isArchived: bool
    isDeletedUi: bool
    aiAutoEnabled: bool
    contactInfo: ContactInfoModel
    commentAttribution: CommentAttributionModel
    keywordContext: KeywordContextModel
    messages: List[MessageModel]


class ConversationPatchModel(BaseModel):
    labelId: Optional[str] = None
    isArchived: Optional[bool] = None
    isDeletedUi: Optional[bool] = None
    unread: Optional[bool] = None
    aiAutoEnabled: Optional[bool] = None


class AnalyticsPayload(BaseModel):
    messagesPerDay: List[Dict[str, Any]]
    keywordDistribution: List[Dict[str, Any]]
    profileActivity: List[Dict[str, Any]]


class AutomationStartRequest(BaseModel):
    profiles: Optional[List[Dict[str, Any]]] = None
    globalSettings: Optional[Dict[str, Any]] = None


class TestCommentRequest(BaseModel):
    profileId: str
    communityUrl: str
    prompt: str = "Write a short, helpful comment under 40 words."
    apiKey: Optional[str] = None


class ProofRunRequest(BaseModel):
    profileId: str
    communityUrl: str


class OpenAITestRequest(BaseModel):
    apiKey: Optional[str] = None
    prompt: str = "Reply with a short 'ok' response."


class OpenAIKeyUpdateRequest(BaseModel):
    apiKey: str


def _looks_like_masked_secret(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if "*" in text:
        return True
    if "..." in text or "…" in text:
        return True
    return False


class ConversationAISuggestRequest(BaseModel):
    tone: Literal["Friendly", "Authority", "Consultant", "Casual"] = "Friendly"


class ConversationAISuggestResponse(BaseModel):
    success: bool
    text: str
    source: str
    model: Optional[str] = None


def _split_keywords(value: str) -> List[str]:
    return [part.strip().lower() for part in str(value or "").split(",") if part.strip()]


def _weekday_labels_last_7_days() -> List[str]:
    labels: List[str] = []
    today = datetime.now()
    for offset in range(6, -1, -1):
        labels.append((today - timedelta(days=offset)).strftime("%a"))
    return labels


def _parse_analytics_datetime(raw: str) -> Optional[datetime]:
    value = str(raw or "").strip()
    if not value:
        return None
    candidates = [
        value,
        value.replace("Z", "+00:00"),
    ]
    for item in candidates:
        try:
            return datetime.fromisoformat(item)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    return None


def _compute_live_analytics_payload(db: sqlite3.Connection) -> Dict[str, Any]:
    day_labels = _weekday_labels_last_7_days()
    day_set = set(day_labels)

    # Messages per day: prefer real datetimes; fallback to current day count when timestamps are time-only.
    messages_by_day = {day: 0 for day in day_labels}
    parsed_message_count = 0
    total_messages = 0
    for row in db.execute("SELECT timestamp FROM messages WHERE isDeletedUi = 0").fetchall():
        total_messages += 1
        dt = _parse_analytics_datetime(str(row["timestamp"] or ""))
        if not dt:
            continue
        day = dt.strftime("%a")
        if day in day_set:
            messages_by_day[day] = int(messages_by_day.get(day, 0)) + 1
            parsed_message_count += 1
    if total_messages > 0 and parsed_message_count == 0:
        today_label = datetime.now().strftime("%a")
        messages_by_day[today_label] = total_messages
    messages_per_day = [{"day": day, "messages": int(messages_by_day.get(day, 0))} for day in day_labels]

    # Keyword distribution from conversations.
    keyword_rows = db.execute(
        """
        SELECT keyword, COUNT(*) AS cnt
        FROM conversations
        WHERE isDeletedUi = 0 AND TRIM(COALESCE(keyword, '')) != ''
        GROUP BY keyword
        ORDER BY cnt DESC, keyword ASC
        LIMIT 12
        """
    ).fetchall()
    keyword_distribution = [{"keyword": str(row["keyword"]), "count": int(row["cnt"] or 0)} for row in keyword_rows]

    # Profile activity by weekday.
    profile_rows = db.execute("SELECT name FROM profiles ORDER BY name").fetchall()
    profile_names = [str(row["name"] or "").strip() for row in profile_rows if str(row["name"] or "").strip()]
    profile_activity_map: Dict[str, Dict[str, Any]] = {
        day: {"day": day, **{name: 0 for name in profile_names}}
        for day in day_labels
    }
    parsed_conversation_count = 0
    conversation_rows = db.execute(
        "SELECT profileName, lastMessageTime FROM conversations WHERE isDeletedUi = 0"
    ).fetchall()
    for row in conversation_rows:
        profile_name = str(row["profileName"] or "").strip()
        if not profile_name:
            continue
        if profile_name not in profile_names:
            profile_names.append(profile_name)
            for day in day_labels:
                profile_activity_map[day][profile_name] = 0
        dt = _parse_analytics_datetime(str(row["lastMessageTime"] or ""))
        if not dt:
            continue
        day = dt.strftime("%a")
        if day in day_set:
            profile_activity_map[day][profile_name] = int(profile_activity_map[day].get(profile_name, 0)) + 1
            parsed_conversation_count += 1
    if conversation_rows and parsed_conversation_count == 0:
        today_label = datetime.now().strftime("%a")
        fallback_rows = db.execute(
            """
            SELECT profileName, COUNT(*) AS cnt
            FROM conversations
            WHERE isDeletedUi = 0
            GROUP BY profileName
            """
        ).fetchall()
        for row in fallback_rows:
            profile_name = str(row["profileName"] or "").strip()
            if not profile_name:
                continue
            if profile_name not in profile_names:
                profile_names.append(profile_name)
                for day in day_labels:
                    profile_activity_map[day][profile_name] = 0
            profile_activity_map[today_label][profile_name] = int(row["cnt"] or 0)
    profile_activity = [profile_activity_map[day] for day in day_labels]

    return {
        "messagesPerDay": messages_per_day,
        "keywordDistribution": keyword_distribution,
        "profileActivity": profile_activity,
    }


def _find_matching_keyword_rule(db: sqlite3.Connection, profile_id: str, conversation_keyword: str) -> Optional[sqlite3.Row]:
    keyword_norm = str(conversation_keyword or "").strip().lower()
    if not keyword_norm:
        return None
    rows = db.execute("SELECT * FROM keyword_rules WHERE active = 1 ORDER BY rowid DESC").fetchall()
    best_row: Optional[sqlite3.Row] = None
    best_score = -1
    for row in rows:
        assigned = parse_json_field(str(row["assignedProfileIds"] or "[]"), [])
        assigned_ids = {str(item).strip() for item in assigned if str(item).strip()}
        if assigned_ids and profile_id not in assigned_ids:
            continue
        for token in _split_keywords(str(row["keyword"] or "")):
            score = -1
            if token == keyword_norm:
                score = 3
            elif token and (token in keyword_norm or keyword_norm in token):
                score = 2
            elif token and token in keyword_norm.replace("-", " "):
                score = 1
            if score > best_score or (score == best_score and best_row is not None and len(token) > len(str(best_row["keyword"] or ""))):
                best_score = score
                best_row = row
    return best_row if best_score >= 0 else None


def _default_dm_fallback_prompt() -> str:
    return (
        "Hey! Noticed your activity in the group. I work in a similar space and thought it might be worth connecting. "
        "Open to a quick chat?"
    )


def _tone_instruction(tone: str) -> str:
    normalized = str(tone or "").strip().lower()
    if normalized == "authority":
        return "Write confidently and clearly. Keep it concise, specific, and professional."
    if normalized == "consultant":
        return "Write like a helpful consultant: curious, structured, and practical."
    if normalized == "casual":
        return "Write naturally and relaxed, but still professional and respectful."
    return "Write warm and friendly, approachable, and human."


def _local_dm_template(tone: str, contact_name: str, keyword: str, origin_group: str) -> str:
    first_name = (str(contact_name or "").strip().split(" ")[0] or "there").strip()
    if str(tone).lower() == "authority":
        return f"Hi {first_name}, saw your activity in {origin_group}. We help teams improve {keyword} with a proven process. Open to a quick chat?"
    if str(tone).lower() == "consultant":
        return f"Hi {first_name}, noticed your posts in {origin_group}. Curious what you're currently doing around {keyword}. Happy to share what has worked for similar teams."
    if str(tone).lower() == "casual":
        return f"Hey {first_name}, saw you in {origin_group}. I work in {keyword} too and thought it made sense to connect. Up for a quick chat?"
    return f"Hey {first_name}, noticed your activity in {origin_group}. I work in a similar space around {keyword}. Open to a quick chat?"


def _openai_generate_dm_rest(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    if not api_key:
        raise RuntimeError("OpenAI API key missing")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 160,
        "temperature": 0.65,
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
    return str(content or "").strip()


def _get_openai_key_for_dm() -> str:
    config_path = Path(__file__).parent / "config.local.json"
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                value = str((json.load(f) or {}).get("openai_api_key", "")).strip()
                if value and not _looks_like_masked_secret(value):
                    return value
        except Exception:
            pass
    env = os.environ.get("OPENAI_API_KEY", "").strip()
    if env and not _looks_like_masked_secret(env):
        return env
    return ""


def _generate_conversation_ai_suggest(
    db: sqlite3.Connection,
    conversation_id: str,
    tone: Literal["Friendly", "Authority", "Consultant", "Casual"] = "Friendly",
) -> ConversationAISuggestResponse:
    row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Conversation not found")

    conversation = dict(row)
    profile_id = str(conversation.get("profileId") or "").strip()
    keyword = str(conversation.get("keyword") or "").strip() or "your topic"
    origin_group = str(conversation.get("originGroup") or "").strip() or "the group"
    contact_name = str(conversation.get("contactName") or "").strip() or "there"

    matched_rule = _find_matching_keyword_rule(db, profile_id, keyword)
    dm_prompt = str((matched_rule["dmPrompt"] if matched_rule else "") or "").strip()

    settings = _load_or_create_automation_settings(db)
    global_dm_fallback = str(getattr(settings, "dmFallbackPrompt", "") or "").strip()

    selected_prompt = dm_prompt or global_dm_fallback
    source = "keyword_rule_dm_prompt" if dm_prompt else ("global_dm_fallback_prompt" if global_dm_fallback else "suggest_only_no_send")

    message_rows = db.execute(
        "SELECT sender, text, timestamp FROM messages WHERE conversationId = ? AND isDeletedUi = 0 ORDER BY rowid DESC LIMIT 8",
        (conversation_id,),
    ).fetchall()
    history_rows = list(reversed(message_rows))
    history_lines = []
    for item in history_rows:
        sender = "me" if str(item["sender"]).strip().lower() == "outbound" else "them"
        text = str(item["text"] or "").strip().replace("\n", " ")
        if not text:
            continue
        history_lines.append(f"{sender}: {text[:280]}")
    history = "\n".join(history_lines) if history_lines else "(no prior messages)"

    system_prompt = (
        "You write direct-response outreach DMs. English only. "
        f"{_tone_instruction(tone)} "
        "Keep it short (1-3 sentences), natural, specific, and non-spammy. "
        "Do not mention being an AI. Avoid emojis unless user asks. "
        "Do not use placeholders."
    )
    if not selected_prompt:
        return ConversationAISuggestResponse(
            success=True,
            text=_local_dm_template(tone, contact_name, keyword, origin_group),
            source="suggest_only_no_send:no_keyword_rule_or_global_fallback",
            model=None,
        )

    user_prompt = (
        f"Primary instruction:\n{selected_prompt}\n\n"
        f"Context:\n"
        f"- Contact: {contact_name}\n"
        f"- Group: {origin_group}\n"
        f"- Keyword/topic: {keyword}\n"
        f"- Desired tone: {tone}\n"
        f"- Recent chat history:\n{history}\n\n"
        "Write one DM reply in English."
    )

    api_key = _get_openai_key_for_dm()
    model = os.environ.get("OPENAI_DM_MODEL", "").strip() or os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    if not api_key:
        return ConversationAISuggestResponse(
            success=True,
            text=_local_dm_template(tone, contact_name, keyword, origin_group),
            source=f"{source}:local_template_no_api_key",
            model=None,
        )

    try:
        generated = _openai_generate_dm_rest(api_key=api_key, model=model, system_prompt=system_prompt, user_prompt=user_prompt)
        if not generated:
            raise RuntimeError("empty_generation")
        return ConversationAISuggestResponse(success=True, text=generated, source=source, model=model)
    except Exception:
        return ConversationAISuggestResponse(
            success=True,
            text=_local_dm_template(tone, contact_name, keyword, origin_group),
            source=f"{source}:local_template_on_error",
            model=None,
        )


def format_message(row: sqlite3.Row) -> MessageModel:
    return MessageModel(id=row["id"], text=row["text"], sender=row["sender"], timestamp=row["timestamp"], isDeletedUi=bool(row["isDeletedUi"]))


def build_message_map(db: sqlite3.Connection) -> Dict[str, List[MessageModel]]:
    rows = db.execute("SELECT * FROM messages ORDER BY rowid").fetchall()
    out: Dict[str, List[MessageModel]] = {}
    for row in rows:
        out.setdefault(row["conversationId"], []).append(format_message(row))
    return out


def build_conversation_model(row: sqlite3.Row, messages: Dict[str, List[MessageModel]]) -> ConversationModel:
    data = dict(row)
    return ConversationModel(
        id=data["id"], contactName=data["contactName"], profileId=data["profileId"], profileName=data["profileName"],
        keyword=data["keyword"], originGroup=_normalize_origin_group_name(data["originGroup"]) or "Skool Inbox", lastMessage=data["lastMessage"], lastMessageTime=data["lastMessageTime"],
        unread=bool(data["unread"]), labelId=data["labelId"], isArchived=bool(data["isArchived"]), isDeletedUi=bool(data["isDeletedUi"]),
        aiAutoEnabled=bool(data.get("aiAutoEnabled", 0)),
        contactInfo=ContactInfoModel(**parse_json_field(data["contactInfo"], {})),
        commentAttribution=CommentAttributionModel(**parse_json_field(data["commentAttribution"], {})),
        keywordContext=KeywordContextModel(**parse_json_field(data["keywordContext"], {})),
        messages=messages.get(data["id"], []),
    )


def build_profile_model(row: sqlite3.Row) -> ProfileModel:
    data = dict(row)
    groups_connected = data["groupsConnectedCalc"] if "groupsConnectedCalc" in data else data["groupsConnected"]
    proxy_key = _normalize_proxy_key(data.get("proxy"))
    proxy_cached = _PROXY_STATUS_CACHE.get(proxy_key) if proxy_key else None
    proxy_status = str((proxy_cached or {}).get("status") or "").strip() or None
    return ProfileModel(
        id=data["id"],
        name=data["name"],
        password=decrypt_secret(data.get("password")),
        email=data["email"],
        proxy=data["proxy"],
        avatar=data["avatar"],
        status=data["status"],
        dailyUsage=data["dailyUsage"],
        groupsConnected=groups_connected,
        hasPassword=bool(str(data.get("password") or "").strip()),
        proxyStatus=proxy_status,
    )


@app.get("/health")
async def health_check(request: Request):
    engine: AutomationEngine = get_automation_engine(request)
    status = await engine.get_status()
    if not status.get("isRunning"):
        return JSONResponse({"status": "ok", "running": False})
    hb_path = engine.heartbeat_file
    try:
        data = json.loads(hb_path.read_text(encoding="utf-8"))
        age = time.time() - float(data.get("ts", 0))
        if age < 120:
            return JSONResponse({"status": "ok", "running": True, "heartbeat_age_seconds": round(age, 1)})
        return JSONResponse(
            {"status": "error", "reason": f"heartbeat stale: age={round(age, 1)}s > 120s"},
            status_code=500,
        )
    except FileNotFoundError:
        return JSONResponse(
            {"status": "error", "reason": "heartbeat file not found — scheduler may not have run yet"},
            status_code=500,
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "reason": str(exc)},
            status_code=500,
        )


@app.get("/profiles", response_model=List[ProfileModel])
def read_profiles():
    _reset_daily_counters_if_needed_for_api()
    with get_db() as db:
        rows = db.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM communities c WHERE c.profileId = p.id) AS groupsConnectedCalc
            FROM profiles p
            ORDER BY p.name
            """
        ).fetchall()
    return [build_profile_model(row) for row in rows]


@app.post("/profiles", response_model=ProfileModel)
async def create_profile(payload: ProfileCreateModel, request: Request):
    profile_id = str(uuid.uuid4())
    email = (payload.email or "").strip()
    password_plain = (payload.password or "").strip()
    if not email or not password_plain:
        raise HTTPException(400, "email and password are required")
    password_encrypted = encrypt_secret(password_plain)
    name = (payload.name or "").strip() or email.split("@")[0] or email
    username = email
    proxy_value = str(payload.proxy or "").strip() or None
    avatar = payload.avatar or "".join([part[0] for part in name.split() if part]).upper()[:2] or "NA"
    requested_status = str(payload.status or "").strip().lower()
    initial_status = "checking" if requested_status in {"", "idle", "checking"} else str(payload.status)
    with get_db() as db:
        db.execute("INSERT INTO profiles (id, name, username, password, email, proxy, avatar, status, dailyUsage, groupsConnected) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (profile_id, name, username, password_encrypted, email, proxy_value, avatar, initial_status, payload.dailyUsage, payload.groupsConnected))

        # Re-link historical comment events from previously deleted profiles with same identity.
        identity_key = str(username or email).strip().lower()
        if identity_key:
            old_rows = db.execute(
                """
                SELECT oldProfileId, name
                FROM deleted_profile_history
                WHERE lower(username) = ? OR lower(COALESCE(email, '')) = ?
                """,
                (identity_key, identity_key),
            ).fetchall()
            old_ids = [str(row["oldProfileId"] or "").strip() for row in old_rows if str(row["oldProfileId"] or "").strip()]
            old_names = [str(row["name"] or "").strip() for row in old_rows if str(row["name"] or "").strip()]
            if old_ids:
                placeholders = ",".join(["?"] * len(old_ids))
                db.execute(
                    f"UPDATE automation_comment_events SET profileId = ?, profile = ? WHERE profileId IN ({placeholders})",
                    (profile_id, name, *old_ids),
                )
                # Restore hidden inbox conversations for the same account.
                db.execute(
                    f"UPDATE conversations SET profileId = ?, profileName = ? WHERE profileId IN ({placeholders})",
                    (profile_id, name, *old_ids),
                )
            if old_names:
                # Re-attach visible activity timeline to the active profile name.
                placeholders = ",".join(["?"] * len(old_names))
                db.execute(
                    f"UPDATE activity_feed SET profile = ? WHERE profile IN ({placeholders})",
                    (name, *old_names),
                )

        db.commit()
        row = db.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM communities c WHERE c.profileId = p.id) AS groupsConnectedCalc
            FROM profiles p
            WHERE p.id = ?
            """,
            (profile_id,),
        ).fetchone()
    return build_profile_model(row)


@app.put("/profiles/{profile_id}", response_model=ProfileModel)
def update_profile(profile_id: str, payload: ProfileUpdateModel):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields provided")
    allowed = ["name", "password", "email", "proxy", "avatar", "status", "dailyUsage", "groupsConnected"]
    if "email" in updates:
        updates["email"] = str(updates.get("email") or "").strip() or None
    if "proxy" in updates:
        updates["proxy"] = str(updates.get("proxy") or "").strip() or None
    if "password" in updates:
        password_plain = str(updates.get("password") or "").strip()
        if not password_plain:
            raise HTTPException(400, "password cannot be empty")
        updates["password"] = encrypt_secret(password_plain)
    clauses = [f"{field} = ?" for field in updates if field in allowed]
    params = [updates[field] for field in updates if field in allowed]
    if not clauses:
        raise HTTPException(400, "No valid fields")
    with get_db() as db:
        if not db.execute("SELECT id FROM profiles WHERE id = ?", (profile_id,)).fetchone():
            raise HTTPException(404, "Profile not found")
        db.execute(f"UPDATE profiles SET {', '.join(clauses)} WHERE id = ?", (*params, profile_id))
        db.commit()
        row = db.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM communities c WHERE c.profileId = p.id) AS groupsConnectedCalc
            FROM profiles p
            WHERE p.id = ?
            """,
            (profile_id,),
        ).fetchone()
    return build_profile_model(row)


@app.post("/profiles/{profile_id}/reset-counters")
def reset_profile_counters(profile_id: str):
    with get_db() as db:
        if not db.execute("SELECT id FROM profiles WHERE id = ?", (profile_id,)).fetchone():
            raise HTTPException(404, "Profile not found")
        _db_execute_with_retry(db, "UPDATE profiles SET dailyUsage = 0 WHERE id = ?", (profile_id,))
        _db_execute_with_retry(
            db,
            "UPDATE communities SET actionsToday = 0, matchesToday = 0 WHERE profileId = ?",
            (profile_id,),
        )
        _db_commit_with_retry(db)
    return {"success": True}


@app.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str):
    with get_db() as db:
        profile_row = db.execute(
            "SELECT id, name, email, username FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if not profile_row:
            raise HTTPException(404, "Profile not found")

        profile_name = str(profile_row["name"] or "").strip()
        profile_email = str(profile_row["email"] or "").strip()
        profile_username = str(profile_row["username"] or "").strip() or profile_email

        # Keep chats/history in DB, but they become hidden because profile no longer exists.

        # Remove deleted profile from keyword rule assignments.
        rule_rows = db.execute("SELECT id, assignedProfileIds FROM keyword_rules").fetchall()
        for rule_row in rule_rows:
            assigned = parse_json_field(str(rule_row["assignedProfileIds"] or "[]"), [])
            filtered = [str(item) for item in assigned if str(item).strip() and str(item).strip() != profile_id]
            if len(filtered) != len(assigned):
                db.execute(
                    "UPDATE keyword_rules SET assignedProfileIds = ? WHERE id = ?",
                    (json.dumps(filtered), str(rule_row["id"])),
                )

        db.execute("DELETE FROM communities WHERE profileId = ?", (profile_id,))
        db.execute("DELETE FROM queue_items WHERE profileId = ?", (profile_id,))
        db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

        # Keep a tombstone so history can be re-linked if the same account is added again.
        db.execute(
            """
            INSERT INTO deleted_profile_history (oldProfileId, username, email, name, deletedAt)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(oldProfileId) DO UPDATE SET
              username = excluded.username,
              email = excluded.email,
              name = excluded.name,
              deletedAt = excluded.deletedAt
            """,
            (profile_id, profile_username, profile_email, profile_name, datetime.now().isoformat()),
        )
        db.commit()

    # Remove local browser/session files for the deleted profile.
    profile_dir = Path(__file__).parent / "skool_accounts" / profile_id
    try:
        shutil.rmtree(profile_dir, ignore_errors=True)
    except Exception:
        pass

    return {"success": True}


def _snapshot_community_fetch_status() -> Dict[str, Any]:
    with _COMMUNITY_FETCH_STATE_LOCK:
        snap = dict(_COMMUNITY_FETCH_STATE)
    return snap


def _set_community_fetch_state(**patch: Any) -> None:
    with _COMMUNITY_FETCH_STATE_LOCK:
        _COMMUNITY_FETCH_STATE.update(patch)


# Run community sync in background so UI can track stable progress across page reloads.
def _run_communities_fetch_job() -> None:
    total_fetched = 0
    total_joined = 0
    total_pending_excluded = 0
    total_unknown_excluded = 0
    results: List[CommunityFetchProfileResultModel] = []
    total_discovered = 0
    total_created = 0
    total_updated = 0
    total_skipped = 0
    total_deleted = 0
    try:
        with get_db() as db:
            profile_rows = db.execute(
                "SELECT id, name, proxy, cookie_json FROM profiles ORDER BY name"
            ).fetchall()
            _set_community_fetch_state(
                profilesTotal=len(profile_rows),
                profilesDone=0,
                currentProfileId="",
                currentProfileName="",
                lastError="",
            )

            for idx, profile in enumerate(profile_rows):
                profile_id = str(profile["id"] or "").strip()
                profile_name = str(profile["name"] or "").strip() or profile_id
                proxy = str(profile["proxy"] or "").strip() or None
                _set_community_fetch_state(
                    currentProfileId=profile_id,
                    currentProfileName=profile_name,
                )
                cookie_json = (profile["cookie_json"] or "").strip() if profile["cookie_json"] else None
                discovered_items, error, stats = _fetch_skool_communities_for_profile(
                    profile_id=profile_id, profile_name=profile_name, proxy=proxy, cookie_json=cookie_json
                )
                discovered_count = len(discovered_items)
                st = stats or {}
                prof_total = st.get("total") or 0
                prof_joined = st.get("joined") or 0
                prof_pending = st.get("pending_excluded") or 0
                prof_unknown = st.get("unknown_excluded") or 0
                total_fetched += prof_total
                total_joined += prof_joined
                total_pending_excluded += prof_pending
                total_unknown_excluded += prof_unknown
                created = 0
                updated = 0
                skipped = 0
                deleted = 0
                if not error:
                    created, updated, skipped = _upsert_profile_communities_from_sync(
                        db=db,
                        profile_id=profile_id,
                        discovered=discovered_items,
                    )
                    deleted = _prune_profile_communities_from_sync(
                        db=db,
                        profile_id=profile_id,
                        discovered=discovered_items,
                    )
                total_discovered += discovered_count
                total_created += created
                total_updated += updated
                total_skipped += skipped
                total_deleted += deleted
                results.append(
                    CommunityFetchProfileResultModel(
                        profileId=profile_id,
                        profileName=profile_name,
                        discovered=discovered_count,
                        created=created,
                        updated=updated,
                        skipped=skipped,
                        total=prof_total if stats else None,
                        joined=prof_joined if stats else None,
                        pendingExcluded=prof_pending if stats else None,
                        unknownExcluded=prof_unknown if stats else None,
                        error=error,
                    )
                )
                _insert_backend_log(
                    db=db,
                    profile=profile_name,
                    status="error" if error else "success",
                    message=(
                        f"Communities sync failed: {error}"
                        if error
                        else (
                            f"Communities sync finished: discovered={discovered_count}, created={created}, "
                            f"updated={updated}, deleted={deleted}"
                        )
                    ),
                )
                _set_community_fetch_state(profilesDone=idx + 1)
            db.commit()

        payload = CommunityFetchResponseModel(
            success=True,
            profilesProcessed=len(results),
            discovered=total_discovered,
            created=total_created,
            updated=total_updated,
            skipped=total_skipped,
            totalFetched=total_fetched,
            totalJoined=total_joined,
            totalPendingExcluded=total_pending_excluded,
            totalUnknownExcluded=total_unknown_excluded,
            results=results,
        )
        _set_community_fetch_state(
            running=False,
            finishedAt=datetime.now().isoformat(),
            currentProfileId="",
            currentProfileName="",
            lastError="",
            lastResult=payload.model_dump(),
        )
    except Exception as exc:
        LOGGER.exception("Communities fetch job failed")
        _set_community_fetch_state(
            running=False,
            finishedAt=datetime.now().isoformat(),
            currentProfileId="",
            currentProfileName="",
            lastError=str(exc)[:400],
        )
    finally:
        _COMMUNITY_FETCH_LOCK.release()


@app.get("/communities", response_model=List[CommunityModel])
def read_communities(profile_id: Optional[str] = None):
    _reset_daily_counters_if_needed_for_api()
    query = "SELECT * FROM communities"
    params: List[Any] = []
    if profile_id:
        query += " WHERE profileId = ?"
        params.append(profile_id)
    with get_db() as db:
        rows = db.execute(query + " ORDER BY name", params).fetchall()
    return [CommunityModel(**dict(row)) for row in rows]


@app.get("/communities/fetch-status", response_model=CommunityFetchStatusModel)
def communities_fetch_status():
    state = _snapshot_community_fetch_status()
    result_payload = state.get("lastResult")
    last_result = CommunityFetchResponseModel(**result_payload) if isinstance(result_payload, dict) else None
    return CommunityFetchStatusModel(
        running=bool(state.get("running")),
        startedAt=str(state.get("startedAt") or ""),
        finishedAt=str(state.get("finishedAt") or ""),
        profilesTotal=int(state.get("profilesTotal") or 0),
        profilesDone=int(state.get("profilesDone") or 0),
        currentProfileId=str(state.get("currentProfileId") or ""),
        currentProfileName=str(state.get("currentProfileName") or ""),
        lastError=str(state.get("lastError") or ""),
        lastResult=last_result,
    )


@app.post("/communities/fetch", response_model=CommunityFetchStatusModel)
def fetch_communities():
    if not _COMMUNITY_FETCH_LOCK.acquire(blocking=False):
        return communities_fetch_status()
    _set_community_fetch_state(
        running=True,
        startedAt=datetime.now().isoformat(),
        finishedAt="",
        profilesTotal=0,
        profilesDone=0,
        currentProfileId="",
        currentProfileName="",
        lastError="",
    )
    worker = threading.Thread(
        target=_run_communities_fetch_job,
        name="communities-fetch",
        daemon=True,
    )
    worker.start()
    return communities_fetch_status()


@app.post("/communities", response_model=CommunityModel)
def create_community(payload: CommunityCreateModel):
    community_id = str(uuid.uuid4())
    with get_db() as db:
        if not db.execute("SELECT id FROM profiles WHERE id = ?", (payload.profileId,)).fetchone():
            raise HTTPException(404, "Profile not found")
        db.execute("INSERT INTO communities (id, profileId, name, url, dailyLimit, maxPostAgeDays, lastScanned, status, matchesToday, actionsToday, totalScannedPosts, totalKeywordMatches) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (community_id, payload.profileId, payload.name, payload.url, payload.dailyLimit, max(0, int(payload.maxPostAgeDays or 0)), payload.lastScanned or now_display_time(), payload.status, payload.matchesToday, payload.actionsToday, payload.totalScannedPosts, payload.totalKeywordMatches))
        db.commit()
        row = db.execute("SELECT * FROM communities WHERE id = ?", (community_id,)).fetchone()
    return CommunityModel(**dict(row))


@app.put("/communities/{community_id}", response_model=CommunityModel)
def update_community(community_id: str, payload: CommunityUpdateModel):
    updates = payload.model_dump(exclude_unset=True)
    if "maxPostAgeDays" in updates:
        updates["maxPostAgeDays"] = max(0, int(updates.get("maxPostAgeDays") or 0))
    if not updates:
        raise HTTPException(400, "No fields provided")
    allowed = ["profileId", "name", "url", "dailyLimit", "maxPostAgeDays", "lastScanned", "status", "matchesToday", "actionsToday", "totalScannedPosts", "totalKeywordMatches"]
    clauses = [f"{field} = ?" for field in updates if field in allowed]
    params = [updates[field] for field in updates if field in allowed]
    if not clauses:
        raise HTTPException(400, "No valid fields")
    with get_db() as db:
        if not db.execute("SELECT id FROM communities WHERE id = ?", (community_id,)).fetchone():
            raise HTTPException(404, "Community not found")
        if "profileId" in updates and not db.execute("SELECT id FROM profiles WHERE id = ?", (updates["profileId"],)).fetchone():
            raise HTTPException(404, "Profile not found")
        db.execute(f"UPDATE communities SET {', '.join(clauses)} WHERE id = ?", (*params, community_id))
        db.commit()
        row = db.execute("SELECT * FROM communities WHERE id = ?", (community_id,)).fetchone()
    return CommunityModel(**dict(row))


@app.delete("/communities/{community_id}")
def delete_community(community_id: str):
    with get_db() as db:
        if not db.execute("SELECT id FROM communities WHERE id = ?", (community_id,)).fetchone():
            raise HTTPException(404, "Community not found")
        db.execute("DELETE FROM queue_items WHERE communityId = ?", (community_id,))
        db.execute("DELETE FROM communities WHERE id = ?", (community_id,))
        db.commit()
    return {"success": True}


@app.get("/labels", response_model=List[LabelModel])
def read_labels():
    with get_db() as db:
        rows = db.execute("SELECT * FROM labels ORDER BY name").fetchall()
    return [LabelModel(**dict(row)) for row in rows]


@app.post("/labels", response_model=LabelModel)
def create_label(payload: LabelCreateModel):
    label_id = str(uuid.uuid4())
    with get_db() as db:
        db.execute("INSERT INTO labels (id, name, color) VALUES (?, ?, ?)", (label_id, payload.name, payload.color))
        db.commit()
        row = db.execute("SELECT * FROM labels WHERE id = ?", (label_id,)).fetchone()
    return LabelModel(**dict(row))


@app.put("/labels/{label_id}", response_model=LabelModel)
def update_label(label_id: str, payload: LabelUpdateModel):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields provided")
    clauses = [f"{field} = ?" for field in updates if field in {"name", "color"}]
    params = [updates[field] for field in updates if field in {"name", "color"}]
    if not clauses:
        raise HTTPException(400, "No valid fields")
    with get_db() as db:
        if not db.execute("SELECT id FROM labels WHERE id = ?", (label_id,)).fetchone():
            raise HTTPException(404, "Label not found")
        db.execute(f"UPDATE labels SET {', '.join(clauses)} WHERE id = ?", (*params, label_id))
        db.commit()
        row = db.execute("SELECT * FROM labels WHERE id = ?", (label_id,)).fetchone()
    return LabelModel(**dict(row))


@app.delete("/labels/{label_id}")
def delete_label(label_id: str):
    with get_db() as db:
        if not db.execute("SELECT id FROM labels WHERE id = ?", (label_id,)).fetchone():
            raise HTTPException(404, "Label not found")
        db.execute("UPDATE conversations SET labelId = NULL WHERE labelId = ?", (label_id,))
        db.execute("DELETE FROM labels WHERE id = ?", (label_id,))
        db.commit()
    return {"success": True}


@app.get("/keyword-rules", response_model=List[KeywordRuleModel])
def read_keyword_rules():
    with get_db() as db:
        rows = db.execute("SELECT * FROM keyword_rules ORDER BY keyword").fetchall()
    return [KeywordRuleModel(id=row["id"], keyword=row["keyword"], persona=row["persona"], promptPreview=row["promptPreview"], commentPrompt=row["commentPrompt"], dmPrompt=row["dmPrompt"], dmMaxReplies=row["dmMaxReplies"], dmReplyDelay=row["dmReplyDelay"], active=bool(row["active"]), assignedProfileIds=parse_json_field(row["assignedProfileIds"], [])) for row in rows]


@app.post("/keyword-rules", response_model=KeywordRuleModel)
def create_keyword_rule(payload: KeywordRuleCreateModel):
    rule_id = str(uuid.uuid4())
    with get_db() as db:
        db.execute("INSERT INTO keyword_rules (id, keyword, persona, promptPreview, commentPrompt, dmPrompt, dmMaxReplies, dmReplyDelay, active, assignedProfileIds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (rule_id, payload.keyword, payload.persona, payload.promptPreview, payload.commentPrompt, payload.dmPrompt, payload.dmMaxReplies, payload.dmReplyDelay, bool_to_int(payload.active), json.dumps(payload.assignedProfileIds)))
        db.commit()
        row = db.execute("SELECT * FROM keyword_rules WHERE id = ?", (rule_id,)).fetchone()
    return KeywordRuleModel(id=row["id"], keyword=row["keyword"], persona=row["persona"], promptPreview=row["promptPreview"], commentPrompt=row["commentPrompt"], dmPrompt=row["dmPrompt"], dmMaxReplies=row["dmMaxReplies"], dmReplyDelay=row["dmReplyDelay"], active=bool(row["active"]), assignedProfileIds=parse_json_field(row["assignedProfileIds"], []))


@app.put("/keyword-rules/{rule_id}", response_model=KeywordRuleModel)
def update_keyword_rule(rule_id: str, payload: KeywordRuleUpdateModel):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields provided")
    if "active" in updates:
        updates["active"] = bool_to_int(updates["active"])
    if "assignedProfileIds" in updates:
        updates["assignedProfileIds"] = json.dumps(updates["assignedProfileIds"])
    allowed = ["keyword", "persona", "promptPreview", "commentPrompt", "dmPrompt", "dmMaxReplies", "dmReplyDelay", "active", "assignedProfileIds"]
    clauses = [f"{field} = ?" for field in updates if field in allowed]
    params = [updates[field] for field in updates if field in allowed]
    if not clauses:
        raise HTTPException(400, "No valid fields")
    with get_db() as db:
        if not db.execute("SELECT id FROM keyword_rules WHERE id = ?", (rule_id,)).fetchone():
            raise HTTPException(404, "Keyword rule not found")
        db.execute(f"UPDATE keyword_rules SET {', '.join(clauses)} WHERE id = ?", (*params, rule_id))
        db.commit()
        row = db.execute("SELECT * FROM keyword_rules WHERE id = ?", (rule_id,)).fetchone()
    return KeywordRuleModel(id=row["id"], keyword=row["keyword"], persona=row["persona"], promptPreview=row["promptPreview"], commentPrompt=row["commentPrompt"], dmPrompt=row["dmPrompt"], dmMaxReplies=row["dmMaxReplies"], dmReplyDelay=row["dmReplyDelay"], active=bool(row["active"]), assignedProfileIds=parse_json_field(row["assignedProfileIds"], []))


@app.delete("/keyword-rules/{rule_id}")
def delete_keyword_rule(rule_id: str):
    with get_db() as db:
        if not db.execute("SELECT id FROM keyword_rules WHERE id = ?", (rule_id,)).fetchone():
            raise HTTPException(404, "Keyword rule not found")
        db.execute("DELETE FROM keyword_rules WHERE id = ?", (rule_id,))
        db.commit()
    return {"success": True}


@app.get("/automation-settings", response_model=AutomationSettingsModel)
@app.get("/automation/settings", response_model=AutomationSettingsModel)
def get_automation_settings():
    with get_db() as db:
        return _load_or_create_automation_settings(db)


@app.put("/automation-settings", response_model=AutomationSettingsModel)
@app.put("/automation/settings", response_model=AutomationSettingsModel)
def update_automation_settings(payload: AutomationSettingsModel):
    with get_db() as db:
        _load_or_create_automation_settings(db)
        db.execute("DELETE FROM automation_settings WHERE key != 'default'")
        db.execute(
            "INSERT INTO automation_settings (key, value) VALUES ('default', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (payload.model_dump_json(),),
        )
        db.commit()
    return payload


@app.get("/queue", response_model=List[QueueItemModel])
def read_queue(profile_id: Optional[str] = None):
    query = "SELECT * FROM queue_items"
    params: List[Any] = []
    if profile_id:
        query += " WHERE profileId = ?"
        params.append(profile_id)
    with get_db() as db:
        rows = db.execute(query + " ORDER BY julianday(scheduledFor) ASC, id ASC", params).fetchall()
    ordered_rows = [dict(row) for row in rows]
    # Dashboard readability: interleave queue by profile while preserving
    # per-profile scheduled order to reflect round-robin intent.
    by_profile: Dict[str, List[Dict[str, Any]]] = {}
    profile_order: List[str] = []
    for row in ordered_rows:
        pid = str(row.get("profileId") or "")
        if pid not in by_profile:
            by_profile[pid] = []
            profile_order.append(pid)
        by_profile[pid].append(row)
    interleaved: List[Dict[str, Any]] = []
    while True:
        took_any = False
        for pid in profile_order:
            bucket = by_profile.get(pid) or []
            if not bucket:
                continue
            interleaved.append(bucket.pop(0))
            took_any = True
        if not took_any:
            break
    return [QueueItemModel(**_queue_row_to_api_payload(row)) for row in interleaved]


@app.put("/queue/{item_id}", response_model=QueueItemModel)
def update_queue_item(item_id: str, payload: QueueItemUpdateModel):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields provided")
    allowed = ["profile", "profileId", "community", "communityId", "postId", "keyword", "keywordId", "scheduledTime", "scheduledFor", "priorityScore", "countdown"]
    with get_db() as db:
        row = db.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Queue item not found")

        if "scheduledTime" in updates and "scheduledFor" not in updates:
            raise HTTPException(400, "scheduledTime update requires scheduledFor")

        if "scheduledFor" in updates:
            try:
                scheduled_dt = _parse_queue_scheduled_for(str(updates.get("scheduledFor") or ""))
            except Exception:
                raise HTTPException(400, "Invalid scheduledFor format")
            updates["scheduledFor"] = scheduled_dt.isoformat(timespec="seconds")
            updates["scheduledTime"] = _format_queue_display_time(scheduled_dt)
            delta_seconds = int((scheduled_dt - datetime.now()).total_seconds())
            updates["countdown"] = max(0, delta_seconds)
            updates["priorityScore"] = max(1, int(delta_seconds // 60) * -1 + 100)

        if "postId" in updates:
            post_id = str(updates.get("postId") or "").strip()
            if not post_id:
                raise HTTPException(400, "postId cannot be empty")
            updates["postId"] = post_id

        clauses = [f"{field} = ?" for field in updates if field in allowed]
        params = [updates[field] for field in updates if field in allowed]
        if not clauses:
            raise HTTPException(400, "No valid fields")
        db.execute(f"UPDATE queue_items SET {', '.join(clauses)} WHERE id = ?", (*params, item_id))
        db.commit()
        row = db.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
    return QueueItemModel(**_queue_row_to_api_payload(dict(row)))


@app.post("/queue/{item_id}/start-soon", response_model=QueueItemModel)
def queue_item_start_soon(item_id: str, seconds: int = 10):
    delay_seconds = max(3, min(300, int(seconds or 10)))
    target_dt = datetime.now() + timedelta(seconds=delay_seconds)
    scheduled_for = target_dt.isoformat(timespec="seconds")
    scheduled_time = _format_queue_display_time(target_dt)
    delta_seconds = int((target_dt - datetime.now()).total_seconds())
    countdown = max(0, delta_seconds)
    priority = max(1, int(delta_seconds // 60) * -1 + 100)
    with get_db() as db:
        row = db.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Queue item not found")
        db.execute(
            """
            UPDATE queue_items
            SET scheduledFor = ?, scheduledTime = ?, countdown = ?, priorityScore = ?
            WHERE id = ?
            """,
            (scheduled_for, scheduled_time, countdown, priority, item_id),
        )
        refreshed = db.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
        profile_name = str((refreshed or row)["profile"] or "SYSTEM")
        _insert_backend_log(
            db,
            profile_name,
            "info",
            f"[SKOOL] Queue task expedited task={item_id} start_in={delay_seconds}s",
        )
        db.commit()
    return QueueItemModel(**_queue_row_to_api_payload(dict(refreshed)))


@app.delete("/queue/{item_id}")
def delete_queue_item(item_id: str):
    with get_db() as db:
        row = db.execute("SELECT profile, community FROM queue_items WHERE id = ?", (item_id,)).fetchone()
        profile_name = str((row["profile"] if row else "") or "SYSTEM")
        db.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
        _insert_backend_log(
            db,
            profile_name,
            "success",
            f"[SKOOL] Queue task removed task={item_id} community={str((row['community'] if row else '') or 'community')}",
            module="queue",
            action="remove",
        )
        db.commit()
    return {"success": True}


@app.get("/logs", response_model=List[LogEntryModel])
def read_logs(profile: Optional[str] = None, status: Optional[str] = None, limit: int = 500):
    query = "SELECT * FROM logs"
    filters: List[str] = []
    params: List[Any] = []
    safe_limit = max(50, min(2000, int(limit or 500)))
    if not DETAILED_TRACE_LOGS_ENABLED:
        filters.append("message NOT LIKE ?")
        params.append("[SKOOL][TRACE]%")
        filters.append("message NOT LIKE ?")
        params.append("[TRACE]%")
    if profile:
        filters.append("profile = ?")
        params.append(profile)
    if status:
        filters.append("status = ?")
        params.append(status)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    with get_db() as db:
        rows = db.execute(query + " ORDER BY rowid DESC LIMIT ?", (*params, safe_limit)).fetchall()
    return [LogEntryModel(**dict(row)) for row in rows]


@app.delete("/logs")
def clear_logs():
    with get_db() as db:
        row = db.execute("SELECT COUNT(*) AS cnt FROM logs").fetchone()
        deleted = int(row["cnt"] if row and "cnt" in row.keys() else 0)
        db.execute("DELETE FROM logs")
        db.commit()
    return {"success": True, "deleted": deleted}


@app.get("/activity", response_model=List[ActivityEntryModel])
def read_activity(profile: Optional[str] = None):
    # Show timeline only for currently active profiles.
    query = "SELECT * FROM activity_feed WHERE profile IN (SELECT name FROM profiles)"
    params: List[Any] = []
    if profile:
        query += " AND profile = ?"
        params.append(profile)
    with get_db() as db:
        rows = db.execute(query + " ORDER BY rowid DESC", params).fetchall()
    return [ActivityEntryModel(**dict(row)) for row in rows]


@app.post("/maintenance/backfill-dm-activity")
def backfill_dm_activity(limit: int = 5000):
    safe_limit = max(100, min(50000, int(limit or 5000)))
    with get_db() as db:
        result = _backfill_dm_activity_from_logs(db, limit=safe_limit)
    return {"success": True, **result}


@app.post("/automation/reset-tasks")
def automation_reset_tasks():
    with get_db() as db:
        queue_row = db.execute("SELECT COUNT(*) AS cnt FROM queue_items").fetchone()
        queue_deleted = int(queue_row["cnt"] if queue_row and "cnt" in queue_row.keys() else 0)
        db.execute("DELETE FROM queue_items")
        db.commit()
    return {
        "success": True,
        "queueDeleted": queue_deleted,
        "activityDeleted": 0,
        "analyticsDeleted": 0,
    }


@app.get("/analytics", response_model=AnalyticsPayload)
def read_analytics():
    with get_db() as db:
        computed = _compute_live_analytics_payload(db)
    return AnalyticsPayload(**computed)


@app.get("/conversations", response_model=List[ConversationModel])
def read_conversations(profile_id: Optional[str] = None, sync: bool = False):
    # Hide orphaned chats while the related profile is deleted.
    query = "SELECT c.rowid AS _rowid, c.* FROM conversations c INNER JOIN profiles p ON p.id = c.profileId"
    params: List[Any] = []
    if profile_id:
        query += " WHERE c.profileId = ?"
        params.append(profile_id)
    with get_db() as db:
        if sync:
            _sync_skool_chats_to_inbox(db, force=True)
        messages = build_message_map(db)
        rows = db.execute(query, params).fetchall()

    def _conversation_rank(row: sqlite3.Row) -> Tuple[float, int]:
        raw_ts = str(row["lastMessageTime"] or "").strip()
        dt = _parse_chat_datetime(raw_ts) or _parse_analytics_datetime(raw_ts)
        ts = 0.0
        if dt:
            try:
                ts = float(dt.timestamp())
            except Exception:
                ts = 0.0
        try:
            rowid = int(row["_rowid"] or 0)
        except Exception:
            rowid = 0
        return (ts, rowid)

    rows = sorted(rows, key=_conversation_rank, reverse=True)
    unique_rows: List[sqlite3.Row] = []
    seen_keys: Set[str] = set()
    for row in rows:
        identity = _conversation_chat_identity(row)
        key = f"{identity[0]}::{identity[1]}" if identity else str(row["id"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_rows.append(row)
    return [build_conversation_model(row, messages) for row in unique_rows]


@app.patch("/conversations/{conversation_id}", response_model=ConversationModel)
def patch_conversation(conversation_id: str, payload: ConversationPatchModel):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "No fields provided")
    if "isArchived" in updates:
        updates["isArchived"] = bool_to_int(updates["isArchived"])
    if "isDeletedUi" in updates:
        updates["isDeletedUi"] = bool_to_int(updates["isDeletedUi"])
    if "unread" in updates:
        updates["unread"] = bool_to_int(updates["unread"])
    if "aiAutoEnabled" in updates:
        updates["aiAutoEnabled"] = bool_to_int(updates["aiAutoEnabled"])
    clauses = [f"{field} = ?" for field in updates if field in {"labelId", "isArchived", "isDeletedUi", "unread", "aiAutoEnabled"}]
    params = [updates[field] for field in updates if field in {"labelId", "isArchived", "isDeletedUi", "unread", "aiAutoEnabled"}]
    if not clauses:
        raise HTTPException(400, "No valid fields")
    with get_db() as db:
        existing_row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not existing_row:
            raise HTTPException(404, "Conversation not found")
        db.execute(f"UPDATE conversations SET {', '.join(clauses)} WHERE id = ?", (*params, conversation_id))
        db.commit()
        should_try_ai_auto_now = (
            "aiAutoEnabled" in updates
            and bool(updates["aiAutoEnabled"])
            and not bool(existing_row["aiAutoEnabled"])
        )
        if should_try_ai_auto_now:
            _try_ai_auto_reply(
                db=db,
                conversation_id=conversation_id,
                require_message_changed=False,
                message_changed=True,
                trigger_reason="manual_toggle_enabled",
            )
            db.commit()
        row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        messages = build_message_map(db)
    return build_conversation_model(row, messages)


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    with get_db() as db:
        if not db.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
            raise HTTPException(404, "Conversation not found")
        db.execute("UPDATE conversations SET isDeletedUi = 1 WHERE id = ?", (conversation_id,))
        db.commit()
    return {"success": True}


@app.post("/conversations/{conversation_id}/ai-suggest", response_model=ConversationAISuggestResponse)
def conversation_ai_suggest(conversation_id: str, payload: ConversationAISuggestRequest, request: Request):
    with get_db() as db:
        return _generate_conversation_ai_suggest(db, conversation_id, payload.tone)


@app.post("/conversations/{conversation_id}/messages", response_model=ConversationModel)
def add_message(conversation_id: str, payload: MessageCreateModel):
    message_id = str(uuid.uuid4())
    ts = payload.timestamp or datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Conversation not found")
        is_skool_import = conversation_id.startswith(SKOOL_CHAT_IMPORT_PREFIX)
        if is_skool_import and payload.sender == "outbound":
            payload_text = str(payload.text or "").strip()
            if not payload_text:
                raise HTTPException(400, "Message text is required")
            if not _reserve_skool_send_dedupe(conversation_id, payload_text):
                _insert_backend_log(
                    db,
                    str(row["profileName"] or "SYSTEM"),
                    "retry",
                    f"DM send skipped: duplicate request blocked for {str(row['contactName'] or '').strip() or 'contact'}",
                )
                row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
                messages = build_message_map(db)
                return build_conversation_model(row, messages)
            _insert_backend_log(
                db,
                str(row["profileName"] or "SYSTEM"),
                "info",
                f"DM send started to {str(row['contactName'] or '').strip() or 'contact'}",
            )
            profile_id, chat_id = _extract_skool_chat_ids(conversation_id, str(row["commentAttribution"] or ""))
            if not profile_id or not chat_id:
                comment_attribution = parse_json_field(str(row["commentAttribution"] or ""), {})
                profile_id, chat_id = _extract_skool_chat_ids(conversation_id, str(comment_attribution.get("postUrl") or ""))
            if not chat_id:
                _insert_backend_log(db, str(row["profileName"] or "SYSTEM"), "error", "DM send failed: missing chat id")
                _release_skool_send_dedupe(conversation_id, payload_text)
                raise HTTPException(409, "Skool chat id is missing for this conversation")
            profile_row = (
                db.execute("SELECT id, name, proxy FROM profiles WHERE id = ?", (profile_id,)).fetchone() if profile_id else None
            )
            if not profile_row:
                _insert_backend_log(db, str(row["profileName"] or "SYSTEM"), "error", "DM send failed: profile session missing")
                _release_skool_send_dedupe(conversation_id, payload_text)
                raise HTTPException(409, "Skool profile session is not available for this conversation")
            _insert_backend_log(
                db,
                str(row["profileName"] or "SYSTEM"),
                "info",
                f"DM send target chat_id={chat_id}",
            )
            sent_card = _send_message_to_skool_chat(
                profile_id=profile_row["id"],
                profile_name=profile_row["name"],
                proxy=profile_row["proxy"],
                chat_id=chat_id or "",
                text=payload_text,
                contact_name=str(row["contactName"] or ""),
            )
            if sent_card is None:
                _insert_backend_log(db, str(row["profileName"] or "SYSTEM"), "error", "DM send failed: unable to confirm sent message in Skool")
                _release_skool_send_dedupe(conversation_id, payload_text)
                raise HTTPException(502, "Failed to send message to Skool chat")
            _upsert_skool_chat_card(db, sent_card)
            _update_live_cache_with_skool_card(sent_card)
            db.execute("UPDATE conversations SET unread = 0 WHERE id = ?", (conversation_id,))
            _insert_backend_log(
                db,
                str(row["profileName"] or "SYSTEM"),
                "success",
                f"DM sent to {str(row['contactName'] or '').strip() or 'contact'}",
            )
            try:
                db.execute(
                    "INSERT INTO activity_feed (id, profile, groupName, action, timestamp, postUrl) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        str(row["profileName"] or "SYSTEM"),
                        str(row["originGroup"] or "Skool Inbox"),
                        f"DM sent to {str(row['contactName'] or '').strip() or 'contact'}",
                        datetime.now(timezone.utc).isoformat(),
                        f"https://www.skool.com/chat?ch={chat_id}",
                    ),
                )
            except Exception:
                pass
            db.commit()
            row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        else:
            db.execute(
                "INSERT INTO messages (id, conversationId, text, sender, timestamp, isDeletedUi) VALUES (?, ?, ?, ?, ?, 0)",
                (message_id, conversation_id, payload.text, payload.sender, ts),
            )
            db.execute(
                "UPDATE conversations SET lastMessage = ?, lastMessageTime = ?, unread = ? WHERE id = ?",
                (payload.text, ts, 0 if payload.sender == "outbound" else 1, conversation_id),
            )
            if payload.sender == "outbound":
                raw_comment_attr = str(row["commentAttribution"] or "")
                comment_attr = parse_json_field(raw_comment_attr, {})
                post_url = str(comment_attr.get("postUrl") or "").strip() or "https://www.skool.com/"
                contact_name = str(row["contactName"] or "").strip() or "contact"
                db.execute(
                    "INSERT INTO activity_feed (id, profile, groupName, action, timestamp, postUrl) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        str(row["profileName"] or "SYSTEM"),
                        str(row["originGroup"] or "Inbox"),
                        f"DM sent to {contact_name}",
                        ts,
                        post_url,
                    ),
                )
            db.commit()
            row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        messages = build_message_map(db)
    return build_conversation_model(row, messages)

@app.post("/automation/start")
async def automation_start(payload: AutomationStartRequest, request: Request):
    engine = get_automation_engine(request)
    try:
        return await engine.start(payload.profiles, payload.globalSettings)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/automation/stop")
async def automation_stop(request: Request):
    engine = get_automation_engine(request)
    return await engine.stop()


@app.post("/automation/pause")
async def automation_pause(request: Request):
    engine = get_automation_engine(request)
    try:
        return await engine.pause()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/automation/resume")
async def automation_resume(request: Request):
    engine = get_automation_engine(request)
    try:
        return await engine.resume()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.get("/automation/status")
async def automation_status(request: Request):
    engine = get_automation_engine(request)
    return await engine.get_status()


@app.get("/automation/logs/stream")
async def automation_logs_stream(request: Request):
    engine = get_automation_engine(request)
    queue = await engine.subscribe()

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    if not DETAILED_TRACE_LOGS_ENABLED and _is_trace_log_message(event.get("message")):
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\\n\\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': now_display_time()})}\\n\\n"
        finally:
            await engine.unsubscribe(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


@app.post("/profiles/{profile_id}/check-login")
@app.post("/automation/profiles/{profile_id}/check-login")
async def automation_check_login(profile_id: str, request: Request):
    engine = get_automation_engine(request)
    try:
        result = await engine.check_login(profile_id)
        if isinstance(result, dict) and bool(result.get("success")):
            with get_db() as db:
                row = db.execute("SELECT proxy FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            proxy_key = _normalize_proxy_key(row["proxy"]) if row else ""
            if proxy_key:
                _save_proxy_cache_entry(
                    proxy_key=proxy_key,
                    status="connected",
                    message="Proxy is connected (auto-cached from successful login check)",
                )
        return result
    except RuntimeError as exc:
        raise HTTPException(404, str(exc))
    except Exception:
        return {"success": False, "status": "network_error", "message": "Login check failed"}


@app.post("/profiles/{profile_id}/check-proxy")
@app.post("/automation/profiles/{profile_id}/check-proxy")
async def automation_check_proxy(profile_id: str, request: Request):
    engine = get_automation_engine(request)
    with get_db() as db:
        row = db.execute("SELECT name, proxy FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Profile not found")
        profile_name = str(row["name"] or profile_id or "SYSTEM")
        proxy_key = _normalize_proxy_key(row["proxy"])
    cached = _PROXY_STATUS_CACHE.get(proxy_key) if proxy_key else None
    if cached and str(cached.get("status") or "").strip() == "connected":
        with get_db() as db:
            _insert_backend_log(
                db,
                profile_name,
                "info",
                "Proxy check served from cache: connection is active",
                module="proxy",
                action="cache",
            )
        return {
            "success": True,
            "status": "connected",
            "message": str(cached.get("message") or "Proxy is connected (cached)"),
        }
    try:
        with get_db() as db:
            _insert_backend_log(
                db,
                profile_name,
                "info",
                "Proxy check started",
                module="proxy",
                action="check",
            )
        result = await engine.check_proxy(profile_id)
        if proxy_key and isinstance(result, dict):
            status_value = str(result.get("status") or "").strip()
            if status_value == "connected":
                _save_proxy_cache_entry(
                    proxy_key=proxy_key,
                    status="connected",
                    message=str(result.get("message") or "Proxy is connected"),
                )
        return result
    except RuntimeError as exc:
        raise HTTPException(404, str(exc))
    except Exception:
        if cached and str(cached.get("status") or "").strip() == "connected":
            return {
                "success": True,
                "status": "connected",
                "message": str(cached.get("message") or "Proxy is connected (cached)"),
            }
        return {"success": False, "status": "network_error", "message": "Proxy check failed"}


@app.post("/automation/test-comment")
async def automation_test_comment(payload: TestCommentRequest, request: Request):
    engine = get_automation_engine(request)
    try:
        result = await engine.run_test_comment(profile_id=payload.profileId, community_url=payload.communityUrl, prompt=payload.prompt, api_key=payload.apiKey)
        if result.get("success") and result.get("postUrl"):
            with get_db() as db:
                profile_row = db.execute("SELECT name FROM profiles WHERE id = ?", (payload.profileId,)).fetchone()
                profile_name = (profile_row["name"] if profile_row else str(payload.profileId)).strip() or str(payload.profileId)
                group_name = str(payload.communityUrl or "").strip() or "Test"
                event_id = f"test-comment-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8]}"
                db.execute(
                    "INSERT INTO activity_feed (id, profile, groupName, action, timestamp, postUrl) VALUES (?, ?, ?, ?, ?, ?)",
                    (event_id, profile_name, group_name, "Commented", datetime.now(timezone.utc).isoformat(), result["postUrl"]),
                )
                db.commit()
        return result
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/automation/proof-run")
async def automation_proof_run(payload: ProofRunRequest, request: Request):
    engine = get_automation_engine(request)
    try:
        return await engine.proof_run(payload.profileId, payload.communityUrl)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/automation/test-openai")
def automation_test_openai(payload: OpenAITestRequest):
    api_key = str(payload.apiKey or "").strip()
    if _looks_like_masked_secret(api_key):
        raise HTTPException(400, "Paste full OpenAI API key, not masked value")
    if not api_key:
        api_key = _get_openai_key_for_dm()
    if not api_key:
        raise HTTPException(400, "apiKey is required")
    try:
        with get_db() as db:
            _insert_backend_log(
                db,
                "SYSTEM",
                "info",
                "OpenAI key check started",
                module="openai",
                action="check_key",
            )
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo"),
                "messages": [
                    {"role": "system", "content": "Return a short test response."},
                    {"role": "user", "content": payload.prompt},
                ],
                "max_tokens": 30,
                "temperature": 0,
            },
            timeout=20,
        )
    except requests.Timeout:
        with get_db() as db:
            _insert_backend_log(
                db,
                "SYSTEM",
                "error",
                "OpenAI key check failed: request timed out",
                module="openai",
                action="check_key",
            )
        raise HTTPException(500, "OpenAI request timed out")
    except requests.RequestException:
        with get_db() as db:
            _insert_backend_log(
                db,
                "SYSTEM",
                "error",
                "OpenAI key check failed: request error",
                module="openai",
                action="check_key",
            )
        raise HTTPException(500, "OpenAI request failed")

    if response.status_code == 401:
        with get_db() as db:
            _insert_backend_log(
                db,
                "SYSTEM",
                "error",
                "OpenAI key check failed: invalid API key",
                module="openai",
                action="check_key",
            )
        raise HTTPException(401, "OpenAI rejected request: invalid API key")
    if response.status_code != 200:
        with get_db() as db:
            _insert_backend_log(
                db,
                "SYSTEM",
                "error",
                f"OpenAI key check failed: HTTP {response.status_code}",
                module="openai",
                action="check_key",
            )
        raise HTTPException(response.status_code, "OpenAI request failed")

    data = response.json()
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
    with get_db() as db:
        _insert_backend_log(
            db,
            "SYSTEM",
            "success",
            "OpenAI key check passed: connection is active",
            module="openai",
            action="check_key",
        )
    return {"success": True, "message": "OpenAI connection successful", "response": content}


@app.get("/automation/openai-key")
def automation_get_openai_key(request: Request):
    engine = get_automation_engine(request)
    value = engine._get_openai_key().strip()
    masked = mask_secret(value)
    source = "config"
    env_raw = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_raw and value == env_raw:
        source = "env"
    return {
        "success": True,
        "apiKey": masked,
        "apiKeyMasked": masked,
        "isConfigured": bool(value),
        "source": source,
    }


@app.put("/automation/openai-key")
def automation_set_openai_key(payload: OpenAIKeyUpdateRequest, request: Request):
    engine = get_automation_engine(request)
    api_key = payload.apiKey.strip()
    if not api_key:
        raise HTTPException(400, "apiKey is required")
    if _looks_like_masked_secret(api_key):
        raise HTTPException(400, "Cannot save masked key. Paste full OpenAI API key.")

    current_data: Dict[str, Any] = {}
    if engine.config_file.exists():
        try:
            with engine.config_file.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    current_data = loaded
        except Exception:
            current_data = {}

    current_data["openai_api_key"] = api_key
    with engine.config_file.open("w", encoding="utf-8") as f:
        json.dump(current_data, f, ensure_ascii=False, indent=2)

    return {"success": True, "isConfigured": True}


ensure_tables()
_cleanup_skool_accounts_storage()
try:
    with get_db() as db:
        backfill_result = _backfill_dm_activity_from_logs(db, limit=5000)
    if int(backfill_result.get("inserted", 0) or 0) > 0:
        LOGGER.info(
            "DM activity backfill applied: inserted=%s scanned=%s",
            int(backfill_result.get("inserted", 0) or 0),
            int(backfill_result.get("scanned", 0) or 0),
        )
except Exception:
    LOGGER.exception("DM activity backfill failed during startup")
