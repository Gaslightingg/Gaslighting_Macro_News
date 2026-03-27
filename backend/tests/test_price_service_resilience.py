from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from time import perf_counter

import pytest

from app.models.schemas import PricesResponse
from app.providers.price_normalizer import normalize_optional_float
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
        return None, "yfinance_failed"

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
    assert payload.tickers[0].no_attempts_reason in {"TICKER_TIMEOUT", "NO_ATTEMPTS_EXECUTED", "GLOBAL_DEADLINE_EXHAUSTED"}


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
    terminal_total = (
        payload.summary["ok_live"]
        + payload.summary["ok_cached"]
        + payload.summary["stale_db"]
        + payload.summary["empty"]
        + payload.summary["error"]
    )
    assert terminal_total == len(payload.tickers)
    assert payload.summary["ok_live"] == 1
    assert payload.summary["stale_db"] == 1


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
        return None, "yfinance_failed"

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


@pytest.mark.asyncio
async def test_shared_refresh_timeout_returns_snapshot(monkeypatch):
    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(1)
        return None

    async def snapshot(*_args, **_kwargs):
        return PricesResponse(as_of="2026-01-01T00:00:00Z", tickers=[], errors={}, timed_out=True, summary={})

    monkeypatch.setattr(price_service, "_refresh_prices_payload", never_finishes)
    monkeypatch.setattr(price_service, "_build_prices_snapshot", snapshot)
    price_service._prices_refresh_task = None
    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="t1")
    assert payload is not None
    assert payload.timed_out is True


@pytest.mark.asyncio
async def test_shared_refresh_exception_returns_controlled_response(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(price_service, "_refresh_prices_payload", boom)
    price_service._prices_refresh_task = None
    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="t2")
    assert payload is not None
    assert isinstance(payload, PricesResponse)


@pytest.mark.asyncio
async def test_timeout_without_snapshot_returns_empty_controlled_payload(monkeypatch):
    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(1)
        return None

    async def no_snapshot(*_args, **_kwargs):
        return None

    monkeypatch.setattr(price_service, "_refresh_prices_payload", never_finishes)
    monkeypatch.setattr(price_service, "_build_prices_snapshot", no_snapshot)
    price_service._prices_refresh_task = None
    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="t3")
    assert payload is not None
    assert all(item.status == "empty" for item in payload.tickers)


@pytest.mark.asyncio
async def test_follower_gets_fresh_when_refresh_finishes_before_timeout(monkeypatch):
    async def refresh(*_args, **_kwargs):
        await asyncio.sleep(0.02)
        return PricesResponse(as_of="2026-01-01T00:00:00Z", tickers=[], errors={}, timed_out=False, summary={})

    monkeypatch.setattr(price_service, "_refresh_prices_payload", refresh)
    monkeypatch.setattr(price_service, "SHARED_WAITER_TIMEOUT_SECONDS", 0.2)
    price_service._prices_refresh_task = None

    first, second = await asyncio.gather(
        price_service.get_prices_payload(bypass_cache=True, request_id="a1"),
        price_service.get_prices_payload(bypass_cache=True, request_id="a2"),
    )
    assert first.timed_out is False
    assert second.timed_out is False


@pytest.mark.asyncio
async def test_follower_near_timeout_boundary_gets_fresh(monkeypatch):
    async def refresh(*_args, **_kwargs):
        await asyncio.sleep(0.045)
        return PricesResponse(as_of="2026-01-01T00:00:00Z", tickers=[], errors={}, timed_out=False, summary={})

    monkeypatch.setattr(price_service, "_refresh_prices_payload", refresh)
    monkeypatch.setattr(price_service, "SHARED_WAITER_TIMEOUT_SECONDS", 0.05)
    price_service._prices_refresh_task = None

    result = await price_service.get_prices_payload(bypass_cache=True, request_id="b1")
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_owner_and_follower_return_fresh_without_timeout_fallback(monkeypatch, caplog):
    async def refresh(*_args, **_kwargs):
        await asyncio.sleep(0.03)
        return PricesResponse(as_of="2026-01-01T00:00:00Z", tickers=[], errors={}, timed_out=False, summary={})

    monkeypatch.setattr(price_service, "_refresh_prices_payload", refresh)
    monkeypatch.setattr(price_service, "SHARED_WAITER_TIMEOUT_SECONDS", 0.2)
    price_service._prices_refresh_task = None
    price_service._prices_cache["payload"] = None
    price_service._prices_cache["fetched_at"] = None
    caplog.set_level("INFO")

    async def owner_call():
        return await price_service.get_prices_payload(bypass_cache=True, request_id="owner-rid")

    async def follower_call():
        await asyncio.sleep(0.005)
        return await price_service.get_prices_payload(bypass_cache=True, request_id="follower-rid")

    owner_payload, follower_payload = await asyncio.gather(owner_call(), follower_call())
    assert owner_payload.timed_out is False
    assert follower_payload.timed_out is False

    assert "owner_path_entered rid=owner-rid" in caplog.text
    assert "owner_path_returning_fresh rid=owner-rid" in caplog.text
    assert "follower_path_entered rid=follower-rid" in caplog.text
    assert "follower_path_returning_fresh rid=follower-rid" in caplog.text
    assert "timeout_branch_entered rid=owner-rid" not in caplog.text
    assert "timeout_branch_entered rid=follower-rid" not in caplog.text
    assert "Shared prices refresh timed out; serving stale snapshot" not in caplog.text


@pytest.mark.asyncio
async def test_cache_hit_returns_fast_payload(monkeypatch):
    async def refresh(*_args, **_kwargs):
        await asyncio.sleep(0.04)
        return PricesResponse(as_of="2026-01-01T00:00:00Z", tickers=[], errors={}, timed_out=False, summary={})

    monkeypatch.setattr(price_service, "_refresh_prices_payload", refresh)
    monkeypatch.setattr(price_service, "get_settings", lambda: type("S", (), {
        "cache_ttl_prices": 30,
        "price_fetch_timeout_seconds": 0.2,
        "price_fetch_concurrency": 1,
        "allow_seed_prices": False,
        "debug_price_fetch": False,
    })())
    price_service._prices_refresh_task = None
    price_service._prices_cache["payload"] = None
    price_service._prices_cache["fetched_at"] = None

    started_refresh = perf_counter()
    await price_service.get_prices_payload(bypass_cache=False, request_id="cache-a")
    refresh_elapsed = perf_counter() - started_refresh

    started_cache = perf_counter()
    await price_service.get_prices_payload(bypass_cache=False, request_id="cache-b")
    cache_elapsed = perf_counter() - started_cache
    assert cache_elapsed < refresh_elapsed
    assert cache_elapsed < 0.02


def test_normalize_optional_float_handles_na_values():
    assert normalize_optional_float("N/A") is None
    assert normalize_optional_float("") is None
    assert normalize_optional_float(None) is None
    assert normalize_optional_float("12.5") == 12.5


@pytest.mark.asyncio
async def test_invalid_numeric_ticker_does_not_crash_prices_response(monkeypatch):
    configs = (
        PriceConfig("good", "GOOD", "Good", "index", "pts", "", (), "GOOD"),
        PriceConfig("bad", "BAD", "Bad", "index", "pts", "", (), "BAD"),
    )
    monkeypatch.setattr(price_service, "PRICE_TICKERS", configs)

    async def fake_build(config, *_args, **_kwargs):
        if config.id == "bad":
            return {
                "id": "bad",
                "symbol": "BAD",
                "name": "Bad",
                "asset_class": "index",
                "unit": "pts",
                "value": "N/A",
                "change": "N/A",
                "change_pct": "N/A",
                "last_updated": "2026-01-01",
                "as_of": "2026-01-01T00:00:00Z",
                "source": "stooq",
                "provider": "stooq",
                "status": "live",
                "quality": "high",
                "history_points": [],
                "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
                "tried_sources": ["stooq:bad"],
                "stale": False,
            }
        return {
            "id": "good",
            "symbol": "GOOD",
            "name": "Good",
            "asset_class": "index",
            "unit": "pts",
            "value": 101.0,
            "change": 0.5,
            "change_pct": 0.5,
            "last_updated": "2026-01-01",
            "as_of": "2026-01-01T00:00:00Z",
            "source": "stooq",
            "provider": "stooq",
            "status": "live",
            "quality": "high",
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "tried_sources": ["stooq:good"],
            "stale": False,
        }

    async def fake_store():
        return _FakeStore()

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_get_store", fake_store)
    price_service._prices_refresh_task = None

    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="mixed-numeric")
    assert len(payload.tickers) == 2
    assert any(t.id == "good" and t.status == "live" for t in payload.tickers)
    bad_ticker = next(t for t in payload.tickers if t.id == "bad")
    assert bad_ticker.value is None
    assert bad_ticker.change is None
    assert bad_ticker.change_pct is None
    assert bad_ticker.status == "error"


@pytest.mark.asyncio
async def test_yahoo_429_sets_cooldown_and_fallback_does_not_crash(monkeypatch):
    cfg = PriceConfig("sp500", "S&P500", "S&P 500", "index", "pts", "", (), "^GSPC")
    store = _FakeStore()

    async def rate_limited(*_args, **_kwargs):
        price_service._set_provider_cooldown("yfinance", seconds=60)
        return None, "rate_limited_cooldown"

    async def stooq_ok(*_args, **_kwargs):
        return 5000.0, 0.7, "2026-01-01"

    monkeypatch.setattr(price_service, "_fetch_yfinance_latest", rate_limited)
    monkeypatch.setattr(price_service, "_fetch_stooq_latest", stooq_ok)
    latest = await price_service._ensure_latest(store, client=None, config=cfg, timeout_seconds=0.1)  # type: ignore[arg-type]
    assert latest is not None
    assert latest["status"] == "live"
    assert latest["source"] == "stooq"
    assert price_service._provider_on_cooldown("yfinance") is True


@pytest.mark.asyncio
async def test_stooq_malformed_rows_become_parse_error_without_crash(monkeypatch):
    cfg = PriceConfig("sp500", "S&P500", "S&P 500", "index", "pts", "", (), "^GSPC")
    store = _FakeStore()

    async def malformed_stooq(*_args, **_kwargs):
        return None, "stooq_missing_columns"

    async def no_yf(*_args, **_kwargs):
        return None, "yfinance_failed"

    monkeypatch.setattr(price_service, "_fetch_stooq_latest", malformed_stooq)
    monkeypatch.setattr(price_service, "_fetch_yfinance_latest", no_yf)
    latest = await price_service._ensure_latest(store, client=None, config=cfg, timeout_seconds=0.1)  # type: ignore[arg-type]
    assert latest is not None
    assert latest["status"] == "error"
    assert latest["error_reason"] in {"stooq_missing_columns", "yfinance_failed"}


@pytest.mark.asyncio
async def test_db_locked_during_persistence_does_not_break_prices_payload(monkeypatch):
    cfg = PriceConfig("ok", "OK", "Okay", "index", "pts", "ok", (), "OK")
    monkeypatch.setattr(price_service, "PRICE_TICKERS", (cfg,))

    async def fake_build(*_args, **_kwargs):
        return {
            "id": "ok",
            "symbol": "OK",
            "name": "Okay",
            "asset_class": "index",
            "unit": "pts",
            "value": 10.0,
            "change": 0.1,
            "change_pct": 0.1,
            "last_updated": "2026-01-01",
            "as_of": "2026-01-01T00:00:00Z",
            "source": "stooq",
            "provider": "stooq",
            "status": "live",
            "quality": "high",
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "tried_sources": ["stooq:ok"],
            "stale": False,
        }

    class LockingStore(_FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def upsert_latest(self, symbol: str, payload: dict):
            self.calls += 1
            if self.calls < 3:
                raise sqlite3.OperationalError("database is locked")
            await super().upsert_latest(symbol, payload)

    store = LockingStore()

    async def fake_store():
        return store

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_get_store", fake_store)
    price_service._prices_refresh_task = None

    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="db-lock")
    assert payload is not None
    assert len(payload.tickers) == 1
    assert payload.tickers[0].status == "live"
    await asyncio.sleep(0.75)
    assert store.calls == 3


@pytest.mark.asyncio
async def test_successful_live_fx_fetch(monkeypatch):
    cfg = PriceConfig("eurusd", "EUR/USD", "EUR/USD", "fx", "USD", "", (), "EURUSD=X")
    store = _FakeStore()

    async def frankfurter_ok(*_args, **_kwargs):
        return (1.095, 0.12, "2026-01-01"), None

    async def stooq_fail(*_args, **_kwargs):
        return None, "stooq_request_failed"

    monkeypatch.setattr(price_service, "_fetch_frankfurter_latest", frankfurter_ok)
    monkeypatch.setattr(price_service, "_fetch_stooq_latest", stooq_fail)
    latest = await price_service._ensure_latest(store, client=None, config=cfg, timeout_seconds=0.1)  # type: ignore[arg-type]
    assert latest is not None
    assert latest["status"] == "live"
    assert latest["source"] == "frankfurter"
    assert isinstance(latest["value"], float)


@pytest.mark.asyncio
async def test_successful_live_index_fetch(monkeypatch):
    cfg = PriceConfig("sp500", "S&P500", "S&P 500", "index", "pts", "", (), "^GSPC")
    store = _FakeStore()

    async def stooq_ok(*_args, **_kwargs):
        return 5123.2, 0.34, "2026-01-01"

    monkeypatch.setattr(price_service, "_fetch_stooq_latest", stooq_ok)
    latest = await price_service._ensure_latest(store, client=None, config=cfg, timeout_seconds=0.1)  # type: ignore[arg-type]
    assert latest is not None
    assert latest["status"] == "live"
    assert latest["source"] == "stooq"


@pytest.mark.asyncio
async def test_stale_db_fallback_when_live_providers_fail(monkeypatch):
    cfg = PriceConfig("gbpusd", "GBP/USD", "GBP/USD", "fx", "USD", "", (), "GBPUSD=X")
    store = _FakeStore()
    store.latest[cfg.id] = {
        "value": 1.27,
        "change": 0.1,
        "change_pct": 0.1,
        "last_updated": "2026-01-01",
        "source": "db",
    }
    store.meta[f"latest:{cfg.id}:updated_at"] = (datetime.utcnow() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def stooq_fail(*_args, **_kwargs):
        return None, "stooq_request_failed"

    async def yf_fail(*_args, **_kwargs):
        return None, "rate_limited_cooldown"

    monkeypatch.setattr(price_service, "_fetch_stooq_latest", stooq_fail)
    monkeypatch.setattr(price_service, "_fetch_yfinance_latest", yf_fail)
    latest = await price_service._ensure_latest(store, client=None, config=cfg, timeout_seconds=0.1)  # type: ignore[arg-type]
    assert latest is not None
    assert latest["status"] == "stale"
    assert latest["error_reason"] in {"stooq_request_failed", "rate_limited_cooldown"}


@pytest.mark.asyncio
async def test_empty_no_cache_case_when_all_live_providers_fail(monkeypatch):
    cfg = PriceConfig("nas100", "NAS100", "Nasdaq 100", "index", "pts", "", (), "^NDX")
    store = _FakeStore()

    async def stooq_fail(*_args, **_kwargs):
        return None, "stooq_missing_columns"

    async def yf_fail(*_args, **_kwargs):
        return None, "rate_limited_cooldown"

    monkeypatch.setattr(price_service, "_fetch_stooq_latest", stooq_fail)
    monkeypatch.setattr(price_service, "_fetch_yfinance_latest", yf_fail)
    latest = await price_service._ensure_latest(store, client=None, config=cfg, timeout_seconds=0.1)  # type: ignore[arg-type]
    assert latest is not None
    assert latest["status"] == "error"


@pytest.mark.asyncio
async def test_live_fetch_survives_persistence_failure(monkeypatch):
    cfg = PriceConfig("sp500", "S&P500", "S&P 500", "index", "pts", "", (), "^GSPC")
    monkeypatch.setattr(price_service, "PRICE_TICKERS", (cfg,))

    async def fake_build(*_args, **_kwargs):
        return {
            "id": "sp500",
            "symbol": "S&P500",
            "name": "S&P 500",
            "asset_class": "index",
            "unit": "pts",
            "value": 5000.0,
            "change": 0.5,
            "change_pct": 0.5,
            "last_updated": "2026-01-01",
            "as_of": "2026-01-01T00:00:00Z",
            "source": "stooq",
            "provider": "stooq",
            "status": "live",
            "quality": "high",
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "tried_sources": ["stooq:spy.us"],
            "stale": False,
        }

    async def fake_store():
        return _FakeStore()

    async def persist_fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_get_store", fake_store)
    monkeypatch.setattr(price_service, "_persist_latest_with_retry", persist_fail)
    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="persist-fail")
    assert payload.tickers[0].status == "live"
    assert payload.summary["ok_live"] == 1


@pytest.mark.asyncio
async def test_persistence_retry_does_not_block_response_completion(monkeypatch):
    cfg = PriceConfig("sp500", "S&P500", "S&P 500", "index", "pts", "", (), "^GSPC")
    monkeypatch.setattr(price_service, "PRICE_TICKERS", (cfg,))

    async def fake_build(*_args, **_kwargs):
        return {
            "id": "sp500",
            "symbol": "S&P500",
            "name": "S&P 500",
            "asset_class": "index",
            "unit": "pts",
            "value": 5000.0,
            "change": 0.5,
            "change_pct": 0.5,
            "last_updated": "2026-01-01",
            "as_of": "2026-01-01T00:00:00Z",
            "source": "stooq",
            "provider": "stooq",
            "status": "live",
            "quality": "high",
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "tried_sources": ["stooq:spy.us"],
            "stale": False,
        }

    async def fake_store():
        return _FakeStore()

    async def slow_schedule(*_args, **_kwargs):
        async def _slow():
            await asyncio.sleep(0.5)
        asyncio.create_task(_slow())

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_get_store", fake_store)
    monkeypatch.setattr(price_service, "_schedule_persistence", slow_schedule)
    started = perf_counter()
    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="slow-persist")
    elapsed = perf_counter() - started
    assert payload.summary["ok_live"] == 1
    assert elapsed < 0.2


@pytest.mark.asyncio
async def test_follower_gets_fresh_while_persistence_retries(monkeypatch):
    async def refresh(*_args, **_kwargs):
        return PricesResponse(as_of="2026-01-01T00:00:00Z", tickers=[], errors={}, timed_out=False, summary={"ok_live": 1})

    async def slow_schedule(*_args, **_kwargs):
        async def _slow():
            await asyncio.sleep(0.5)
        asyncio.create_task(_slow())

    monkeypatch.setattr(price_service, "_refresh_prices_payload", refresh)
    monkeypatch.setattr(price_service, "_schedule_persistence", slow_schedule)
    monkeypatch.setattr(price_service, "SHARED_WAITER_TIMEOUT_SECONDS", 0.2)
    first, second = await asyncio.gather(
        price_service.get_prices_payload(bypass_cache=True, request_id="owner-p"),
        price_service.get_prices_payload(bypass_cache=True, request_id="follower-p"),
    )
    assert first.timed_out is False
    assert second.timed_out is False


@pytest.mark.asyncio
async def test_late_tickers_still_get_first_attempt_when_deadline_positive(monkeypatch):
    configs = (
        PriceConfig("a", "A", "A", "index", "pts", "", (), "A"),
        PriceConfig("b", "B", "B", "index", "pts", "", (), "B"),
        PriceConfig("c", "C", "C", "index", "pts", "", (), "C"),
    )
    monkeypatch.setattr(price_service, "PRICE_TICKERS", configs)
    monkeypatch.setattr(price_service, "get_settings", lambda: type("S", (), {
        "cache_ttl_prices": 0,
        "price_fetch_timeout_seconds": 0.2,
        "price_fetch_concurrency": 1,
        "allow_seed_prices": False,
        "debug_price_fetch": False,
    })())

    async def fake_build(config, *_args, **_kwargs):
        await asyncio.sleep(0.05)
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
            "source": "stooq",
            "provider": "stooq",
            "status": "live",
            "quality": "high",
            "provider_loop_started": True,
            "first_attempt_started_at": datetime.utcnow().isoformat(),
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "tried_sources": [f"stooq:{config.id}"],
            "stale": False,
        }

    async def fake_store():
        return _FakeStore()

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_get_store", fake_store)
    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="late-attempts")
    assert all(t.provider_loop_started for t in payload.tickers)
    assert all(t.no_attempts_reason in {None, ""} for t in payload.tickers)


@pytest.mark.asyncio
async def test_secondary_history_work_does_not_block_spot_refresh(monkeypatch):
    cfg = PriceConfig("sp500", "S&P500", "S&P 500", "index", "pts", "", (), "^GSPC")
    monkeypatch.setattr(price_service, "PRICE_TICKERS", (cfg,))

    async def fake_build(*_args, **_kwargs):
        await asyncio.sleep(0.02)
        return {
            "id": "sp500",
            "symbol": "S&P500",
            "name": "S&P 500",
            "asset_class": "index",
            "unit": "pts",
            "value": 5000.0,
            "change": 0.5,
            "change_pct": 0.5,
            "last_updated": "2026-01-01",
            "as_of": "2026-01-01T00:00:00Z",
            "source": "stooq",
            "provider": "stooq",
            "status": "live",
            "quality": "high",
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "tried_sources": ["stooq:spy.us"],
            "stale": False,
        }

    async def fake_store():
        return _FakeStore()

    async def slow_history(*_args, **_kwargs):
        await asyncio.sleep(0.5)
        return []

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_get_store", fake_store)
    monkeypatch.setattr(price_service, "_ensure_history", slow_history)

    history_task = asyncio.create_task(price_service.get_price_history_payload("sp500", "1m"))
    started = perf_counter()
    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="spot-priority")
    elapsed = perf_counter() - started
    assert payload.summary["ok_live"] == 1
    assert elapsed < 0.2
    history_task.cancel()


@pytest.mark.asyncio
async def test_persistence_batches_use_single_writer_and_do_not_overlap(monkeypatch):
    class SlowStore(_FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.active_writers = 0
            self.max_active_writers = 0
            self.calls: list[str] = []

        async def upsert_latest(self, symbol: str, payload: dict):
            self.active_writers += 1
            self.max_active_writers = max(self.max_active_writers, self.active_writers)
            self.calls.append(symbol)
            await asyncio.sleep(0.05)
            self.latest[symbol] = payload
            self.active_writers -= 1

    store = SlowStore()
    price_service._persistence_pending_by_symbol.clear()
    price_service._persistence_worker_task = None

    await asyncio.gather(
        price_service._schedule_persistence(
            store,
            [{"id": "sp500", "value": 1.0, "change_pct": 0.1, "last_updated": "2026-01-01", "provider": "stooq"}],
            "rid-1",
        ),
        price_service._schedule_persistence(
            store,
            [{"id": "nas100", "value": 2.0, "change_pct": 0.2, "last_updated": "2026-01-01", "provider": "stooq"}],
            "rid-2",
        ),
    )
    await price_service._wait_for_persistence_idle()

    assert store.max_active_writers == 1
    assert set(store.calls) == {"sp500", "nas100"}


@pytest.mark.asyncio
async def test_persistence_coalesces_older_symbol_updates(monkeypatch):
    class RecordingStore(_FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.writes: list[tuple[str, float]] = []

        async def upsert_latest(self, symbol: str, payload: dict):
            self.writes.append((symbol, float(payload["value"])))
            self.latest[symbol] = payload

    store = RecordingStore()
    price_service._persistence_pending_by_symbol.clear()
    price_service._persistence_worker_task = None

    await price_service._schedule_persistence(
        store,
        [{"id": "sp500", "value": 100.0, "change_pct": 0.1, "last_updated": "2026-01-01", "provider": "stooq"}],
        "rid-old",
    )
    await price_service._schedule_persistence(
        store,
        [{"id": "sp500", "value": 200.0, "change_pct": 0.2, "last_updated": "2026-01-02", "provider": "stooq"}],
        "rid-new",
    )
    await price_service._wait_for_persistence_idle()

    assert store.latest["sp500"]["value"] == 200.0


@pytest.mark.asyncio
async def test_spot_tasks_are_scheduled_without_stagger_with_low_configured_concurrency(monkeypatch):
    configs = tuple(
        PriceConfig(f"t{i}", f"T{i}", f"Ticker {i}", "fx", "USD", "", (), f"T{i}")
        for i in range(7)
    )
    monkeypatch.setattr(price_service, "PRICE_TICKERS", configs)
    monkeypatch.setattr(
        price_service,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "cache_ttl_prices": 0,
                "price_fetch_timeout_seconds": 0.8,
                "price_fetch_concurrency": 1,
                "allow_seed_prices": False,
                "debug_price_fetch": False,
            },
        )(),
    )
    call_times: dict[str, float] = {}

    async def fake_build(config, *_args, **_kwargs):
        call_times[config.id] = perf_counter()
        await asyncio.sleep(0.02)
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
            "source": "stooq",
            "provider": "stooq",
            "status": "live",
            "quality": "high",
            "provider_loop_started": True,
            "first_attempt_started_at": datetime.utcnow().isoformat(),
            "tried_sources": [f"stooq:{config.id}"],
            "history_points": [],
            "history_meta": {"data_start": None, "data_end": None, "interval": "1d", "points_count": 0},
            "stale": False,
        }

    async def fake_store():
        return _FakeStore()

    monkeypatch.setattr(price_service, "_build_ticker_payload", fake_build)
    monkeypatch.setattr(price_service, "_get_store", fake_store)

    payload = await price_service.get_prices_payload(bypass_cache=True, request_id="spot-schedule")
    assert payload.summary["ok_live"] == 7
    assert len(call_times) == 7
    spread = max(call_times.values()) - min(call_times.values())
    assert spread < 0.15
