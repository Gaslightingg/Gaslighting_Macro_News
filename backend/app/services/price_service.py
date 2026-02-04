from __future__ import annotations

from datetime import datetime
import logging

from pydantic import ValidationError

from ..models.schemas import PricesResponse
from ..providers import MarketDataProvider, MockMarketDataProvider, get_provider
from ..providers.price_normalizer import normalize_price_ticker, slugify
from ..utils.cache_db import CacheStore
from ..utils.database import MarketDataStore
from ..utils.settings import get_settings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
_DEFAULT_TICKERS = [
    "S&P500",
    "NAS100",
    "NASDAQ mini",
    "EUR/USD",
    "GBP/USD",
    "GBP/JPY",
    "XAUUSD",
]


def _normalize_prices_payload(cached: dict) -> dict:
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    tickers = []
    for idx, entry in enumerate(cached.get("tickers", [])):
        symbol = entry.get("symbol") or entry.get("id") or entry.get("name")
        if not symbol:
            tickers.append(
                normalize_price_ticker(
                    {},
                    now=datetime.utcnow(),
                    source=entry.get("source") or "cache",
                    status="unavailable",
                    error="invalid cached schema",
                )
            )
            continue

        status = entry.get("status") if entry.get("status") in {"live", "cached", "stale", "unavailable"} else "cached"
        tickers.append(
            normalize_price_ticker(
                entry,
                now=datetime.utcnow(),
                source=entry.get("source") or "cache",
                status=status,
                error=entry.get("error"),
            )
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": cached.get("as_of") or now_iso,
        "tickers": tickers,
    }


def _empty_prices_payload() -> dict:
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    tickers = []
    for symbol in _DEFAULT_TICKERS:
        tickers.append(
            normalize_price_ticker(
                {"symbol": symbol},
                now=datetime.utcnow(),
                source=None,
                status="unavailable",
                error="invalid cached schema",
            )
        )
    return {"schema_version": SCHEMA_VERSION, "as_of": now_iso, "tickers": tickers}


async def get_prices_payload(provider: MarketDataProvider | None = None) -> PricesResponse:
    settings = get_settings()
    cache = CacheStore(settings.cache_db_url)
    cache_key = "prices:latest"
    cached = cache.get_cache(cache_key)
    if cached:
        payload = cached
        if cached.get("schema_version") != SCHEMA_VERSION:
            payload = _normalize_prices_payload(cached)
            cache.set_cache(cache_key, payload, settings.cache_ttl_prices)
        try:
            return PricesResponse(**payload)
        except ValidationError as exc:
            logger.warning("Prices cache validation failed: %s", exc)
            fallback = _empty_prices_payload()
            return PricesResponse(**fallback)
    provider = provider or get_provider()
    response = await provider.get_prices()
    if not response.tickers:
        response = await MockMarketDataProvider().get_prices()

    store = MarketDataStore(settings.resolved_database_path())
    latest = {slugify(row["symbol"]): row for row in store.load_prices()}
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    tickers = []
    for item in response.tickers:
        latest_row = latest.get(item.id)
        history_symbol = item.name
        tickers.append(
            normalize_price_ticker(
                {
                    "id": item.id,
                    "symbol": item.name,
                    "value": item.value,
                    "change": item.change,
                    "last_updated": latest_row["as_of"] if latest_row else item.last_updated,
                    "history_points": len(store.load_price_history(history_symbol)),
                },
                now=datetime.utcnow(),
                source=latest_row["source"] if latest_row else None,
                status="live",
            )
        )

    payload = PricesResponse(as_of=now_iso, tickers=tickers)
    cache.set_cache(
        cache_key,
        {"schema_version": SCHEMA_VERSION, **payload.model_dump()},
        settings.cache_ttl_prices,
    )
    return payload
