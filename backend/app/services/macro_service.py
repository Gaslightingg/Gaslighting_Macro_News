from __future__ import annotations

from ..models.schemas import MacroResponse
from ..providers import MarketDataProvider, MockMarketDataProvider


def get_macro_payload(provider: MarketDataProvider | None = None) -> MacroResponse:
    provider = provider or MockMarketDataProvider()
    return provider.get_macro()
