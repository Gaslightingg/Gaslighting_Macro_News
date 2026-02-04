from __future__ import annotations

from datetime import datetime

from ..models.schemas import PricesResponse
from ..providers import MarketDataProvider, MockMarketDataProvider, get_provider
from ..utils.cache_db import CacheStore
from ..utils.database import MarketDataStore
from ..utils.settings import get_settings


async def get_prices_payload(provider: MarketDataProvider | None = None) -> PricesResponse:
    settings = get_settings()
    cache = CacheStore(settings.cache_db_url)
    cache_key = "prices:latest"
    cached = cache.get_cache(cache_key)
    if cached:
        for item in cached.get("tickers", []):
            item["status"] = "cached"
        return PricesResponse(**cached)
    provider = provider or get_provider()
    response = await provider.get_prices()
    if not response.tickers:
        response = await MockMarketDataProvider().get_prices()

    store = MarketDataStore(settings.resolved_database_path())
    latest = {row["symbol"]: row for row in store.load_prices()}
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    tickers = []
    for item in response.tickers:
        asset_class = "fx"
        if item.symbol in {"S&P500", "NAS100", "NASDAQ mini"}:
            asset_class = "index"
        if item.symbol == "XAUUSD":
            asset_class = "commodity"
        latest_row = latest.get(item.symbol)
        unit = None
        if asset_class == "index":
            unit = "pts"
        elif asset_class == "commodity":
            unit = "USD"
        elif asset_class == "fx":
            unit = "USD"
        tickers.append(
            {
                "id": item.symbol,
                "name": item.symbol,
                "asset_class": asset_class,
                "value": item.price,
                "change": item.change_pct,
                "unit": unit,
                "last_updated": latest_row["as_of"] if latest_row else None,
                "status": "live",
                "source": latest_row["source"] if latest_row else None,
                "quality": "medium" if latest_row else "low",
                "error": None,
                "history_points": len(store.load_price_history(item.symbol)),
            }
        )

    payload = PricesResponse(as_of=now_iso, tickers=tickers)
    cache.set_cache(cache_key, payload.model_dump(), settings.cache_ttl_prices)
    return payload
