from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable


class MarketDataStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_latest (
                    symbol TEXT PRIMARY KEY,
                    price REAL NOT NULL,
                    change_pct REAL NOT NULL,
                    as_of TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_latest (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    change TEXT NOT NULL,
                    updated TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    source TEXT NOT NULL
                )
                """
            )

    def upsert_prices(self, rows: Iterable[dict]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO price_latest (symbol, price, change_pct, as_of, source)
                VALUES (:symbol, :price, :change_pct, :as_of, :source)
                ON CONFLICT(symbol) DO UPDATE SET
                    price=excluded.price,
                    change_pct=excluded.change_pct,
                    as_of=excluded.as_of,
                    source=excluded.source
                """,
                list(rows),
            )

    def upsert_macro(self, rows: Iterable[dict]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO macro_latest (name, value, change, updated, as_of, source)
                VALUES (:name, :value, :change, :updated, :as_of, :source)
                ON CONFLICT(name) DO UPDATE SET
                    value=excluded.value,
                    change=excluded.change,
                    updated=excluded.updated,
                    as_of=excluded.as_of,
                    source=excluded.source
                """,
                list(rows),
            )

    def load_prices(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, price, change_pct, as_of, source FROM price_latest"
            ).fetchall()
        return [dict(row) for row in rows]

    def load_macro(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, value, change, updated, as_of, source FROM macro_latest"
            ).fetchall()
        return [dict(row) for row in rows]
