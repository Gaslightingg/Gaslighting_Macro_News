from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass
class CacheEntry:
    value: Any
    expires_at: datetime


def ttl_for_frequency(frequency: str) -> timedelta:
    mapping = {
        "intraday": timedelta(minutes=15),
        "daily": timedelta(hours=6),
        "weekly": timedelta(hours=12),
        "monthly": timedelta(hours=24),
        "quarterly": timedelta(hours=24),
    }
    return mapping.get(frequency, timedelta(hours=6))


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if not entry:
            return None
        if datetime.utcnow() >= entry.expires_at:
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: timedelta) -> None:
        self._store[key] = CacheEntry(value=value, expires_at=datetime.utcnow() + ttl)

    def clear(self) -> None:
        self._store.clear()
