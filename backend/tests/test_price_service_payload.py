import pytest

from app.models.schemas import MacroResponse, PriceTicker, PricesResponse
from app.providers.base import MarketDataProvider
from app.services.price_service import get_prices_payload
from app.utils.settings import get_settings


class DummyProvider(MarketDataProvider):
    async def get_prices(self) -> PricesResponse:
        return PricesResponse(
            as_of="2024-01-02T00:00:00Z",
            tickers=[
                PriceTicker(
                    id="sp500",
                    name="S&P500",
                    asset_class="index",
                    value=100.0,
                    change=1.0,
                    unit="pts",
                    last_updated="2024-01-02T00:00:00Z",
                    status="live",
                    source="test",
                    quality="high",
                    history_points=0,
                )
            ],
        )

    async def get_macro(self) -> MacroResponse:
        return MacroResponse(as_of="2024-01-02", series=[], commentary="")


@pytest.mark.asyncio
async def test_get_prices_payload_uses_id(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKET_DATA_DB_PATH", str(tmp_path / "market.sqlite3"))
    monkeypatch.setenv("CACHE_DB_URL", f"sqlite:///{tmp_path / 'cache.sqlite3'}")
    get_settings.cache_clear()

    payload = await get_prices_payload(provider=DummyProvider())

    assert payload.tickers
    assert payload.tickers[0].id == "sp500"
