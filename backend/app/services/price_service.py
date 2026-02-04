from __future__ import annotations

from datetime import datetime
import logging
import re

from pydantic import ValidationError

from ..models.schemas import PricesResponse
from ..providers import MarketDataProvider, MockMarketDataProvider, get_provider
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


def _slugify(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()


def _infer_asset_class(symbol: str) -> str:
    if symbol in {"S&P500", "NAS100", "NASDAQ mini"}:
        return "index"
    if symbol == "XAUUSD":
        return "commodity"
    return "fx"


def _infer_unit(asset_class: str) -> str | None:
    if asset_class == "index":
        return "pts"
    if asset_class == "commodity":
        return "USD"
    if asset_class == "fx":
        return "USD"
    return None


def _normalize_prices_payload(cached: dict) -> dict:
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    tickers = []
    for idx, entry in enumerate(cached.get("tickers", [])):
        symbol = entry.get("symbol") or entry.get("id") or entry.get("name")
        if not symbol:
            tickers.append(
                {
                    "id": f"unknown_{idx}",
                    "name": "Unknown",
                    "asset_class": "unknown",
                    "value": None,
                    "change": None,
                    "unit": None,
                    "last_updated": entry.get("last_updated") or cached.get("as_of") or now_iso,
                    "status": "unavailable",
                    "source": entry.get("source") or "cache",
                    "quality": entry.get("quality") or "low",
                    "error": "invalid cached schema",
                    "history_points": entry.get("history_points") or 0,
                }
            )
            continue

        asset_class = entry.get("asset_class") or _infer_asset_class(symbol)
        status = entry.get("status")
        if status not in {"live", "cached", "stale", "unavailable"}:
            status = "cached"
        value = entry.get("value")
        if value is None:
            value = entry.get("price")
        change = entry.get("change")
        if change is None:
            change = entry.get("change_pct")
        tickers.append(
            {
                "id": entry.get("id") or _slugify(symbol),
                "name": entry.get("name") or symbol,
                "asset_class": asset_class,
                "value": value,
                "change": change,
                "unit": entry.get("unit") or _infer_unit(asset_class),
                "last_updated": entry.get("last_updated") or cached.get("as_of") or now_iso,
                "status": status,
                "source": entry.get("source") or "cache",
                "quality": entry.get("quality") or "low",
                "error": entry.get("error"),
                "history_points": entry.get("history_points") or 0,
            }
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
        asset_class = _infer_asset_class(symbol)
        tickers.append(
            {
                "id": _slugify(symbol),
                "name": symbol,
                "asset_class": asset_class,
                "value": None,
                "change": None,
                "unit": _infer_unit(asset_class),
                "last_updated": None,
                "status": "unavailable",
                "source": None,
                "quality": "low",
                "error": "invalid cached schema",
                "history_points": 0,
            }
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
    cache.set_cache(
        cache_key,
        {"schema_version": SCHEMA_VERSION, **payload.model_dump()},
        settings.cache_ttl_prices,
    )
    return payload
