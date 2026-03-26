from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services import price_service
from app.services.price_catalog import PriceConfig


class _FakeStore:
    def __init__(self) -> None:
        self.latest: dict[str, dict] = {}
        self.meta: dict[str, str] = {}

    async def get_latest(self, symbol: str):
        return self.latest.get(symbol)

    async def get_meta(self, key: str):
        return self.meta.get(key)

    async def upsert_latest(self, symbol: str, payload: dict):
        self.latest[symbol] = payload

    async def set_meta(self, key: str, value: str):
        self.meta[key] = value

    async def get_history(self, _symbol: str):
        return []


@pytest.mark.asyncio
async def test_get_prices_payload_partial_success(monkeypatch):
    ok = PriceConfig("ok", "OK", "Okay", "index", "pts", "ok", (), "OK")
    bad = PriceConfig("bad", "BAD", "Broken", "index", "pts", "bad", (), "BAD")
    monkeypatch.setattr(price_service, "PRICE_TICKERS", (ok, bad))

    async def fake_build(config, *_args, **_kwargs):
        if config.id == "bad":
            raise RuntimeError("provider down")
        return {
            "id": config.id,
            "symbol": config.symbol,
            "name": config.name,
            "asset_class": config.asset_class,
            "unit": config.unit,
            "value": 10.0,
            "change": 1.0,
            "change_pct": 1.0,
            "last_updated": "2026-01-01",
            "as_of": "2026-01-01T00:00:00Z",
            "source": "stooq",
            "provider": "stooq",
            "status": "live",
            "quality": "high",
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "tried_sources": [],
            "stale": False,
        }

    async def fake_cached(config, _store, error=None, allow_seed=False):
        return {
            "id": config.id,
            "symbol": config.symbol,
            "name": config.name,
            "asset_class": config.asset_class,
            "unit": config.unit,
            "value": None,
            "change": None,
            "change_pct": None,
            "last_updated": None,
            "as_of": "2026-01-01T00:00:00Z",
            "source": None,
            "provider": None,
            "status": "error",
            "quality": "low",
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "tried_sources": [],
            "stale": False,
            "error": error or "provider_error",
            "error_reason": "provider_error",
        }

    async def fake_get_store():
        return _FakeStore()

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_build_cached_payload", fake_cached)
    monkeypatch.setattr(price_service, "_get_store", fake_get_store)
    price_service._prices_cache["payload"] = None
    price_service._prices_cache["fetched_at"] = None

    payload = await price_service.get_prices_payload(bypass_cache=True)
    assert [t.id for t in payload.tickers] == ["ok", "bad"]
    assert payload.summary["live"] == 1
    assert payload.summary["error"] == 1


@pytest.mark.asyncio
async def test_seed_suppressed_when_disabled(monkeypatch):
    cfg = PriceConfig("t1", "T1", "Ticker", "index", "pts", "t1", (), "T1")
    store = _FakeStore()

    async def fake_latest(*_args, **_kwargs):
        return None

    async def fake_history(*_args, **_kwargs):
        return []

    monkeypatch.setattr(price_service, "_ensure_latest", fake_latest)
    monkeypatch.setattr(price_service, "_ensure_history", fake_history)
    monkeypatch.setattr(price_service, "_seed_for_config", lambda _cfg: {"price": 100.0, "as_of": "seed"})

    ticker = await price_service._build_ticker_payload_inner(
        cfg,
        store=store,
        client=None,  # type: ignore[arg-type]
        timeout_seconds=0.1,
        allow_seed=False,
    )
    assert ticker["status"] == "empty"


@pytest.mark.asyncio
async def test_cached_value_marked_stale_when_old(monkeypatch):
    cfg = PriceConfig("t2", "T2", "Ticker", "index", "pts", "t2", (), "T2")
    store = _FakeStore()
    store.latest[cfg.id] = {
        "value": 50.0,
        "change": 1.0,
        "change_pct": 1.0,
        "last_updated": "2026-01-01",
        "source": "stooq",
    }
    store.meta[f"latest:{cfg.id}:updated_at"] = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def no_stooq(*_args, **_kwargs):
        return None, "provider_error"

    async def no_yf(*_args, **_kwargs):
        return None

    monkeypatch.setattr(price_service, "_fetch_stooq_latest", no_stooq)
    monkeypatch.setattr(price_service, "_fetch_yfinance_latest", no_yf)

    latest = await price_service._ensure_latest(store, client=None, config=cfg, timeout_seconds=0.1)  # type: ignore[arg-type]
    assert latest is not None
    assert latest["status"] == "stale"
