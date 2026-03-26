from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_known_unsupported_stooq_symbol_skips_network():
    class NeverClient:
        async def get(self, *_args, **_kwargs):
            raise AssertionError("network should not be called for unsupported symbol")

    rows, error = await price_service._fetch_stooq_rows(NeverClient(), "spx")  # type: ignore[arg-type]
    assert rows == []
    assert error == "unsupported_symbol"


@pytest.mark.asyncio
async def test_timeout_returns_stale_or_empty_without_crash(monkeypatch):
    cfg = PriceConfig("slow", "SLOW", "Slow", "index", "pts", "", (), "SLOW")
    monkeypatch.setattr(price_service, "PRICE_TICKERS", (cfg,))

    async def fake_build(*_args, **_kwargs):
        await asyncio.sleep(0.2)
        return {}

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
            "error": error or "timeout",
            "error_reason": "timeout",
        }

    async def fake_store():
        return _FakeStore()

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_build_cached_payload", fake_cached)
    monkeypatch.setattr(price_service, "_get_store", fake_store)
    monkeypatch.setattr(price_service, "get_settings", lambda: type("S", (), {
        "cache_ttl_prices": 0,
        "price_fetch_timeout_seconds": 0.01,
        "price_fetch_concurrency": 1,
        "allow_seed_prices": False,
    })())
    payload = await price_service.get_prices_payload(bypass_cache=True)
    assert payload.timed_out is True
    assert payload.tickers[0].status == "error"


@pytest.mark.asyncio
async def test_summary_counters_consistent(monkeypatch):
    configs = (
        PriceConfig("a", "A", "A", "index", "pts", "", (), "A"),
        PriceConfig("b", "B", "B", "index", "pts", "", (), "B"),
    )
    monkeypatch.setattr(price_service, "PRICE_TICKERS", configs)

    async def fake_build(config, *_args, **_kwargs):
        status = "live" if config.id == "a" else "stale"
        return {
            "id": config.id,
            "symbol": config.symbol,
            "name": config.name,
            "asset_class": config.asset_class,
            "unit": config.unit,
            "value": 1.0,
            "change": 0.1,
            "change_pct": 0.1,
            "last_updated": "2026-01-01",
            "as_of": "2026-01-01T00:00:00Z",
            "source": "db",
            "provider": "db",
            "status": status,
            "quality": "low",
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "tried_sources": [],
            "stale": status != "live",
        }

    async def fake_store():
        return _FakeStore()

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_get_store", fake_store)
    payload = await price_service.get_prices_payload(bypass_cache=True)
    assert payload.summary["live"] + payload.summary["stale"] == len(payload.tickers)


@pytest.mark.asyncio
async def test_singleflight_coalesces_concurrent_refresh(monkeypatch):
    calls = {"count": 0}

    async def fake_refresh(*, bypass_cache=False):
        calls["count"] += 1
        await asyncio.sleep(0.05)
        return type("Resp", (), {
            "as_of": "2026-01-01T00:00:00Z",
            "tickers": [],
            "errors": {},
            "timed_out": False,
            "cache_bypassed": bypass_cache,
            "summary": {},
        })()

    monkeypatch.setattr(price_service, "_refresh_prices_payload", fake_refresh)
    price_service._prices_refresh_task = None
    price_service._prices_cache["payload"] = None
    price_service._prices_cache["fetched_at"] = None

    await asyncio.gather(
        price_service.get_prices_payload(bypass_cache=True),
        price_service.get_prices_payload(bypass_cache=True),
    )
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_yfinance_failure_uses_stooq_fallback_chain(monkeypatch):
    cfg = PriceConfig("sp500", "S&P500", "S&P 500", "index", "pts", "", (), "^GSPC")
    store = _FakeStore()
    calls: list[str] = []

    async def no_yf(*_args, **_kwargs):
        return None

    async def stooq_ok(_client, symbol):
        calls.append(symbol)
        if symbol == "spy.us":
            return 500.0, 0.4, "2026-01-02"
        return None, "no_data"

    monkeypatch.setattr(price_service, "_fetch_yfinance_latest", no_yf)
    monkeypatch.setattr(price_service, "_fetch_stooq_latest", stooq_ok)

    latest = await price_service._ensure_latest(store, client=None, config=cfg, timeout_seconds=0.1)  # type: ignore[arg-type]
    assert latest is not None
    assert latest["status"] == "live"
    assert latest["provider"] == "stooq"
    assert "stooq:spy.us" in latest["tried_sources"]
