from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import sqlite3
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryPoint:
    date: str
    value: float
    change_pct: float | None = None


class PriceHistoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._conn: aiosqlite.Connection | None = None

    async def _configure_connection(self, conn: aiosqlite.Connection) -> None:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=8000")
        await conn.execute("PRAGMA synchronous=NORMAL")

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return

            conn = await aiosqlite.connect(self.db_path)
            await self._configure_connection(conn)

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_history (
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    change_pct REAL,
                    as_of TEXT NOT NULL,
                    source TEXT,
                    PRIMARY KEY (symbol, as_of)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_latest (
                    symbol TEXT PRIMARY KEY,
                    price REAL,
                    change_pct REAL,
                    as_of TEXT,
                    source TEXT
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )

            await self._log_schema(conn, "price_history")
            await self._log_schema(conn, "price_latest")
            await conn.commit()

            self._conn = conn
            self._initialized = True

    async def _write_transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> None:
        async with self._write_lock:
            conn = await aiosqlite.connect(self.db_path)
            try:
                await self._configure_connection(conn)
                await conn.execute("BEGIN IMMEDIATE")
                for sql, params in statements:
                    await conn.execute(sql, params)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
            finally:
                await conn.close()

    async def _get_connection(self) -> aiosqlite.Connection:
        await self._ensure_initialized()
        if self._conn is None:
            raise RuntimeError("PriceHistoryStore connection is not initialized")
        return self._conn

    async def _log_schema(self, conn: aiosqlite.Connection, table_name: str) -> None:
        async with conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ) as cur:
            row = await cur.fetchone()
        if row and row["sql"]:
            logger.info("Schema for %s: %s", table_name, row["sql"])

    async def get_history(self, symbol: str) -> list[HistoryPoint]:
        conn = await self._get_connection()
        async with conn.execute(
            "SELECT as_of AS date, COALESCE(price, value) AS value FROM price_history WHERE symbol = ? ORDER BY as_of ASC",
            (symbol,),
        ) as cur:
            rows = await cur.fetchall()
        return [HistoryPoint(date=row["date"], value=row["value"]) for row in rows]

    async def upsert_history(self, symbol: str, points: list[HistoryPoint], source: str | None) -> None:
        if not points:
            return
        rows = [
            (
                symbol,
                point.value,
                point.change_pct if point.change_pct is not None else 0.0,
                point.date,
                source,
            )
            for point in points
        ]
        for attempt in range(3):
            try:
                await self._write_transaction([
                    (
                        """
                        INSERT OR REPLACE INTO price_history (symbol, price, change_pct, as_of, source)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        row,
                    )
                    for row in rows
                ])
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                await asyncio.sleep(0.15 * (attempt + 1))

    async def get_latest(self, symbol: str) -> dict[str, Any] | None:
        conn = await self._get_connection()
        async with conn.execute(
            """
            SELECT symbol,
                   COALESCE(price, value) AS value,
                   change_pct AS change,
                   change_pct AS change_pct,
                   as_of AS last_updated,
                   source
            FROM price_latest WHERE symbol = ?
            """,
            (symbol,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_latest(self, symbol: str, payload: dict[str, Any]) -> None:
        for attempt in range(3):
            try:
                await self._write_transaction([
                    (
                        """
                        INSERT OR REPLACE INTO price_latest (
                            symbol, price, change_pct, as_of, source
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            symbol,
                            payload.get("value"),
                            payload.get("change_pct"),
                            payload.get("last_updated"),
                            payload.get("source"),
                        ),
                    )
                ])
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                await asyncio.sleep(0.15 * (attempt + 1))

    async def upsert_latest_with_meta(
        self,
        symbol: str,
        payload: dict[str, Any],
        meta_key: str,
        meta_value: str,
    ) -> None:
        for attempt in range(3):
            try:
                await self._write_transaction(
                    [
                        (
                            """
                            INSERT OR REPLACE INTO price_latest (
                                symbol, price, change_pct, as_of, source
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                symbol,
                                payload.get("value"),
                                payload.get("change_pct"),
                                payload.get("last_updated"),
                                payload.get("source"),
                            ),
                        ),
                        (
                            "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                            (meta_key, meta_value),
                        ),
                    ]
                )
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                await asyncio.sleep(0.15 * (attempt + 1))

    async def get_meta(self, key: str) -> str | None:
        conn = await self._get_connection()
        async with conn.execute(
            "SELECT value FROM cache_meta WHERE key = ?",
            (key,),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        for attempt in range(3):
            try:
                await self._write_transaction(
                    [("INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)", (key, value))]
                )
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                await asyncio.sleep(0.15 * (attempt + 1))
