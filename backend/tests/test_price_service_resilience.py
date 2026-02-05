from __future__ import annotations

import pytest

from app.providers.fred_provider import FredClient
from app.services import price_service
from app.services.price_catalog import PriceConfig


@pytest.mark.asyncio
async def test_build_ticker_payload_never_returns_none_status(monkeypatch):
    cfg = PriceConfig(
        id="t1",
        symbol="T1",
        name="Ticker 1",
        asset_class="index",
        unit="pts",
        stooq_symbol="t1",
        yfinance_symbol="T1",
    )

    async def fake_latest(*args, **kwargs):
        # Simulate cached DB payload without status field.
        return {
            "value": 100.0,
            "change": 1.0,
            "change_pct": 1.0,
            "last_updated": "2024-01-01",
            "source": "stooq",
            "status": None,
        }

    async def fake_history(*args, **kwargs):
        return []

    monkeypatch.setattr(price_service, "_ensure_latest", fake_latest)
    monkeypatch.setattr(price_service, "_ensure_history", fake_history)

    ticker = await price_service._build_ticker_payload(cfg, store=None, client=None)

    assert ticker["status"] == "cached"


@pytest.mark.asyncio
async def test_get_prices_payload_survives_per_ticker_failures(monkeypatch):
    ticker_ok = PriceConfig(
        id="ok",
        symbol="OK",
        name="Okay",
        asset_class="index",
        unit="pts",
        stooq_symbol="ok",
        yfinance_symbol="OK",
    )
    ticker_bad = PriceConfig(
        id="bad",
        symbol="BAD",
        name="Broken",
        asset_class="index",
        unit="pts",
        stooq_symbol="bad",
        yfinance_symbol="BAD",
    )

    monkeypatch.setattr(price_service, "PRICE_TICKERS", (ticker_ok, ticker_bad))

    async def fake_ensure_latest(store, client, symbol_id, stooq_symbol):
        if symbol_id == "bad":
            raise RuntimeError("upstream failed")
        return {
            "value": 10.0,
            "change": 0.1,
            "change_pct": 0.1,
            "last_updated": "2024-01-01",
            "source": "stooq",
            "status": "live",
            "quality": "high",
        }

    async def fake_ensure_history(*args, **kwargs):
        return []

    async def fake_get_store():
        return object()

    monkeypatch.setattr(price_service, "_ensure_latest", fake_ensure_latest)
    monkeypatch.setattr(price_service, "_ensure_history", fake_ensure_history)
    monkeypatch.setattr(price_service, "_get_store", fake_get_store)

    payload = await price_service.get_prices_payload()

    assert len(payload.tickers) == 2
    assert [item.id for item in payload.tickers] == ["ok", "bad"]
    assert all(item.status for item in payload.tickers)
    assert payload.tickers[1].status == "error"


@pytest.mark.asyncio
async def test_fred_400_returns_empty_without_retries(monkeypatch):
    class FakeResponse:
        status_code = 400

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def get(self, url, params):
            self.calls += 1
            import httpx

            req = httpx.Request("GET", url)
            resp = httpx.Response(400, request=req)
            raise httpx.HTTPStatusError("bad request", request=req, response=resp)

        async def aclose(self):
            return None

    fred = FredClient(api_key="dummy")
    fake_client = FakeClient()
    monkeypatch.setattr(fred, "_client", fake_client)

    observations = await fred.get_series_observations("NAPM", retries=2)

    assert observations == []
    assert fake_client.calls == 1
