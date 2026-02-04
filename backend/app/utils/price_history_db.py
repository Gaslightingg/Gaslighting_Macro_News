from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryPoint:
    date: str
    value: float


class PriceHistoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
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
        self._initialized = True

    async def _log_schema(self, conn: aiosqlite.Connection, table_name: str) -> None:
        async with conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ) as cur:
            row = await cur.fetchone()
        if row and row["sql"]:
            logger.info("Schema for %s: %s", table_name, row["sql"])

    async def get_history(self, symbol: str) -> list[HistoryPoint]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT as_of AS date, price AS value FROM price_history WHERE symbol = ? ORDER BY as_of ASC",
                (symbol,),
            ) as cur:
                rows = await cur.fetchall()
        return [HistoryPoint(date=row["date"], value=row["value"]) for row in rows]

    async def upsert_history(self, symbol: str, points: list[HistoryPoint], source: str | None) -> None:
        if not points:
            return
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executemany(
                """
                INSERT OR REPLACE INTO price_history (symbol, price, change_pct, as_of, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(symbol, point.value, None, point.date, source) for point in points],
            )
            await conn.commit()

    async def get_latest(self, symbol: str) -> dict[str, Any] | None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT symbol,
                       price AS value,
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
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
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
            await conn.commit()

    async def get_meta(self, key: str) -> str | None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT value FROM cache_meta WHERE key = ?",
                (key,),
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                (key, value),
            )
            await conn.commit()
