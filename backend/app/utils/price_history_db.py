from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import logging
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
                    date TEXT NOT NULL,
                    value REAL NOT NULL,
                    source TEXT,
                    PRIMARY KEY (symbol, date)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_latest (
                    symbol TEXT PRIMARY KEY,
                    value REAL,
                    change REAL,
                    change_pct REAL,
                    last_updated TEXT,
                    source TEXT,
                    status TEXT,
                    quality TEXT,
                    updated_at TEXT
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
            await self._migrate_value_column(conn, "price_history")
            await self._migrate_value_column(conn, "price_latest")
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

    async def _migrate_value_column(self, conn: aiosqlite.Connection, table_name: str) -> None:
        async with conn.execute(f"PRAGMA table_info({table_name})") as cur:
            columns = [row[1] for row in await cur.fetchall()]
        if "value" in columns:
            return
        fallback = None
        for candidate in ("price", "close"):
            if candidate in columns:
                fallback = candidate
                break
        if fallback:
            logger.warning(
                "Table %s missing value column; migrating from %s column.",
                table_name,
                fallback,
            )
            await conn.execute(f"ALTER TABLE {table_name} ADD COLUMN value REAL")
            await conn.execute(f"UPDATE {table_name} SET value = {fallback}")
        else:
            logger.warning(
                "Table %s missing value column without fallback; recreating cache table.",
                table_name,
            )
            await conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            if table_name == "price_history":
                await conn.execute(
                    """
                    CREATE TABLE price_history (
                        symbol TEXT NOT NULL,
                        date TEXT NOT NULL,
                        value REAL NOT NULL,
                        source TEXT,
                        PRIMARY KEY (symbol, date)
                    )
                    """
                )
            elif table_name == "price_latest":
                await conn.execute(
                    """
                    CREATE TABLE price_latest (
                        symbol TEXT PRIMARY KEY,
                        value REAL,
                        change REAL,
                        change_pct REAL,
                        last_updated TEXT,
                        source TEXT,
                        status TEXT,
                        quality TEXT,
                        updated_at TEXT
                    )
                    """
                )

    async def get_history(self, symbol: str) -> list[HistoryPoint]:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT date, value FROM price_history WHERE symbol = ? ORDER BY date ASC",
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
                INSERT OR REPLACE INTO price_history (symbol, date, value, source)
                VALUES (?, ?, ?, ?)
                """,
                [(symbol, point.date, point.value, source) for point in points],
            )
            await conn.commit()

    async def get_latest(self, symbol: str) -> dict[str, Any] | None:
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT symbol, value, change, change_pct, last_updated, source, status, quality, updated_at
                FROM price_latest WHERE symbol = ?
                """,
                (symbol,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_latest(self, symbol: str, payload: dict[str, Any]) -> None:
        await self._ensure_initialized()
        updated_at = payload.get("updated_at") or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO price_latest (
                    symbol, value, change, change_pct, last_updated, source, status, quality, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    payload.get("value"),
                    payload.get("change"),
                    payload.get("change_pct"),
                    payload.get("last_updated"),
                    payload.get("source"),
                    payload.get("status"),
                    payload.get("quality"),
                    updated_at,
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
