from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..utils.cache_db import CacheStore
from ..utils.settings import get_settings


@dataclass(frozen=True)
class CachePayload:
    value: dict[str, Any]
    expires_at: datetime


def build_hist_cache_key(ticker: str, interval_days: int, start_date: str, end_date: str) -> str:
    return f"hist:{ticker}:{interval_days}:{start_date}:{end_date}"


class TestsCache:
    def __init__(self, ttl_seconds: int, store: CacheStore) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self.store = store
        self._memory: dict[str, CachePayload] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        now = datetime.utcnow()
        in_mem = self._memory.get(key)
        if in_mem and in_mem.expires_at > now:
            return in_mem.value
        cached = self.store.get_cache(key)
        if cached is None:
            self._memory.pop(key, None)
            return None
        expires_at = now + self.ttl
        self._memory[key] = CachePayload(value=cached, expires_at=expires_at)
        return cached

    def set(self, key: str, value: dict[str, Any]) -> None:
        now = datetime.utcnow()
        expires_at = now + self.ttl
        self._memory[key] = CachePayload(value=value, expires_at=expires_at)
        self.store.set_cache(key, value, int(self.ttl.total_seconds()))


_tests_cache: TestsCache | None = None


def get_tests_cache() -> TestsCache:
    global _tests_cache
    if _tests_cache is None:
        settings = get_settings()
        _tests_cache = TestsCache(settings.tests_cache_ttl_seconds, CacheStore(settings.cache_db_url))
    return _tests_cache
