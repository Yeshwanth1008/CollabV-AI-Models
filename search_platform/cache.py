"""
In-process TTL cache for search responses. Swap for Redis in production by
replacing this module's get/set with redis-py calls behind the same
interface — call sites (api.py) only use `cache.get(key)` / `cache.set(key, value)`.
"""
import hashlib
import json
import threading
import time
from typing import Optional

from .config import get_settings


class TTLCache:
    def __init__(self):
        self._store: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[object]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: object, ttl_seconds: Optional[int] = None) -> None:
        settings = get_settings()
        ttl = ttl_seconds if ttl_seconds is not None else settings.cache_ttl_seconds
        with self._lock:
            self._store[key] = (time.time() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


def make_key(*parts) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


_cache = TTLCache()


def get_cache() -> TTLCache:
    return _cache
