from __future__ import annotations

from ..models.schemas import PricesResponse
from ..providers import MarketDataProvider, MockMarketDataProvider, get_provider


def get_prices_payload(provider: MarketDataProvider | None = None) -> PricesResponse:
    provider = provider or get_provider()
    response = provider.get_prices()
    if not response.tickers:
        return MockMarketDataProvider().get_prices()
    return response
