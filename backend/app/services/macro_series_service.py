from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from ..models.schemas import (
    MacroCategoriesResponse,
    MacroCategory,
    MacroIndicatorMeta,
    MacroLatestItem,
    MacroLatestResponse,
    MacroSeriesPoint,
    MacroSeriesResponse,
)
from ..providers.fred_provider import FredClient, parse_fred_points
from ..utils.cache_db import CacheStore
from ..utils.settings import get_settings
from .macro_catalog import ALL_CATEGORIES, MacroIndicator, all_indicators, find_indicator

SERIES_RANGE_LIMITS = {
    "1m": 35,
    "3m": 100,
    "1y": 380,
    "5y": 2000,
    "max": 5000,
}


def _series_cache_key(indicator: MacroIndicator, range_key: str, series_id: str | None) -> str:
    source = indicator.source.lower()
    suffix = series_id or "none"
    return f"macro:series:{source}:{indicator.id}:{suffix}:{range_key}"


def _latest_cache_key() -> str:
    return "macro:latest"


def _mock_series(indicator: MacroIndicator, points: int) -> list[tuple[str, float]]:
    today = datetime.utcnow().date()
    series = []
    base = 100.0
    for i in range(points):
        date = today - timedelta(days=points - i)
        value = base + (i * 0.1)
        series.append((date.isoformat(), value))
    return series


def get_categories_payload() -> MacroCategoriesResponse:
    categories = []
    for category in ALL_CATEGORIES:
        indicators = [
            MacroIndicatorMeta(
                id=indicator.id,
                name=indicator.name,
                category=indicator.category,
                frequency=indicator.frequency,
                units=indicator.units,
                source=indicator.source,
                why_it_matters=indicator.why_it_matters,
                asset_impact=indicator.asset_impact,
                direction_hint=indicator.direction_hint,
            )
            for indicator in category.indicators
        ]
        categories.append(MacroCategory(id=category.id, name=category.name, indicators=indicators))
    return MacroCategoriesResponse(categories=categories)


async def _fetch_series(
    indicator: MacroIndicator, range_key: str, client: FredClient, semaphore: asyncio.Semaphore
) -> tuple[list[tuple[str, float]], str | None]:
    limit = SERIES_RANGE_LIMITS.get(range_key, SERIES_RANGE_LIMITS["1y"])
    settings = get_settings()
    if not indicator.fred_series:
        return _mock_series(indicator, min(limit, 120)), "No data source configured"

    fred_series_id = indicator.fred_series
    if indicator.id == "pmi":
        fred_series_id = settings.fred_pmi_series_id

    if not settings.fred_api_key:
        return _mock_series(indicator, min(limit, 120)), "FRED API key not configured"

    async with semaphore:
        observations = await client.get_series_observations(fred_series_id, limit=limit)

    points = parse_fred_points(observations)
    if points:
        return list(reversed(points)), None
    return _mock_series(indicator, min(limit, 120)), f"No data returned for {fred_series_id}"


async def get_series_payload(indicator_id: str, range_key: str) -> MacroSeriesResponse:
    indicator = find_indicator(indicator_id)
    if not indicator:
        raise ValueError("Indicator not found")

    settings = get_settings()
    series_id = indicator.fred_series
    if indicator.id == "pmi":
        series_id = settings.fred_pmi_series_id
    cache = CacheStore(settings.cache_db_url)
    cache_key = _series_cache_key(indicator, range_key, series_id)
    cached = cache.get_cache(cache_key)
    if cached:
        return MacroSeriesResponse(**cached)

    client = FredClient(settings.fred_api_key or "", timeout=settings.request_timeout)
    semaphore = asyncio.Semaphore(6)
    points, error = await _fetch_series(indicator, range_key, client, semaphore)
    await client.close()

    latest_value = points[-1][1] if points else 0.0
    last_updated = points[-1][0] if points else datetime.utcnow().date().isoformat()

    response = MacroSeriesResponse(
        indicator_id=indicator_id,
        latest=f"{latest_value:.2f}",
        last_updated=last_updated,
        units=indicator.units,
        frequency=indicator.frequency,
        source=indicator.source,
        series=[MacroSeriesPoint(date=date, value=value) for date, value in points],
        available=error is None,
        error=error,
    )

    ttl_seconds = settings.cache_ttl_series_1y if range_key == "1y" else settings.cache_ttl_series_5y
    cache.set_cache(cache_key, response.model_dump(), ttl_seconds)
    return response


async def get_latest_payload() -> MacroLatestResponse:
    settings = get_settings()
    cache = CacheStore(settings.cache_db_url)
    cached = cache.get_cache(_latest_cache_key())
    if cached:
        return MacroLatestResponse(**cached)

    latest_items: list[MacroLatestItem] = []
    as_of = datetime.utcnow().date().isoformat()
    client = FredClient(settings.fred_api_key or "", timeout=settings.request_timeout)
    semaphore = asyncio.Semaphore(6)

    async def build_latest(indicator: MacroIndicator) -> None:
        nonlocal as_of
        points, error = await _fetch_series(indicator, "1y", client, semaphore)
        if not points:
            latest_items.append(
                MacroLatestItem(
                    indicator_id=indicator.id,
                    name=indicator.name,
                    value="—",
                    change="—",
                    updated="—",
                    category=indicator.category,
                    available=False,
                    error=error,
                )
            )
            return
        latest_value = points[-1][1]
        change = None
        if len(points) >= 2:
            change = points[-1][1] - points[-2][1]
        latest_items.append(
            MacroLatestItem(
                indicator_id=indicator.id,
                name=indicator.name,
                value=f"{latest_value:.2f}",
                change=f"{change:+.2f}" if change is not None else "0.00",
                updated=points[-1][0],
                category=indicator.category,
                available=error is None,
                error=error,
            )
        )
        as_of = max(as_of, points[-1][0])

    await asyncio.gather(*[build_latest(ind) for ind in all_indicators()])
    await client.close()

    response = MacroLatestResponse(as_of=as_of, latest=latest_items)
    cache.set_cache(_latest_cache_key(), response.model_dump(), settings.cache_ttl_latest)
    return response


def clear_cache() -> None:
    settings = get_settings()
    cache = CacheStore(settings.cache_db_url)
    cache.clear_all()
