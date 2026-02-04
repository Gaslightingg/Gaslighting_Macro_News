import pytest

from app.providers import real_provider
from app.providers.real_provider import RealMarketDataProvider


@pytest.mark.asyncio
async def test_real_provider_prices_handles_failure(monkeypatch):
    async def fake_stooq(client, symbol, timeout, semaphore):
        if symbol == "ndx":
            return None
        return 100.0, 1.0, "2024-01-01"

    async def fake_yfinance(symbol, timeout):
        return None

    monkeypatch.setattr(real_provider, "_fetch_stooq_price", fake_stooq)
    monkeypatch.setattr(real_provider, "_fetch_yfinance_price", fake_yfinance)

    provider = RealMarketDataProvider()
    response = await provider.get_prices()

    assert response.tickers
    statuses = {item.status for item in response.tickers}
    assert "unavailable" in statuses
