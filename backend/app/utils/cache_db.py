from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Column, DateTime, Float, String, Text, create_engine
from sqlalchemy.engine import make_url
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


class IndicatorLatest(Base):
    __tablename__ = "indicator_latest"

    indicator_id = Column(String, primary_key=True)
    fetched_at = Column(DateTime, nullable=False)
    last_updated = Column(String, nullable=True)
    value = Column(Float, nullable=True)
    change = Column(Float, nullable=True)
    status = Column(String, nullable=False)
    source = Column(String, nullable=True)
    quality = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)


class IndicatorSeries(Base):
    __tablename__ = "indicator_series"

    indicator_id = Column(String, primary_key=True)
    date = Column(String, primary_key=True)
    value = Column(Float, nullable=True)
    fetched_at = Column(DateTime, nullable=False)


class CacheStore:
    def __init__(self, db_url: str) -> None:
        self._ensure_sqlite_parent_dir(db_url)
        self.engine = create_engine(db_url, future=True)
        Base.metadata.create_all(self.engine)

    @staticmethod
    def _ensure_sqlite_parent_dir(db_url: str) -> None:
        """
        SQLite URLs can reference files in directories that do not yet exist.
        On first launch this leads to `sqlite3.OperationalError: unable to open database file`.
        """
        try:
            url = make_url(db_url)
        except Exception:
            return
        if url.drivername != "sqlite":
            return
        database = url.database or ""
        if not database or database == ":memory:":
            return
        db_path = Path(database).expanduser()
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

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

    def get_latest(self, indicator_id: str) -> IndicatorLatest | None:
        with Session(self.engine) as session:
            return session.get(IndicatorLatest, indicator_id)

    def set_latest(self, entry: IndicatorLatest) -> None:
        with Session(self.engine) as session:
            session.merge(entry)
            session.commit()

    def get_series(self, indicator_id: str) -> list[IndicatorSeries]:
        with Session(self.engine) as session:
            rows = (
                session.query(IndicatorSeries)
                .filter(IndicatorSeries.indicator_id == indicator_id)
                .order_by(IndicatorSeries.date.asc())
                .all()
            )
            return rows

    def set_series_points(self, indicator_id: str, points: list[tuple[str, float]]) -> None:
        now = datetime.utcnow()
        with Session(self.engine) as session:
            for date, value in points:
                session.merge(
                    IndicatorSeries(
                        indicator_id=indicator_id,
                        date=date,
                        value=value,
                        fetched_at=now,
                    )
                )
            session.commit()
