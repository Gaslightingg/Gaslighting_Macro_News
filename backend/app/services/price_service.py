from __future__ import annotations

from ..models.schemas import PricesResponse
from ..providers import MarketDataProvider, MockMarketDataProvider


def get_prices_payload(provider: MarketDataProvider | None = None) -> PricesResponse:
    provider = provider or MockMarketDataProvider()
    return provider.get_prices()
