from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

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
from ..utils.cache_db import CacheStore, IndicatorLatest
from ..utils.settings import get_settings
from .macro_catalog import ALL_CATEGORIES, MacroIndicator, all_indicators, find_indicator

SERIES_RANGE_LIMITS = {
    "1y": 380,
    "2y": 800,
    "5y": 2000,
    "10y": 4000,
    "max": 5000,
}


def _series_cache_key(indicator: MacroIndicator, range_key: str, series_id: str | None) -> str:
    source = indicator.source.lower()
    suffix = series_id or "none"
    return f"macro:series:{source}:{indicator.id}:{suffix}:{range_key}"


def _latest_cache_key() -> str:
    return "macro:latest"


def _stale_after_seconds(frequency: str) -> int:
    mapping = {
        "daily": 6 * 3600,
        "weekly": 12 * 3600,
        "monthly": 24 * 3600,
        "quarterly": 24 * 3600,
        "irregular": 24 * 3600,
    }
    return mapping.get(frequency, 6 * 3600)


def _observation_start_for_range(range_key: str) -> str | None:
    # Chart should default to 10 years; use date-based filtering instead of only point limits.
    windows = {
        "1y": 365,
        "2y": 365 * 2,
        "5y": 365 * 5,
        "10y": 365 * 10,
    }
    days = windows.get(range_key)
    if days is None:
        return None
    return (datetime.utcnow() - timedelta(days=days)).date().isoformat()


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
    limit = SERIES_RANGE_LIMITS.get(range_key, SERIES_RANGE_LIMITS["10y"])
    observation_start = _observation_start_for_range(range_key)
    settings = get_settings()
    if not indicator.fred_series:
        return [], "No data source configured"

    fred_series_id = indicator.fred_series
    if indicator.id == "pmi":
        fred_series_id = settings.fred_pmi_series_id

    if not settings.fred_api_key:
        return [], "FRED API key not configured"

    async with semaphore:
        observations = await client.get_series_observations(
            fred_series_id,
            limit=limit,
            observation_start=observation_start,
        )

    points = parse_fred_points(observations)
    if points:
        return list(reversed(points)), None
    return [], f"No data returned for {fred_series_id}"


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
    cached_series = cache.get_series(indicator_id)
    if cached_series:
        last_fetch = max(row.fetched_at for row in cached_series)
        if (datetime.utcnow() - last_fetch).total_seconds() < _stale_after_seconds(
            indicator.frequency
        ):
            return MacroSeriesResponse(
                indicator_id=indicator_id,
                name=indicator.name,
                category=indicator.category,
                unit=indicator.units,
                last_updated=cached_series[-1].date,
                status="cached",
                source=indicator.source,
                expected_frequency=indicator.frequency,
                stale_after_seconds=_stale_after_seconds(indicator.frequency),
                quality="high" if indicator.source == "FRED" else "medium",
                points=[
                    MacroSeriesPoint(date=row.date, value=row.value)
                    for row in cached_series
                    if row.value is not None
                ],
                history_points=len(cached_series),
                error=None,
            )

    points, error = await _fetch_series(indicator, range_key, client, semaphore)
    await client.close()

    if points:
        cache.set_series_points(indicator_id, points)

    last_updated = points[-1][0] if points else None
    response = MacroSeriesResponse(
        indicator_id=indicator_id,
        name=indicator.name,
        category=indicator.category,
        unit=indicator.units,
        last_updated=last_updated,
        status="live" if points else "unavailable",
        source=indicator.source if points else None,
        expected_frequency=indicator.frequency,
        stale_after_seconds=_stale_after_seconds(indicator.frequency),
        quality="high" if indicator.source == "FRED" else "low",
        points=[MacroSeriesPoint(date=date, value=value) for date, value in points],
        history_points=len(points),
        error=error,
    )

    ttl_seconds = (
        settings.cache_ttl_series_1y if range_key in {"1y", "2y"} else settings.cache_ttl_series_5y
    )
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
        cached_latest = cache.get_latest(indicator.id)
        if cached_latest:
            age_seconds = (datetime.utcnow() - cached_latest.fetched_at).total_seconds()
            if age_seconds < _stale_after_seconds(indicator.frequency):
                latest_items.append(
                    MacroLatestItem(
                        indicator_id=indicator.id,
                        name=indicator.name,
                        value=cached_latest.value,
                        change=cached_latest.change,
                        unit=indicator.units,
                        last_updated=cached_latest.last_updated,
                        category=indicator.category,
                        status="cached",
                        source=cached_latest.source,
                        history_points=cache.get_series(indicator.id).__len__(),
                        expected_frequency=indicator.frequency,
                        stale_after_seconds=_stale_after_seconds(indicator.frequency),
                        quality=cached_latest.quality or "medium",
                        error=cached_latest.error,
                    )
                )
                return

        points, error = await _fetch_series(indicator, "1y", client, semaphore)
        latest_value = points[-1][1] if points else None
        change = None
        if points and len(points) >= 2:
            change = points[-1][1] - points[-2][1]
        latest_items.append(
            MacroLatestItem(
                indicator_id=indicator.id,
                name=indicator.name,
                value=latest_value,
                change=change,
                unit=indicator.units,
                last_updated=points[-1][0] if points else None,
                category=indicator.category,
                status="live" if points else "unavailable",
                source=indicator.source if points else None,
                history_points=len(points),
                expected_frequency=indicator.frequency,
                stale_after_seconds=_stale_after_seconds(indicator.frequency),
                quality="high" if indicator.source == "FRED" else "low",
                error=error if not points else None,
            )
        )
        if points:
            as_of = max(as_of, points[-1][0])
            cache.set_series_points(indicator.id, points)
        cache.set_latest(
            IndicatorLatest(
                indicator_id=indicator.id,
                fetched_at=datetime.utcnow(),
                last_updated=points[-1][0] if points else None,
                value=latest_value,
                change=change,
                status="live" if points else "unavailable",
                source=indicator.source if points else None,
                quality="high" if indicator.source == "FRED" else "low",
                error=error if not points else None,
                payload_json=None,
            )
        )

    await asyncio.gather(*[build_latest(ind) for ind in all_indicators()])
    await client.close()

    response = MacroLatestResponse(as_of=as_of, latest=latest_items)
    cache.set_cache(_latest_cache_key(), response.model_dump(), settings.cache_ttl_latest)
    return response


def clear_cache() -> None:
    settings = get_settings()
    cache = CacheStore(settings.cache_db_url)
    cache.clear_all()
