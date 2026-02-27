from __future__ import annotations

import os
import re
import threading
from typing import Dict, Optional, Tuple

_LOCK = threading.Lock()
_SLOTS: Dict[Tuple[str, str], threading.BoundedSemaphore] = {}

CHAT_LIMIT_PER_PROXY = max(1, int(os.environ.get("PROXY_CHAT_MAX_CONNECTIONS_PER_PROXY", "1")))
QUEUE_LIMIT_PER_PROXY = max(1, int(os.environ.get("PROXY_QUEUE_MAX_CONNECTIONS_PER_PROXY", "2")))
ACQUIRE_TIMEOUT_SECONDS = max(1, int(os.environ.get("PROXY_SLOT_ACQUIRE_TIMEOUT_SECONDS", "120")))


def _normalize_proxy_key(proxy_value: Optional[str]) -> str:
    return re.sub(r"\s+", "", str(proxy_value or "").strip().lower())


def _module_limit(module: str) -> int:
    mod = str(module or "").strip().lower()
    if mod == "chat":
        return CHAT_LIMIT_PER_PROXY
    if mod == "queue":
        return QUEUE_LIMIT_PER_PROXY
    return max(CHAT_LIMIT_PER_PROXY, QUEUE_LIMIT_PER_PROXY)


def acquire_proxy_slot(module: str, proxy_value: Optional[str], timeout_seconds: Optional[int] = None) -> Optional[Tuple[str, str]]:
    proxy_key = _normalize_proxy_key(proxy_value)
    if not proxy_key:
        return None

    mod = str(module or "").strip().lower() or "generic"
    slot_key = (mod, proxy_key)
    limit = _module_limit(mod)
    wait_for = max(1, int(timeout_seconds or ACQUIRE_TIMEOUT_SECONDS))

    with _LOCK:
        semaphore = _SLOTS.get(slot_key)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(limit)
            _SLOTS[slot_key] = semaphore

    acquired = semaphore.acquire(timeout=wait_for)
    if not acquired:
        raise RuntimeError(
            f"proxy_slot_timeout: module={mod} limit={limit} timeout={wait_for}s"
        )
    return slot_key


def release_proxy_slot(slot_handle: Optional[Tuple[str, str]]) -> None:
    if not slot_handle:
        return
    semaphore = None
    with _LOCK:
        semaphore = _SLOTS.get(slot_handle)
    if semaphore is None:
        return
    try:
        semaphore.release()
    except ValueError:
        # Already fully released; ignore duplicate-release edge cases safely.
        pass
