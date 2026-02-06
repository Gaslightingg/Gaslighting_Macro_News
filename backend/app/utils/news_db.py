from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite


class NewsStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def _conn_ready(self) -> aiosqlite.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
        await self._ensure_initialized()
        return self._conn

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            conn = self._conn
            if conn is None:
                return
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_events (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    country TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    datetime_utc TEXT NOT NULL,
                    datetime_local TEXT NOT NULL,
                    unit TEXT,
                    previous TEXT,
                    forecast TEXT,
                    actual TEXT,
                    revised TEXT,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_impacts (
                    event_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    window TEXT NOT NULL,
                    return_pct REAL,
                    direction TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    price_pre REAL,
                    price_post REAL,
                    t_pre TEXT,
                    t_post TEXT,
                    data_quality TEXT NOT NULL,
                    reason TEXT,
                    PRIMARY KEY(event_id, ticker, window)
                )
                """
            )
            await conn.commit()
            self._initialized = True

    async def upsert_events(self, rows: list[dict]) -> None:
        if not rows:
            return
        conn = await self._conn_ready()
        async with self._write_lock:
            await conn.executemany(
                """
                INSERT INTO news_events (
                    id, source, title, country, importance, datetime_utc, datetime_local,
                    unit, previous, forecast, actual, revised, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source,
                    title=excluded.title,
                    country=excluded.country,
                    importance=excluded.importance,
                    datetime_utc=excluded.datetime_utc,
                    datetime_local=excluded.datetime_local,
                    unit=excluded.unit,
                    previous=excluded.previous,
                    forecast=excluded.forecast,
                    actual=excluded.actual,
                    revised=excluded.revised,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        r["id"], r["source"], r["title"], r["country"], r["importance"], r["datetime_utc"], r["datetime_local"],
                        r.get("unit"), r.get("previous"), r.get("forecast"), r.get("actual"), r.get("revised"), r["status"], r["updated_at"],
                    )
                    for r in rows
                ],
            )
            await conn.commit()

    async def list_events(self, start_utc: str, end_utc: str) -> list[dict]:
        conn = await self._conn_ready()
        cur = await conn.execute(
            """
            SELECT * FROM news_events
            WHERE datetime_utc >= ? AND datetime_utc < ?
            ORDER BY datetime_utc ASC
            """,
            (start_utc, end_utc),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_event(self, event_id: str) -> dict | None:
        conn = await self._conn_ready()
        cur = await conn.execute("SELECT * FROM news_events WHERE id = ?", (event_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_impacts(self, rows: list[dict]) -> None:
        if not rows:
            return
        conn = await self._conn_ready()
        async with self._write_lock:
            await conn.executemany(
                """
                INSERT INTO news_impacts (
                    event_id, ticker, window, return_pct, direction, computed_at,
                    price_pre, price_post, t_pre, t_post, data_quality, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, ticker, window) DO UPDATE SET
                    return_pct=excluded.return_pct,
                    direction=excluded.direction,
                    computed_at=excluded.computed_at,
                    price_pre=excluded.price_pre,
                    price_post=excluded.price_post,
                    t_pre=excluded.t_pre,
                    t_post=excluded.t_post,
                    data_quality=excluded.data_quality,
                    reason=excluded.reason
                """,
                [
                    (
                        r["event_id"], r["ticker"], r["window"], r.get("return_pct"), r["direction"], r["computed_at"],
                        r.get("price_pre"), r.get("price_post"), r.get("t_pre"), r.get("t_post"), r["data_quality"], r.get("reason"),
                    )
                    for r in rows
                ],
            )
            await conn.commit()

    async def impacts_for_events(self, event_ids: list[str]) -> list[dict]:
        if not event_ids:
            return []
        conn = await self._conn_ready()
        placeholders = ",".join("?" for _ in event_ids)
        cur = await conn.execute(
            f"SELECT * FROM news_impacts WHERE event_id IN ({placeholders})",
            event_ids,
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
