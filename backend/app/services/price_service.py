from __future__ import annotations

from ..models.schemas import PricesResponse
from ..providers import MarketDataProvider, MockMarketDataProvider, get_provider
from ..utils.cache_db import CacheStore
from ..utils.settings import get_settings


async def get_prices_payload(provider: MarketDataProvider | None = None) -> PricesResponse:
    settings = get_settings()
    cache = CacheStore(settings.cache_db_url)
    cache_key = "prices:latest"
    cached = cache.get_cache(cache_key)
    if cached:
        return PricesResponse(**cached)
    provider = provider or get_provider()
    response = await provider.get_prices()
    if not response.tickers:
        response = await MockMarketDataProvider().get_prices()
    cache.set_cache(cache_key, response.model_dump(), settings.cache_ttl_prices)
    return response
