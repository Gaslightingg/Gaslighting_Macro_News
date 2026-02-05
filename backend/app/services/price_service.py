from __future__ import annotations

from datetime import datetime, timedelta
import asyncio
import csv
import logging
from io import StringIO

import httpx

from ..models.schemas import PriceHistoryMeta, PriceHistoryPoint, PriceHistoryResponse, PricesResponse
from ..providers.price_normalizer import normalize_price_ticker
from ..services.price_catalog import PRICE_TICKERS, PriceConfig, resolve_price_config
from ..utils.price_history_db import HistoryPoint, PriceHistoryStore
from ..utils.settings import get_settings

logger = logging.getLogger(__name__)

LATEST_TTL = timedelta(minutes=15)
HISTORY_TTL = timedelta(hours=24)
SPARKLINE_POINTS = 120



_store_cache: dict[str, PriceHistoryStore] = {}
_store_cache_lock = asyncio.Lock()


async def _get_store() -> PriceHistoryStore:
    settings = get_settings()
    db_path = settings.resolved_database_path()
    cache_key = str(db_path)
    store = _store_cache.get(cache_key)
    if store is not None:
        return store

    async with _store_cache_lock:
        store = _store_cache.get(cache_key)
        if store is None:
            store = PriceHistoryStore(db_path)
            _store_cache[cache_key] = store
    return store

def _parse_range(range_key: str) -> timedelta | None:
    mapping = {
        "1m": timedelta(days=30),
        "3m": timedelta(days=90),
        "6m": timedelta(days=180),
        "1y": timedelta(days=365),
        "2y": timedelta(days=365 * 2),
        "5y": timedelta(days=365 * 5),
        "10y": timedelta(days=365 * 10),
        "max": None,
    }
    return mapping.get(range_key, mapping["1y"])


def _history_meta(points: list[HistoryPoint]) -> PriceHistoryMeta:
    data_start = points[0].date if points else None
    data_end = points[-1].date if points else None
    return PriceHistoryMeta(
        data_start=data_start,
        data_end=data_end,
        interval="1d",
        points_count=len(points),
    )


async def _request_with_retries(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str],
    retries: int = 2,
) -> httpx.Response | None:
    attempt = 0
    while True:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            attempt += 1
            if attempt > retries:
                logger.info("Request failed for %s: %s", url, exc)
                return None


def _parse_stooq_rows(csv_text: str) -> tuple[list[dict[str, str]], str | None]:
    text = (csv_text or "").strip()
    if not text:
        return [], "no data"
    lowered = text.lower()
    if lowered.startswith("<html") or lowered.startswith("<!doctype"):
        return [], "html response"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [], "no data"
    # Skip leading garbage lines until likely CSV header.
    header_index = 0
    for i, line in enumerate(lines[:5]):
        if "," in line:
            header_index = i
            break
    candidate_csv = "\n".join(lines[header_index:])
    reader = csv.DictReader(StringIO(candidate_csv))
    if not reader.fieldnames:
        return [], "missing csv header"

    normalized_fields = {field.strip().lower(): field for field in reader.fieldnames if field}
    date_key = normalized_fields.get("date")
    close_key = normalized_fields.get("close")
    if not date_key or not close_key:
        return [], "no date/close columns"

    rows: list[dict[str, str]] = []
    for row in reader:
        date = (row.get(date_key) or "").strip()
        close = (row.get(close_key) or "").strip()
        if not date or not close:
            continue
        rows.append({"Date": date, "Close": close})
    if not rows:
        return [], "no valid rows"
    return rows, None


async def _fetch_stooq_rows(
    client: httpx.AsyncClient,
    stooq_symbol: str,
) -> tuple[list[dict[str, str]], str | None]:
    url = "https://stooq.com/q/d/l/"
    response = await _request_with_retries(client, url, {"s": stooq_symbol, "i": "d"})
    if response is None:
        logger.warning("Stooq request failed for symbol=%s", stooq_symbol)
        return [], "request failed"
    rows, error = _parse_stooq_rows(response.text)
    if error is not None:
        logger.info("Stooq no data for symbol=%s: %s", stooq_symbol, error)
    return rows, error


async def _fetch_stooq_latest(
    client: httpx.AsyncClient,
    stooq_symbol: str,
) -> tuple[float, float, str] | None:
    rows, error = await _fetch_stooq_rows(client, stooq_symbol)
    if error is not None or len(rows) < 2:
        return None
    latest = rows[-1]
    previous = rows[-2]
    try:
        latest_close = float(latest["Close"])
        previous_close = float(previous["Close"])
    except (KeyError, ValueError):
        return None
    if previous_close == 0:
        return None
    change_pct = ((latest_close - previous_close) / previous_close) * 100
    return latest_close, round(change_pct, 2), latest["Date"]


async def _fetch_stooq_history(
    client: httpx.AsyncClient,
    stooq_symbol: str,
) -> list[HistoryPoint]:
    rows, error = await _fetch_stooq_rows(client, stooq_symbol)
    if error is not None:
        return []
    points: list[HistoryPoint] = []
    for row in rows:
        try:
            value = float(row["Close"])
        except (KeyError, ValueError):
            continue
        date = row.get("Date")
        if not date:
            continue
        points.append(HistoryPoint(date=date, value=value, change_pct=None))
    return points


async def _fetch_yfinance_latest(symbol: str) -> tuple[float, float, str] | None:
    try:
        import yfinance as yf
    except ImportError:
        return None

    def _run() -> tuple[float, float, str] | None:
        try:
            history = yf.Ticker(symbol).history(period="5d", interval="1d")
        except Exception:
            return None
        if history.empty or len(history) < 2:
            return None
        latest = history.iloc[-1]
        previous = history.iloc[-2]
        latest_close = float(latest["Close"])
        previous_close = float(previous["Close"])
        if previous_close == 0:
            return None
        change_pct = ((latest_close - previous_close) / previous_close) * 100
        return latest_close, round(change_pct, 2), latest.name.strftime("%Y-%m-%d")

    return await asyncio.to_thread(_run)


async def _fetch_yfinance_history(symbol: str) -> list[HistoryPoint]:
    try:
        import yfinance as yf
    except ImportError:
        return []

    def _run() -> list[HistoryPoint]:
        try:
            history = yf.Ticker(symbol).history(period="10y", interval="1d")
        except Exception:
            return []
        if history.empty:
            return []
        points: list[HistoryPoint] = []
        for idx, row in history.iterrows():
            try:
                value = float(row["Close"])
            except (KeyError, ValueError, TypeError):
                continue
            date = idx.strftime("%Y-%m-%d")
            points.append(HistoryPoint(date=date, value=value, change_pct=None))
        return points

    return await asyncio.to_thread(_run)


def _candidate_stooq_symbols(config: PriceConfig) -> list[str]:
    return [config.stooq_symbol, *config.stooq_fallback_symbols]



def _is_fresh(timestamp: str | None, ttl: timedelta) -> bool:
    if not timestamp:
        return False
    try:
        fetched_at = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return datetime.utcnow() - fetched_at < ttl


async def _ensure_history(
    store: PriceHistoryStore,
    client: httpx.AsyncClient,
    config: PriceConfig,
) -> list[HistoryPoint]:
    meta_key = f"history:{config.id}:updated_at"
    last_update = await store.get_meta(meta_key)
    points = await store.get_history(config.id)
    if points and _is_fresh(last_update, HISTORY_TTL):
        return points

    for candidate in _candidate_stooq_symbols(config):
        fresh_points = await _fetch_stooq_history(client, candidate)
        if fresh_points:
            await store.upsert_history(config.id, fresh_points, "stooq")
            await store.set_meta(meta_key, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
            return fresh_points

    fresh_points = await _fetch_yfinance_history(config.yfinance_symbol) if config.yfinance_symbol else []
    if fresh_points:
        await store.upsert_history(config.id, fresh_points, "yfinance")
        await store.set_meta(meta_key, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        return fresh_points
    return points


async def _ensure_latest(
    store: PriceHistoryStore,
    client: httpx.AsyncClient,
    config: PriceConfig,
) -> dict[str, object] | None:
    cached = await store.get_latest(config.id)
    meta_key = f"latest:{config.id}:updated_at"
    last_update = await store.get_meta(meta_key)
    if cached and _is_fresh(last_update, LATEST_TTL):
        cached["status"] = cached.get("status") or "cached"
        cached["quality"] = cached.get("quality") or "high"
        return cached

    for candidate in _candidate_stooq_symbols(config):
        latest = await _fetch_stooq_latest(client, candidate)
        if latest:
            value, change_pct, date = latest
            payload = {
                "value": value,
                "change": change_pct,
                "change_pct": change_pct,
                "last_updated": date,
                "source": "stooq",
                "status": "live",
                "quality": "high",
            }
            await store.upsert_latest(config.id, payload)
            await store.set_meta(meta_key, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
            return payload

    latest = await _fetch_yfinance_latest(config.yfinance_symbol) if config.yfinance_symbol else None
    if latest:
        value, change_pct, date = latest
        payload = {
            "value": value,
            "change": change_pct,
            "change_pct": change_pct,
            "last_updated": date,
            "source": "yfinance",
            "status": "live",
            "quality": "high",
        }
        await store.upsert_latest(config.id, payload)
        await store.set_meta(meta_key, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        return payload

    if cached:
        cached["status"] = "stale"
        cached["quality"] = "low"
        return cached
    return None


async def get_prices_payload() -> PricesResponse:
    store = await _get_store()
    now = datetime.utcnow()

    async with httpx.AsyncClient(timeout=20) as client:
        tasks = []
        for config in PRICE_TICKERS:
            tasks.append(_build_ticker_payload(config, store, client))
        tickers = await asyncio.gather(*tasks)

    return PricesResponse(
        as_of=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        tickers=tickers,
    )


async def _build_ticker_payload(config: PriceConfig, store: PriceHistoryStore, client: httpx.AsyncClient) -> dict:
    try:
        latest = await _ensure_latest(store, client, config)
        history = await _ensure_history(store, client, config)
        meta = _history_meta(history)

        spark_points = history[-SPARKLINE_POINTS:] if history else []
        history_points = [PriceHistoryPoint(date=p.date, value=p.value) for p in spark_points]

        if latest is None and not history:
            return normalize_price_ticker(
                {
                    "id": config.id,
                    "symbol": config.symbol,
                    "name": config.name,
                    "asset_class": config.asset_class,
                    "unit": config.unit,
                    "history_points": history_points,
                    "history_meta": meta,
                },
                now=datetime.utcnow(),
                source=None,
                status="error",
                error="No price data available from stooq/yfinance",
            )

        # status is required by PriceTicker schema; force a safe non-null default.
        safe_status = (latest or {}).get("status") or "cached"

        return normalize_price_ticker(
            {
                "id": config.id,
                "symbol": config.symbol,
                "name": config.name,
                "asset_class": config.asset_class,
                "unit": config.unit,
                "value": latest.get("value") if latest else None,
                "change": latest.get("change") if latest else None,
                "change_pct": latest.get("change_pct") if latest else None,
                "last_updated": latest.get("last_updated") if latest else None,
                "history_points": history_points,
                "history_meta": meta,
            },
            now=datetime.utcnow(),
            source=latest.get("source") if latest else None,
            status=safe_status,
            error=None,
        )
    except Exception as exc:
        logger.exception("Failed to build ticker payload for %s", config.id)
        return normalize_price_ticker(
            {
                "id": config.id,
                "symbol": config.symbol,
                "name": config.name,
                "asset_class": config.asset_class,
                "unit": config.unit,
                "history_points": [],
                "history_meta": PriceHistoryMeta(
                    data_start=None,
                    data_end=None,
                    interval="1d",
                    points_count=0,
                ),
            },
            now=datetime.utcnow(),
            source=None,
            status="error",
            error=f"Ticker fetch failed: {exc}",
        )


async def get_price_history_payload(symbol: str, range_key: str) -> PriceHistoryResponse:
    config = resolve_price_config(symbol)
    if not config:
        raise ValueError(f"Unknown symbol: {symbol}")

    store = await _get_store()
    async with httpx.AsyncClient(timeout=20) as client:
        history = await _ensure_history(store, client, config)

    meta = _history_meta(history)
    points = history
    window = _parse_range(range_key)
    if window and history:
        try:
            end_date = datetime.strptime(history[-1].date, "%Y-%m-%d")
            cutoff = end_date - window
            points = [
                item
                for item in history
                if datetime.strptime(item.date, "%Y-%m-%d") >= cutoff
            ]
        except ValueError:
            points = history

    response_points = [PriceHistoryPoint(date=p.date, value=p.value) for p in points]
    status = "live" if response_points else "unavailable"
    quality = "high" if response_points else "low"

    return PriceHistoryResponse(
        id=config.id,
        symbol=config.symbol,
        name=config.name,
        asset_class=config.asset_class,
        unit=config.unit,
        source="stooq" if response_points else None,
        status=status,
        quality=quality,
        points=response_points,
        history_meta=meta,
    )
