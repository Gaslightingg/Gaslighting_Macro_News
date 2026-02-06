from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from ..providers.news_provider import NewsEvent
from ..services.price_catalog import PRICE_TICKERS
from ..utils.price_history_db import PriceHistoryStore
from ..utils.news_db import NewsStore
from ..utils.settings import get_settings
from ..services.news_sync import sync_news_range
from ..services.news_utils import add_months

_NEWS_CACHE: dict[str, Any] = {"updated_at": None, "payload": None}
_NEWS_CACHE_TTL = timedelta(minutes=15)
_store: NewsStore | None = None
_store_lock = asyncio.Lock()

WINDOWS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}
THRESHOLDS = {"15m": 0.10, "1h": 0.25, "1d": 0.50}
IMPACT_TICKERS = ["sp500", "nas100", "nqmini"]


async def _get_store() -> NewsStore:
    global _store
    if _store is not None:
        return _store
    async with _store_lock:
        if _store is None:
            db_path = get_settings().resolved_database_path()
            _store = NewsStore(db_path)
    return _store


def _parse_date(date: str) -> datetime:
    return datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def sync_news(months_back: int = 6, months_forward: int = 1) -> dict[str, int | str]:
    store = await _get_store()
    sync_result = await sync_news_range(store, months_back=months_back, months_forward=months_forward)
    start = add_months(datetime.now(timezone.utc), -months_back)
    end = add_months(datetime.now(timezone.utc), months_forward)
    events = await store.list_events(_iso(start), _iso(end))
    computed = await _compute_impacts_for_events(store, [_row_to_event(e) for e in events])
    return {
        "created": sync_result.get("created", 0),
        "updated": sync_result.get("updated", 0),
        "impacts_computed": computed,
    }


async def _compute_impacts_for_events(store: NewsStore, events: list[NewsEvent]) -> int:
    settings = get_settings()
    price_store = PriceHistoryStore(settings.resolved_database_path())
    config_by_id = {t.id: t for t in PRICE_TICKERS}
    computed_rows: list[dict] = []

    for event in events:
        if event.status != "RELEASED":
            continue
        t0 = datetime.strptime(event.datetime_utc, "%Y-%m-%dT%H:%M:%SZ")
        for ticker in IMPACT_TICKERS:
            config = config_by_id.get(ticker)
            if config is None:
                continue
            history = await price_store.get_history(ticker)
            if not history:
                for window in WINDOWS:
                    computed_rows.append(_missing_impact(event.id, ticker, window, "price history missing"))
                continue
            for window, delta in WINDOWS.items():
                row = _impact_from_history(event.id, ticker, window, t0, delta, history)
                computed_rows.append(row)

    await store.upsert_impacts(computed_rows)
    return len(computed_rows)


def _missing_impact(event_id: str, ticker: str, window: str, reason: str) -> dict:
    return {
        "event_id": event_id,
        "ticker": ticker,
        "window": window,
        "return_pct": None,
        "direction": "FLAT",
        "computed_at": _iso(datetime.now(timezone.utc)),
        "price_pre": None,
        "price_post": None,
        "t_pre": None,
        "t_post": None,
        "data_quality": "MISSING",
        "reason": reason,
    }


def _impact_from_history(event_id: str, ticker: str, window: str, t0: datetime, delta: timedelta, history: list) -> dict:
    points: list[tuple[datetime, float]] = []
    for p in history:
        try:
            dt = datetime.strptime(p.date, "%Y-%m-%d")
            points.append((dt, float(p.value)))
        except Exception:
            continue
    if len(points) < 2:
        return _missing_impact(event_id, ticker, window, "not enough points")

    pre = None
    for dt, v in points:
        if dt <= t0:
            pre = (dt, v)
        else:
            break
    if pre is None:
        return _missing_impact(event_id, ticker, window, "pre price missing")

    target = t0 + delta
    post = None
    for dt, v in points:
        if dt >= target:
            post = (dt, v)
            break
    if post is None:
        return _missing_impact(event_id, ticker, window, "post price missing")

    if pre[1] == 0:
        return _missing_impact(event_id, ticker, window, "zero baseline")

    ret = ((post[1] - pre[1]) / pre[1]) * 100
    th = THRESHOLDS[window]
    direction = "LONG" if ret >= th else "SHORT" if ret <= -th else "FLAT"
    quality = "GOOD" if window == "1d" else "DAILY_PROXY"
    return {
        "event_id": event_id,
        "ticker": ticker,
        "window": window,
        "return_pct": round(ret, 4),
        "direction": direction,
        "computed_at": _iso(datetime.now(timezone.utc)),
        "price_pre": pre[1],
        "price_post": post[1],
        "t_pre": pre[0].strftime("%Y-%m-%d"),
        "t_post": post[0].strftime("%Y-%m-%d"),
        "data_quality": quality,
        "reason": None,
    }


def _event_with_impact(event: dict, impacts: list[dict]) -> dict:
    impacts_map: dict[str, dict[str, dict]] = {}
    for imp in impacts:
        ticker = imp["ticker"]
        impacts_map.setdefault(ticker, {})[imp["window"]] = {
            "direction": imp["direction"],
            "move": imp["return_pct"],
            "window": imp["window"],
            "data_quality": imp["data_quality"],
            "reason": imp.get("reason"),
        }

    surprise = None
    surprise_pct = None
    try:
        if event.get("actual") is not None and event.get("forecast") is not None:
            a = float(str(event["actual"]).replace("%", "").replace("K", ""))
            f = float(str(event["forecast"]).replace("%", "").replace("K", ""))
            surprise = round(a - f, 4)
            surprise_pct = round((surprise / f) * 100, 4) if f else None
    except Exception:
        surprise = None
        surprise_pct = None

    return {
        **event,
        "impacts": impacts_map,
        "surprise": surprise,
        "surprise_pct": surprise_pct,
    }


def _row_to_event(row: dict) -> NewsEvent:
    return NewsEvent(
        id=row["id"],
        source=row["source"],
        title=row["title"],
        country=row["country"],
        importance=row["importance"],
        datetime_utc=row["datetime_utc"],
        datetime_local=row["datetime_local"],
        unit=row.get("unit"),
        previous=row.get("previous"),
        forecast=row.get("forecast"),
        actual=row.get("actual"),
        revised=row.get("revised"),
        status=row["status"],
        updated_at=row["updated_at"],
    )


async def get_news_payload(
    start: str,
    end: str,
    country: str | None = None,
    status: str | None = None,
    importance: str | None = None,
    search: str | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    cache_updated = _NEWS_CACHE.get("updated_at")
    if cache_updated is None or now - cache_updated > _NEWS_CACHE_TTL:
        await sync_news()
        _NEWS_CACHE["updated_at"] = now

    store = await _get_store()
    rows = await store.list_events(_iso(_parse_date(start)), _iso(_parse_date(end) + timedelta(days=1) - timedelta(seconds=1)))

    if country:
        rows = [r for r in rows if r["country"] == country.upper()]
    if status:
        rows = [r for r in rows if r["status"] == status.upper()]
    if importance:
        rows = [r for r in rows if r["importance"] == importance.upper()]
    if search:
        q = search.lower()
        rows = [r for r in rows if q in r["title"].lower()]

    ids = [r["id"] for r in rows]
    impacts = await store.impacts_for_events(ids)
    impacts_by_event: dict[str, list[dict]] = {}
    for imp in impacts:
        impacts_by_event.setdefault(imp["event_id"], []).append(imp)

    events = [_event_with_impact(r, impacts_by_event.get(r["id"], [])) for r in rows]

    return {
        "updated_at": _iso(datetime.now(timezone.utc)),
        "provider_status": "ok",
        "events": events,
    }




async def get_news_event_payload(event_id: str) -> dict | None:
    store = await _get_store()
    row = await store.get_event(event_id)
    if row is None:
        return None
    impacts = await store.impacts_for_events([event_id])
    return _event_with_impact(row, impacts)
