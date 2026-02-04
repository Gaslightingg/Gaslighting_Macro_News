from __future__ import annotations

from ..models.schemas import MacroResponse
from ..providers import MarketDataProvider, MockMarketDataProvider, get_provider


async def get_macro_payload(provider: MarketDataProvider | None = None) -> MacroResponse:
    provider = provider or get_provider()
    response = await provider.get_macro()
    if not response.series:
        return await MockMarketDataProvider().get_macro()
    return response
