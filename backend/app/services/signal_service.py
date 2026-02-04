from __future__ import annotations

import asyncio

from ..analytics import build_signals
from ..models.schemas import SignalsResponse
from ..providers import MarketDataProvider, get_provider
from ..utils.cache_db import CacheStore
from ..utils.settings import get_settings
from .macro_service import get_macro_payload
from .price_service import get_prices_payload


async def get_signals_payload(provider: MarketDataProvider | None = None) -> SignalsResponse:
    settings = get_settings()
    cache = CacheStore(settings.cache_db_url)
    cache_key = "signals:latest"
    cached = cache.get_cache(cache_key)
    if cached:
        return SignalsResponse(**cached)

    provider = provider or get_provider()
    macro, prices = await asyncio.gather(
        get_macro_payload(provider),
        get_prices_payload(provider),
    )
    tickers = [item.id for item in prices.tickers]
    response = SignalsResponse(
        as_of=macro.as_of,
        disclaimer="Not financial advice",
        signals=build_signals(macro.series, tickers),
    )
    cache.set_cache(cache_key, response.model_dump(), settings.cache_ttl_signals)
    return response
