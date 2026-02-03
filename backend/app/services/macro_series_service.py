from __future__ import annotations

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
from ..providers.fred_provider import FredClient, change_from_points, parse_fred_points
from ..utils.cache import TTLCache, ttl_for_frequency
from ..utils.settings import get_settings
from .macro_catalog import ALL_CATEGORIES, MacroIndicator, all_indicators, find_indicator

SERIES_RANGE_LIMITS = {
    "1m": 35,
    "3m": 100,
    "1y": 380,
    "5y": 2000,
    "max": 5000,
}

_cache = TTLCache()


def _series_cache_key(indicator_id: str, range_key: str) -> str:
    return f"series:{indicator_id}:{range_key}"


def _latest_cache_key() -> str:
    return "latest:macro"


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


def _fetch_series(indicator: MacroIndicator, range_key: str) -> list[tuple[str, float]]:
    limit = SERIES_RANGE_LIMITS.get(range_key, SERIES_RANGE_LIMITS["1y"])
    settings = get_settings()
    if indicator.fred_series and settings.fred_api_key:
        client = FredClient(settings.fred_api_key, timeout=settings.request_timeout)
        observations = client.get_series_observations(indicator.fred_series, limit=limit)
        points = parse_fred_points(observations)
        if points:
            return list(reversed(points))
    return _mock_series(indicator, min(limit, 120))


def get_series_payload(indicator_id: str, range_key: str) -> MacroSeriesResponse:
    indicator = find_indicator(indicator_id)
    if not indicator:
        raise ValueError("Indicator not found")

    cache_key = _series_cache_key(indicator_id, range_key)
    cached = _cache.get(cache_key)
    if cached:
        return cached

    points = _fetch_series(indicator, range_key)
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
    )

    ttl = ttl_for_frequency(indicator.frequency)
    _cache.set(cache_key, response, ttl)
    return response


def get_latest_payload() -> MacroLatestResponse:
    cached = _cache.get(_latest_cache_key())
    if cached:
        return cached

    latest_items: list[MacroLatestItem] = []
    as_of = datetime.utcnow().date().isoformat()
    for indicator in all_indicators():
        series_points = _fetch_series(indicator, "1y")
        if not series_points:
            continue
        latest_value = series_points[-1][1]
        change = None
        if len(series_points) >= 2:
            change = series_points[-1][1] - series_points[-2][1]
        latest_items.append(
            MacroLatestItem(
                indicator_id=indicator.id,
                name=indicator.name,
                value=f"{latest_value:.2f}",
                change=f"{change:+.2f}" if change is not None else "0.00",
                updated=series_points[-1][0],
                category=indicator.category,
            )
        )
        as_of = max(as_of, series_points[-1][0])

    response = MacroLatestResponse(as_of=as_of, latest=latest_items)
    _cache.set(_latest_cache_key(), response, ttl_for_frequency("daily"))
    return response


def clear_cache() -> None:
    _cache.clear()
