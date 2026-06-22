import sqlite3

import pytest

from app.utils.price_history_db import HistoryPoint, PriceHistoryStore


@pytest.mark.asyncio
async def test_price_history_store_uses_price_column_without_legacy_value(tmp_path):
    store = PriceHistoryStore(tmp_path / "prices.db")

    await store.upsert_latest(
        "sp500",
        {"value": 5000.0, "change_pct": 0.5, "last_updated": "2026-06-22", "source": "test"},
    )
    await store.upsert_history("sp500", [HistoryPoint(date="2026-06-22", value=5000.0)], "test")

    latest = await store.get_latest("sp500")
    history = await store.get_history("sp500")

    assert latest is not None
    assert latest["value"] == 5000.0
    assert history == [HistoryPoint(date="2026-06-22", value=5000.0)]


@pytest.mark.asyncio
async def test_price_history_store_migrates_legacy_value_column(tmp_path):
    db_path = tmp_path / "legacy-prices.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE price_latest (
                symbol TEXT PRIMARY KEY,
                value REAL,
                change_pct REAL,
                as_of TEXT,
                source TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE price_history (
                symbol TEXT NOT NULL,
                value REAL NOT NULL,
                change_pct REAL,
                as_of TEXT NOT NULL,
                source TEXT,
                PRIMARY KEY (symbol, as_of)
            )
            """
        )
        conn.execute(
            "INSERT INTO price_latest (symbol, value, change_pct, as_of, source) VALUES (?, ?, ?, ?, ?)",
            ("sp500", 4999.0, 0.4, "2026-06-21", "legacy"),
        )
        conn.execute(
            "INSERT INTO price_history (symbol, value, change_pct, as_of, source) VALUES (?, ?, ?, ?, ?)",
            ("sp500", 4999.0, 0.4, "2026-06-21", "legacy"),
        )

    store = PriceHistoryStore(db_path)

    latest = await store.get_latest("sp500")
    history = await store.get_history("sp500")

    assert latest is not None
    assert latest["value"] == 4999.0
    assert history == [HistoryPoint(date="2026-06-21", value=4999.0)]

    with sqlite3.connect(db_path) as conn:
        latest_columns = {row[1] for row in conn.execute("PRAGMA table_info(price_latest)")}
        history_columns = {row[1] for row in conn.execute("PRAGMA table_info(price_history)")}

    assert "price" in latest_columns
    assert "price" in history_columns
