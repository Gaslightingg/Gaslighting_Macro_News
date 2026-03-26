from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

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

logger = logging.getLogger(__name__)

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


def _format_value(value: float | None) -> float | str:
    return value if value is not None else "N/A"


def _format_change(value: float | None) -> float | str:
    return value if value is not None else "N/A"


def _format_date(value: str | None) -> str:
    return value or "N/A"


def _quality_for_status(status: str, base_quality: str | None) -> str:
    if status == "live":
        return base_quality or "high"
    if status in {"cached", "stale"}:
        return "medium"
    return "n/a"


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
        logger.info(
            "Attempt macro fetch: provider=%s series=%s indicator=%s -> UNSUPPORTED",
            indicator.source.lower(),
            "none",
            indicator.id,
        )
        return [], "No data source configured"

    fred_series_id = indicator.fred_series
    if indicator.id == "pmi":
        fred_series_id = settings.fred_pmi_series_id

    if not settings.fred_api_key:
        return [], "FRED API key not configured"

    async with semaphore:
        logger.info(
            "Attempt macro fetch: provider=fred series=%s indicator=%s",
            fred_series_id,
            indicator.id,
        )
        observations, error_reason = await client.get_series_observations(
            fred_series_id,
            limit=limit,
            observation_start=observation_start,
        )

    if error_reason:
        logger.info(
            "Result macro fetch: provider=fred series=%s indicator=%s -> %s",
            fred_series_id,
            indicator.id,
            error_reason,
        )
        return [], error_reason
    points = parse_fred_points(observations)
    if points:
        logger.info(
            "Result macro fetch: provider=fred series=%s indicator=%s -> SUCCESS points=%s",
            fred_series_id,
            indicator.id,
            len(points),
        )
        return list(reversed(points)), None
    logger.info(
        "Result macro fetch: provider=fred series=%s indicator=%s -> EMPTY",
        fred_series_id,
        indicator.id,
    )
    return [], f"No data returned for {fred_series_id}"


def log_macro_startup_validation() -> None:
    for indicator in all_indicators():
        if indicator.source.upper() == "FRED" and indicator.fred_series:
            logger.info("Macro series mapping: indicator=%s provider=fred series=%s", indicator.id, indicator.fred_series)
        elif indicator.source.lower() == "unsupported" or not indicator.fred_series:
            logger.warning(
                "Macro series unsupported: indicator=%s provider=%s series=%s",
                indicator.id,
                indicator.source,
                indicator.fred_series or "none",
            )


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
        cached_payload = dict(cached)
        cached_payload["stale"] = cached_payload.get("stale", False)
        missing_inputs: list[str] = []
        if not settings.fred_api_key:
            missing_inputs.append("FRED API key not configured")
        if not settings.bea_api_key:
            missing_inputs.append("BEA API key not configured")
        cached_payload["missing_inputs"] = missing_inputs
        cached_payload.setdefault("errors", [])
        if isinstance(cached_payload.get("latest"), list):
            for item in cached_payload["latest"]:
                if isinstance(item, dict):
                    item["value"] = _format_value(item.get("value"))
                    item["change"] = _format_change(item.get("change"))
                    item["last_updated"] = _format_date(item.get("last_updated"))
                    item["updated_at"] = _format_date(item.get("updated_at") or item.get("last_updated"))
                    status = item.get("status") or "unknown"
                    item["quality"] = _quality_for_status(status, item.get("quality"))
        return MacroLatestResponse(**cached_payload)

    latest_items: list[MacroLatestItem] = []
    stale_response = False
    errors: list[str] = []
    missing_inputs: list[str] = []
    as_of = datetime.utcnow().date().isoformat()
    client = FredClient(settings.fred_api_key or "", timeout=settings.request_timeout)
    semaphore = asyncio.Semaphore(6)

    if not settings.fred_api_key:
        missing_inputs.append("FRED API key not configured")
    if not settings.bea_api_key:
        missing_inputs.append("BEA API key not configured")

    def source_missing_reason(indicator: MacroIndicator) -> str | None:
        if indicator.source.upper() == "FRED" and not settings.fred_api_key:
            return "FRED API key not configured"
        if indicator.source.upper() == "BEA" and not settings.bea_api_key:
            return "BEA API key not configured"
        return None

    def no_data_reason(indicator: MacroIndicator) -> str | None:
        if indicator.fred_series is None and indicator.source.lower() == "derived":
            return "No data source configured"
        return None

    async def build_latest(indicator: MacroIndicator) -> None:
        nonlocal as_of, stale_response
        cached_latest = cache.get_latest(indicator.id)
        missing_reason = source_missing_reason(indicator)
        if missing_reason:
            if missing_reason not in missing_inputs:
                missing_inputs.append(missing_reason)
            latest_items.append(
                MacroLatestItem(
                    indicator_id=indicator.id,
                    name=indicator.name,
                    value=_format_value(cached_latest.value if cached_latest else None),
                    change=_format_change(cached_latest.change if cached_latest else None),
                    unit=indicator.units,
                    last_updated=_format_date(cached_latest.last_updated if cached_latest else None),
                    updated_at=_format_date(cached_latest.last_updated if cached_latest else None),
                    category=indicator.category,
                    status="disabled",
                    source=indicator.source,
                    history_points=len(cache.get_series(indicator.id) or []),
                    expected_frequency=indicator.frequency,
                    stale_after_seconds=_stale_after_seconds(indicator.frequency),
                    quality="n/a",
                    error=missing_reason,
                    reason=missing_reason,
                    stale=True if cached_latest else False,
                )
            )
            stale_response = True
            cache.set_latest(
                IndicatorLatest(
                    indicator_id=indicator.id,
                    fetched_at=datetime.utcnow(),
                    last_updated=cached_latest.last_updated if cached_latest else None,
                    value=cached_latest.value if cached_latest else None,
                    change=cached_latest.change if cached_latest else None,
                    status="disabled",
                    source=indicator.source,
                    quality="low",
                    error=missing_reason,
                    payload_json=None,
                )
            )
            return
        no_data = no_data_reason(indicator)
        if no_data and cached_latest is None:
            latest_items.append(
                MacroLatestItem(
                    indicator_id=indicator.id,
                    name=indicator.name,
                    value="N/A",
                    change="N/A",
                    unit=indicator.units,
                    last_updated="N/A",
                    updated_at="N/A",
                    category=indicator.category,
                    status="empty",
                    source=indicator.source,
                    history_points=0,
                    expected_frequency=indicator.frequency,
                    stale_after_seconds=_stale_after_seconds(indicator.frequency),
                    quality="n/a",
                    error=no_data,
                    reason=no_data,
                    stale=False,
                )
            )
            errors.append(no_data)
            stale_response = True
            return
        if cached_latest:
            age_seconds = (datetime.utcnow() - cached_latest.fetched_at).total_seconds()
            if age_seconds < _stale_after_seconds(indicator.frequency):
                cached_status = cached_latest.status
                if cached_latest.status == "live" and cached_latest.value is not None:
                    cached_status = "cached"
                if cached_latest.status == "cached" and cached_latest.value is None:
                    cached_status = "no_data"
                cached_status = cached_status or "no_data"
                if cached_status != "cached":
                    stale_response = True
                    cached_error = cached_latest.error or ("No data available" if cached_status == "no_data" else None)
                    if cached_error:
                        errors.append(cached_error)
                cached_reason = cached_latest.error or ("No data available" if cached_status == "no_data" else None)
                latest_items.append(
                    MacroLatestItem(
                        indicator_id=indicator.id,
                        name=indicator.name,
                        value=_format_value(cached_latest.value),
                        change=_format_change(cached_latest.change),
                        unit=indicator.units,
                        last_updated=_format_date(cached_latest.last_updated),
                        updated_at=_format_date(cached_latest.last_updated),
                        category=indicator.category,
                        status=cached_status,
                        source=cached_latest.source,
                        history_points=len(cache.get_series(indicator.id) or []),
                        expected_frequency=indicator.frequency,
                        stale_after_seconds=_stale_after_seconds(indicator.frequency),
                        quality=_quality_for_status(cached_status, cached_latest.quality),
                        error=cached_reason,
                        reason=cached_reason,
                        stale=cached_status != "cached",
                    )
                )
                return

        points, error = await _fetch_series(indicator, "1y", client, semaphore)
        cached_latest = cache.get_latest(indicator.id)
        latest_value = points[-1][1] if points else None
        change = None
        if points and len(points) >= 2:
            change = points[-1][1] - points[-2][1]
        if not points and cached_latest and cached_latest.value is not None:
            latest_value = cached_latest.value
            change = cached_latest.change
            error = cached_latest.error or error or "provider failed, using cached"
            stale_response = True
        status = "live" if points else "stale" if cached_latest and cached_latest.value is not None else "empty"
        quality = "high" if indicator.source == "FRED" and points else None
        if not points and error:
            errors.append(error)
        if error and error.startswith("FRED 400"):
            status = "error"
            quality = "n/a"
        if status in {"disabled", "error", "empty", "no_data"}:
            quality = "n/a"
        latest_items.append(
            MacroLatestItem(
                indicator_id=indicator.id,
                name=indicator.name,
                value=_format_value(latest_value),
                change=_format_change(change),
                unit=indicator.units,
                last_updated=_format_date(
                    points[-1][0] if points else cached_latest.last_updated if cached_latest else None
                ),
                updated_at=_format_date(
                    points[-1][0] if points else cached_latest.last_updated if cached_latest else None
                ),
                category=indicator.category,
                status=status,
                source=indicator.source if points else cached_latest.source if cached_latest else indicator.source,
                history_points=len(points) if points else len(cache.get_series(indicator.id) or []) if cached_latest else 0,
                expected_frequency=indicator.frequency,
                stale_after_seconds=_stale_after_seconds(indicator.frequency),
                quality=_quality_for_status(status, quality),
                error=error if not points else None,
                reason=error if not points else None,
                stale=status != "live",
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
                status=status,
                source=indicator.source if points else None,
                quality=_quality_for_status(status, quality),
                error=error if not points else None,
                payload_json=None,
            )
        )

    await asyncio.gather(*[build_latest(ind) for ind in all_indicators()])
    await client.close()

    response = MacroLatestResponse(
        as_of=as_of,
        latest=latest_items,
        stale=stale_response,
        missing_inputs=missing_inputs,
        errors=sorted(set(errors)),
    )
    cache_ttl = settings.cache_ttl_macro_seconds or settings.cache_ttl_latest
    cache.set_cache(_latest_cache_key(), response.model_dump(), cache_ttl)
    return response


def clear_cache() -> None:
    settings = get_settings()
    cache = CacheStore(settings.cache_db_url)
    cache.clear_all()
