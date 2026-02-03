from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Column, DateTime, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    key = Column(String, primary_key=True)
    value_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    meta_json = Column(Text, nullable=True)


class CacheStore:
    def __init__(self, db_url: str) -> None:
        self.engine = create_engine(db_url, future=True)
        Base.metadata.create_all(self.engine)

    def get_cache(self, key: str) -> Any | None:
        now = datetime.utcnow()
        with Session(self.engine) as session:
            entry = session.get(CacheEntry, key)
            if not entry:
                return None
            if entry.expires_at <= now:
                session.delete(entry)
                session.commit()
                return None
            return json.loads(entry.value_json)

    def set_cache(self, key: str, value: Any, ttl_seconds: int, meta: dict | None = None) -> None:
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=ttl_seconds)
        payload = CacheEntry(
            key=key,
            value_json=json.dumps(value),
            created_at=now,
            expires_at=expires_at,
            meta_json=json.dumps(meta) if meta else None,
        )
        with Session(self.engine) as session:
            session.merge(payload)
            session.commit()

    def clear_all(self) -> None:
        with Session(self.engine) as session:
            session.query(CacheEntry).delete()
            session.commit()
