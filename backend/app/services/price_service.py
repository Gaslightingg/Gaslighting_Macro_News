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
from ..services.symbols import get_symbol_mapping
from ..utils.price_history_db import HistoryPoint, PriceHistoryStore
from ..utils.settings import get_settings

logger = logging.getLogger(__name__)

LATEST_TTL = timedelta(minutes=15)
HISTORY_TTL = timedelta(hours=24)
SPARKLINE_POINTS = 120

_prices_cache: dict[str, object] = {"payload": None, "fetched_at": None}
_prices_cache_lock = asyncio.Lock()


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
            backoff = 0.4 * attempt
            if attempt > retries:
                logger.warning("Request failed for %s after %s attempts: %s", url, attempt, exc)
                return None
            await asyncio.sleep(backoff)


def _parse_stooq_rows(csv_text: str) -> tuple[list[dict[str, str]], str | None]:
    text = (csv_text or "").strip()
    if not text:
        return [], "stooq_no_data"
    lowered = text.lower()
    if lowered.startswith("<html") or lowered.startswith("<!doctype") or "<html" in lowered:
        return [], "stooq_non_csv"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [], "stooq_no_data"
    # Skip leading garbage lines until likely CSV header.
    header_index = 0
    for i, line in enumerate(lines[:5]):
        if "," in line:
            header_index = i
            break
    candidate_csv = "\n".join(lines[header_index:])
    reader = csv.DictReader(StringIO(candidate_csv))
    if not reader.fieldnames:
        return [], "stooq_missing_columns"

    normalized_fields = {field.strip().lower(): field for field in reader.fieldnames if field}
    date_key = normalized_fields.get("date") or normalized_fields.get("data")
    close_key = (
        normalized_fields.get("close")
        or normalized_fields.get("last")
        or normalized_fields.get("c")
        or normalized_fields.get("zamkniecie")
        or normalized_fields.get("zamknięcie")
    )
    if not date_key or not close_key:
        return [], "stooq_missing_columns"

    rows: list[dict[str, str]] = []
    for row in reader:
        date = (row.get(date_key) or "").strip()
        close = (row.get(close_key) or "").strip()
        if not date or not close:
            continue
        rows.append({"Date": date, "Close": close})
    if not rows:
        return [], "stooq_no_data"
    return rows, None


async def _fetch_stooq_rows(
    client: httpx.AsyncClient,
    stooq_symbol: str,
) -> tuple[list[dict[str, str]], str | None]:
    url = "https://stooq.com/q/d/l/"
    response = await _request_with_retries(client, url, {"s": stooq_symbol, "i": "d"})
    if response is None:
        logger.warning("Stooq request failed for symbol=%s", stooq_symbol)
        return [], "stooq_request_failed"
    rows, error = _parse_stooq_rows(response.text)
    if error is not None:
        snippet = "\n".join((response.text or "").splitlines()[:3])
        logger.warning("Stooq data issue for symbol=%s: %s", stooq_symbol, error)
        logger.debug("Stooq response snippet for %s:\n%s", stooq_symbol, snippet)
    return rows, error


async def _fetch_stooq_latest(
    client: httpx.AsyncClient,
    stooq_symbol: str,
) -> tuple[tuple[float, float, str] | None, str | None]:
    rows, error = await _fetch_stooq_rows(client, stooq_symbol)
    if error is not None or len(rows) < 2:
        return None, error or "stooq_no_data"
    latest = rows[-1]
    previous = rows[-2]
    try:
        latest_close = float(latest["Close"])
        previous_close = float(previous["Close"])
    except (KeyError, ValueError):
        return None, "stooq_missing_columns"
    if previous_close == 0:
        return None, "stooq_no_data"
    change_pct = ((latest_close - previous_close) / previous_close) * 100
    return (latest_close, round(change_pct, 2), latest["Date"]), None


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


async def _fetch_yfinance_latest(symbol: str, timeout_seconds: float) -> tuple[float, float, str] | None:
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
        try:
            latest_close = float(latest["Close"])
            previous_close = float(previous["Close"])
        except Exception:
            return None
        if previous_close == 0:
            return None
        change_pct = ((latest_close - previous_close) / previous_close) * 100
        return latest_close, round(change_pct, 2), latest.name.strftime("%Y-%m-%d")

    for attempt in range(2):
        try:
            result = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            result = None
        if result:
            return result
        await asyncio.sleep(0.4 * (attempt + 1))
    logger.warning("yfinance returned no data for symbol=%s", symbol)
    return None


async def _fetch_yfinance_history(symbol: str, timeout_seconds: float) -> list[HistoryPoint]:
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

    for attempt in range(2):
        try:
            points = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            points = []
        if points:
            return points
        await asyncio.sleep(0.5 * (attempt + 1))
    logger.warning("yfinance returned empty history for symbol=%s", symbol)
    return []


def _resolve_symbols(config: PriceConfig) -> dict[str, str | None]:
    mapping = get_symbol_mapping(config.id)
    return {
        "stooq": mapping.get("stooq") or None,
        "yfinance": mapping.get("yfinance") or None,
    }



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

    symbols = _resolve_symbols(config)
    if symbols.get("stooq"):
        fresh_points = await _fetch_stooq_history(client, symbols["stooq"])
        if fresh_points:
            await store.upsert_history(config.id, fresh_points, "stooq")
            await store.set_meta(meta_key, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
            return fresh_points

    fresh_points = (
        await _fetch_yfinance_history(symbols["yfinance"], timeout_seconds=get_settings().price_fetch_timeout_seconds)
        if symbols.get("yfinance")
        else []
    )
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
        cached["source"] = "db"
        cached["tried_sources"] = []
        return cached

    symbols = _resolve_symbols(config)
    tried_sources: list[str] = []
    error_reason: str | None = None

    if symbols.get("stooq"):
        tried_sources.append("stooq")
        latest, stooq_error = await _fetch_stooq_latest(client, symbols["stooq"])
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
                "tried_sources": tried_sources,
            }
            await store.upsert_latest(config.id, payload)
            await store.set_meta(meta_key, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
            return payload
        error_reason = stooq_error or "stooq_failed"

    if symbols.get("yfinance"):
        tried_sources.append("yfinance")
        latest = await _fetch_yfinance_latest(
            symbols["yfinance"],
            timeout_seconds=get_settings().price_fetch_timeout_seconds,
        )
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
                "tried_sources": tried_sources,
            }
            await store.upsert_latest(config.id, payload)
            await store.set_meta(meta_key, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
            return payload
        error_reason = "yfinance_failed"

    if not tried_sources and error_reason is None:
        error_reason = "no_sources_configured"

    if cached:
        cached["status"] = "stale"
        cached["quality"] = "low"
        cached["source"] = cached.get("source") or "db"
        cached["tried_sources"] = tried_sources
        cached["error_reason"] = error_reason
        return cached
    return {"status": "error", "error_reason": error_reason, "tried_sources": tried_sources}


async def get_prices_payload() -> PricesResponse:
    store = await _get_store()
    now = datetime.utcnow()
    start_time = datetime.utcnow()
    settings = get_settings()
    cache_ttl = timedelta(seconds=settings.cache_ttl_prices)
    cache_hit = False

    async with _prices_cache_lock:
        cached_payload = _prices_cache.get("payload")
        cached_at = _prices_cache.get("fetched_at")
        if isinstance(cached_at, datetime) and cached_payload and now - cached_at < cache_ttl:
            cache_hit = True
            logger.info("Prices cache hit (age=%.1fs)", (now - cached_at).total_seconds())
            return cached_payload
    logger.info("Prices cache miss")

    errors: dict[str, str] = {}
    timed_out = False
    async with httpx.AsyncClient(timeout=settings.price_fetch_timeout_seconds) as client:
        semaphore = asyncio.Semaphore(settings.price_fetch_concurrency)
        tasks: dict[asyncio.Task, PriceConfig] = {}
        for config in PRICE_TICKERS:
            task = asyncio.create_task(_build_ticker_payload(config, store, client, semaphore))
            tasks[task] = config
        done, pending = await asyncio.wait(tasks.keys(), timeout=settings.price_fetch_timeout_seconds)
        tickers: list[dict] = []
        for task in done:
            config = tasks[task]
            try:
                ticker = task.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ticker fetch failed for %s: %s", config.id, exc)
                ticker = await _build_cached_payload(config, store, error=str(exc))
            if ticker.get("error"):
                errors[config.id] = ticker["error"]
            tickers.append(ticker)
        if pending:
            timed_out = True
            logger.warning("Price fetch timed out for %s tickers", len(pending))
            for task in pending:
                task.cancel()
                config = tasks[task]
                ticker = await _build_cached_payload(config, store, error="timeout")
                if ticker.get("error"):
                    errors[config.id] = ticker["error"]
                tickers.append(ticker)

    response = PricesResponse(
        as_of=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        tickers=tickers,
        errors=errors,
        timed_out=timed_out,
    )
    duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
    ok_count = sum(1 for ticker in tickers if ticker.get("value") is not None)
    logger.info(
        "Prices fetch completed in %.1fms (tickers=%s, ok=%s, errors=%s, timed_out=%s, cache_hit=%s)",
        duration_ms,
        len(tickers),
        ok_count,
        len(errors),
        timed_out,
        cache_hit,
    )
    async with _prices_cache_lock:
        _prices_cache["payload"] = response
        _prices_cache["fetched_at"] = now
    return response


async def _build_ticker_payload(
    config: PriceConfig,
    store: PriceHistoryStore,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict:
    async with semaphore:
        return await _build_ticker_payload_inner(config, store, client)


async def _build_ticker_payload_inner(config: PriceConfig, store: PriceHistoryStore, client: httpx.AsyncClient) -> dict:
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
                error_reason="no_price_data",
            )

        # status is required by PriceTicker schema; force a safe non-null default.
        safe_status = (latest or {}).get("status") or "cached"
        tried_sources = (latest or {}).get("tried_sources") or []
        error_reason = (latest or {}).get("error_reason")
        if safe_status == "error" and not error_reason:
            error_reason = "no_price_data"

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
            source=latest.get("source") if safe_status == "live" else "db",
            status=safe_status,
            error=None if safe_status != "error" else "No price data available",
            error_reason=error_reason,
            tried_sources=tried_sources,
        )
    except Exception as exc:
        logger.exception("Failed to build ticker payload for %s", config.id)
        return await _build_cached_payload(config, store, error=f"Ticker fetch failed: {exc}")


async def _build_cached_payload(config: PriceConfig, store: PriceHistoryStore, error: str | None = None) -> dict:
    cached = await store.get_latest(config.id)
    history = await store.get_history(config.id)
    meta = _history_meta(history)
    spark_points = history[-SPARKLINE_POINTS:] if history else []
    history_points = [PriceHistoryPoint(date=p.date, value=p.value) for p in spark_points]
    return normalize_price_ticker(
        {
            "id": config.id,
            "symbol": config.symbol,
            "name": config.name,
            "asset_class": config.asset_class,
            "unit": config.unit,
            "value": cached.get("value") if cached else None,
            "change": cached.get("change") if cached else None,
            "change_pct": cached.get("change_pct") if cached else None,
            "last_updated": cached.get("last_updated") if cached else None,
            "history_points": history_points,
            "history_meta": meta,
        },
        now=datetime.utcnow(),
        source="db",
        status="stale" if cached else "error",
        error=error or ("No cached price data" if not cached else None),
        error_reason="fallback_db" if cached else "no_cached_price",
        tried_sources=[],
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
