from __future__ import annotations

from datetime import datetime, timedelta
import asyncio
import csv
import logging
from io import StringIO
import json
from pathlib import Path
from time import perf_counter

import httpx

from ..models.schemas import (
    PriceHistoryMeta,
    PriceHistoryPoint,
    PriceHistoryResponse,
    PriceProviderHealthItem,
    PriceProviderHealthResponse,
    PricesResponse,
)
from ..providers.price_normalizer import normalize_price_ticker
from ..services.price_catalog import PRICE_TICKERS, PriceConfig, resolve_price_config
from ..services.provider_map import get_provider_plan
from ..services.symbols import get_symbol_mapping
from ..utils.price_history_db import HistoryPoint, PriceHistoryStore
from ..utils.settings import get_settings

logger = logging.getLogger(__name__)

LATEST_TTL = timedelta(minutes=15)
HISTORY_TTL = timedelta(hours=24)
SPARKLINE_POINTS = 120
UNSUPPORTED_PROVIDER_SYMBOLS: dict[tuple[str, str], str] = {
    ("stooq", "spx"): "unsupported_symbol",
    ("stooq", "^spx"): "unsupported_symbol",
    ("stooq", "ndx"): "unsupported_symbol",
    ("stooq", "^ndx"): "unsupported_symbol",
    ("stooq", "nq.f"): "unsupported_symbol",
    ("stooq", "nq=f"): "unsupported_symbol",
}

_prices_cache: dict[str, object] = {"payload": None, "fetched_at": None}
_prices_cache_lock = asyncio.Lock()


_store_cache: dict[str, PriceHistoryStore] = {}
_store_cache_lock = asyncio.Lock()

_seed_cache: dict[str, dict[str, object]] | None = None


def _load_seed_prices() -> dict[str, dict[str, object]]:
    global _seed_cache
    if _seed_cache is not None:
        return _seed_cache
    seed_path = Path(__file__).resolve().parents[1] / "data" / "seed_prices.json"
    try:
        with seed_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            _seed_cache = payload
        else:
            _seed_cache = {}
    except FileNotFoundError:
        _seed_cache = {}
    except json.JSONDecodeError:
        logger.warning("Seed price file invalid JSON: %s", seed_path)
        _seed_cache = {}
    return _seed_cache


def _seed_for_config(config: PriceConfig) -> dict[str, object] | None:
    seeds = _load_seed_prices()
    if not seeds:
        return None
    return (
        seeds.get(config.id)
        or seeds.get(config.symbol)
        or seeds.get(config.symbol.upper())
        or seeds.get(config.symbol.lower())
    )


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
        except httpx.HTTPStatusError as exc:
            attempt += 1
            status_code = exc.response.status_code if exc.response is not None else None
            # 4xx is usually a permanent error (bad symbol/params), so retrying only adds latency.
            if status_code is not None and 400 <= status_code < 500:
                logger.warning(
                    "Request failed for %s with status=%s: %s",
                    url,
                    status_code,
                    exc,
                )
                return None
            backoff = 0.4 * attempt
            if attempt > retries:
                logger.warning("Request failed for %s after %s attempts: %s", url, attempt, exc)
                return None
            await asyncio.sleep(backoff)
        except httpx.RequestError as exc:
            attempt += 1
            backoff = 0.4 * attempt
            if attempt > retries:
                logger.warning(
                    "Connection failed for %s after %s attempts: %s",
                    url,
                    attempt,
                    exc,
                )
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
    unsupported_reason = UNSUPPORTED_PROVIDER_SYMBOLS.get(("stooq", stooq_symbol.lower()))
    if unsupported_reason:
        return [], unsupported_reason
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


async def _fetch_yahoo_chart_rows(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    range_key: str,
) -> tuple[list[tuple[str, float]], str | None]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    response = await _request_with_retries(client, url, {"interval": "1d", "range": range_key}, retries=1)
    if response is None:
        return [], "provider_error"
    try:
        payload = response.json()
    except ValueError:
        return [], "parse_error"
    chart = ((payload or {}).get("chart") or {})
    result = (chart.get("result") or [None])[0] or {}
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [None])[0] or {})
    closes = quote.get("close") or []
    if not timestamps or not closes:
        return [], "no_data"
    points: list[tuple[str, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        try:
            value = float(close)
            date = datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
        except Exception:
            continue
        points.append((date, value))
    if not points:
        return [], "no_data"
    return points, None


async def _fetch_yfinance_latest(
    client: httpx.AsyncClient,
    symbol: str,
    timeout_seconds: float,
) -> tuple[float, float, str] | None:
    points, _error = await _fetch_yahoo_chart_rows(client, symbol, range_key="5d")
    if len(points) < 2:
        return None
    latest_date, latest_close = points[-1]
    _prev_date, prev_close = points[-2]
    if prev_close == 0:
        return None
    change_pct = ((latest_close - prev_close) / prev_close) * 100
    return latest_close, round(change_pct, 2), latest_date


async def _fetch_yfinance_history(
    client: httpx.AsyncClient,
    symbol: str,
    timeout_seconds: float,
) -> list[HistoryPoint]:
    points, _error = await _fetch_yahoo_chart_rows(client, symbol, range_key="10y")
    return [HistoryPoint(date=date, value=value, change_pct=None) for date, value in points]


def _resolve_symbols(config: PriceConfig) -> dict[str, str | None]:
    mapping = get_symbol_mapping(config.id)
    return {
        "stooq": mapping.get("stooq") or None,
        "stooq_fallbacks": tuple(mapping.get("stooq_fallbacks") or ()),
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


def _age_seconds(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%d")
    except ValueError:
        try:
            parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None
    return max(0, int((datetime.utcnow() - parsed).total_seconds()))


def _log_ticker_result(
    *,
    config: PriceConfig,
    provider: str | None,
    status: str,
    source: str | None,
    latency_ms: float,
    error_reason: str | None,
    fallback_chain: list[str] | None = None,
) -> None:
    logger.info(
        "price_fetch ticker=%s symbol=%s provider=%s status=%s source=%s latency_ms=%.1f error_reason=%s fallback_chain=%s",
        config.id,
        config.symbol,
        provider or "none",
        status,
        source or "none",
        latency_ms,
        error_reason or "none",
        ",".join(fallback_chain or []),
    )


async def _ensure_history(
    store: PriceHistoryStore,
    client: httpx.AsyncClient,
    config: PriceConfig,
    timeout_seconds: float,
) -> list[HistoryPoint]:
    meta_key = f"history:{config.id}:updated_at"
    last_update = await store.get_meta(meta_key)
    points = await store.get_history(config.id)
    if points and _is_fresh(last_update, HISTORY_TTL):
        return points

    plan = get_provider_plan(config.id)
    chain = plan.history_chain if plan else ()
    for attempt in chain:
        if attempt.provider == "stooq":
            fresh_points = await _fetch_stooq_history(client, attempt.symbol)
        elif attempt.provider == "yfinance":
            fresh_points = await _fetch_yfinance_history(client, attempt.symbol, timeout_seconds=timeout_seconds)
        else:
            fresh_points = []
        if fresh_points:
            return fresh_points
    return points


async def _ensure_latest(
    store: PriceHistoryStore,
    client: httpx.AsyncClient,
    config: PriceConfig,
    timeout_seconds: float,
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

    plan = get_provider_plan(config.id)
    if not plan:
        return {"status": "error", "error_reason": "no_sources_configured", "tried_sources": []}
    tried_sources: list[str] = []
    error_reason: str | None = None
    for attempt in plan.latest_chain:
        tried_sources.append(f"{attempt.provider}:{attempt.symbol}")
        latest: tuple[float, float, str] | None = None
        current_error: str | None = None
        if attempt.provider == "stooq":
            latest, current_error = await _fetch_stooq_latest(client, attempt.symbol)
        elif attempt.provider == "yfinance":
            latest = await _fetch_yfinance_latest(client, attempt.symbol, timeout_seconds=timeout_seconds)
            current_error = None if latest else "yfinance_failed"
        if latest:
            value, change_pct, date = latest
            payload = {
                "value": value,
                "change": change_pct,
                "change_pct": change_pct,
                "last_updated": date,
                "source": attempt.provider,
                "provider": attempt.provider,
                "provider_symbol": attempt.symbol,
                "status": "live",
                "quality": "high",
                "tried_sources": tried_sources,
                "_persist": True,
            }
            return payload
        error_reason = current_error or error_reason or "provider_error"

    if not tried_sources and error_reason is None:
        error_reason = "no_sources_configured"

    if cached:
        cached["status"] = "stale"
        cached["quality"] = "low"
        cached["source"] = cached.get("source") or "db"
        cached["provider"] = cached.get("provider") or cached.get("source")
        cached["tried_sources"] = tried_sources
        cached["error_reason"] = error_reason
        return cached
    return {"status": "error", "error_reason": error_reason, "tried_sources": tried_sources}


async def get_prices_payload(*, bypass_cache: bool = False) -> PricesResponse:
    store = await _get_store()
    configs = list(PRICE_TICKERS)
    now = datetime.utcnow()
    start_time = datetime.utcnow()
    settings = get_settings()
    cache_ttl = timedelta(seconds=settings.cache_ttl_prices)
    effective_timeout = min(settings.price_fetch_timeout_seconds, 1.8)
    cache_hit = False

    if not bypass_cache:
        async with _prices_cache_lock:
            cached_payload = _prices_cache.get("payload")
            cached_at = _prices_cache.get("fetched_at")
            if isinstance(cached_at, datetime) and cached_payload and now - cached_at < cache_ttl:
                cache_hit = True
                logger.info("Prices cache hit (age=%.1fs)", (now - cached_at).total_seconds())
                return cached_payload
    logger.info("Prices cache miss (bypass=%s)", bypass_cache)

    errors: dict[str, str] = {}
    timed_out = False
    http_timeout = httpx.Timeout(
        timeout=effective_timeout,
        connect=min(1.0, effective_timeout),
    )
    async with httpx.AsyncClient(timeout=http_timeout) as client:
        semaphore = asyncio.Semaphore(settings.price_fetch_concurrency)
        ticker_by_id: dict[str, dict] = {}

        async def run_for_ticker(config: PriceConfig) -> tuple[str, dict]:
            started = perf_counter()
            try:
                ticker = await asyncio.wait_for(
                    _build_ticker_payload(
                        config,
                        store,
                        client,
                        semaphore,
                        timeout_seconds=effective_timeout,
                        allow_seed=settings.allow_seed_prices,
                    ),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                nonlocal timed_out
                timed_out = True
                ticker = await _build_cached_payload(
                    config,
                    store,
                    error="timeout",
                    allow_seed=settings.allow_seed_prices,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ticker fetch failed for %s: %s", config.id, exc)
                ticker = await _build_cached_payload(
                    config,
                    store,
                    error=f"provider_error:{exc}",
                    allow_seed=settings.allow_seed_prices,
                )
            latency_ms = (perf_counter() - started) * 1000
            ticker["fetch_latency_ms"] = round(latency_ms, 2)
            ticker["age_seconds"] = _age_seconds(ticker.get("last_updated"))
            ticker["freshness_seconds"] = int(LATEST_TTL.total_seconds())
            _log_ticker_result(
                config=config,
                provider=ticker.get("provider") or ticker.get("source"),
                status=str(ticker.get("status") or "unknown"),
                source=ticker.get("source"),
                latency_ms=latency_ms,
                error_reason=ticker.get("error_reason") or ticker.get("error"),
                fallback_chain=list(ticker.get("tried_sources") or []),
            )
            return config.id, ticker

        results = await asyncio.gather(*(run_for_ticker(config) for config in configs), return_exceptions=True)
        for idx, result in enumerate(results):
            config = configs[idx]
            if isinstance(result, Exception):
                logger.warning("Ticker task failed for %s: %s", config.id, result)
                ticker = await _build_cached_payload(
                    config,
                    store,
                    error="task_failed",
                    allow_seed=settings.allow_seed_prices,
                )
                ticker["error_reason"] = ticker.get("error_reason") or "task_failed"
                ticker_by_id[config.id] = ticker
            else:
                key, ticker = result
                ticker_by_id[key] = ticker

    tickers = [ticker_by_id.get(config.id) for config in configs if ticker_by_id.get(config.id)]
    for ticker in tickers:
        if ticker.get("status") == "live" and isinstance(ticker.get("value"), (int, float)):
            ticker_id = str(ticker.get("id"))
            latest_payload = {
                "value": ticker.get("value"),
                "change": ticker.get("change"),
                "change_pct": ticker.get("change_pct"),
                "last_updated": ticker.get("last_updated"),
                "source": ticker.get("provider") or ticker.get("source"),
            }
            try:
                await store.upsert_latest(ticker_id, latest_payload)
                await store.set_meta(
                    f"latest:{ticker_id}:updated_at",
                    datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Price persistence failed for %s: %s", ticker_id, exc)
                ticker["status"] = "stale"
                ticker["error_reason"] = "persistence_failed"
                ticker["error"] = str(exc)
    for ticker in tickers:
        if ticker.get("error") or ticker.get("status") in {"error", "empty", "unsupported"}:
            errors[str(ticker.get("id"))] = str(ticker.get("error") or ticker.get("error_reason") or "error")

    response = PricesResponse(
        as_of=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        tickers=tickers,
        errors=errors,
        timed_out=timed_out,
        cache_bypassed=bypass_cache,
        summary={
            "live": sum(1 for t in tickers if t.get("status") == "live"),
            "cache": sum(1 for t in tickers if t.get("status") == "cached"),
            "stale": sum(1 for t in tickers if t.get("status") == "stale"),
            "seed": sum(1 for t in tickers if t.get("status") == "seed"),
            "error": sum(1 for t in tickers if t.get("status") == "error"),
            "empty": sum(1 for t in tickers if t.get("status") == "empty"),
            "unsupported": sum(1 for t in tickers if t.get("status") == "unsupported"),
        },
    )
    duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
    ok_count = sum(1 for ticker in tickers if isinstance(ticker.get("value"), (int, float)))
    seed_count = sum(1 for ticker in tickers if ticker.get("status") == "seed")
    stale_count = sum(1 for ticker in tickers if ticker.get("status") in {"stale", "cached"})
    logger.info(
        "Prices fetch completed in %.1fms (tickers=%s, updated=%s, stale=%s, seed=%s, errors=%s, timed_out=%s, cache_hit=%s)",
        duration_ms,
        len(tickers),
        ok_count,
        stale_count,
        seed_count,
        len(errors),
        timed_out,
        cache_hit,
    )
    async with _prices_cache_lock:
        if not bypass_cache:
            _prices_cache["payload"] = response
            _prices_cache["fetched_at"] = now
    return response


async def _build_ticker_payload(
    config: PriceConfig,
    store: PriceHistoryStore,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    timeout_seconds: float,
    allow_seed: bool,
) -> dict:
    async with semaphore:
        return await _build_ticker_payload_inner(config, store, client, timeout_seconds, allow_seed)


async def _build_ticker_payload_inner(
    config: PriceConfig,
    store: PriceHistoryStore,
    client: httpx.AsyncClient,
    timeout_seconds: float,
    allow_seed: bool,
) -> dict:
    try:
        latest = await _ensure_latest(store, client, config, timeout_seconds)
        history = await _ensure_history(store, client, config, timeout_seconds)
        meta = _history_meta(history)

        spark_points = history[-SPARKLINE_POINTS:] if history else []
        history_points = [PriceHistoryPoint(date=p.date, value=p.value) for p in spark_points]

        seed = _seed_for_config(config)
        if latest is None and not history and seed and allow_seed:
            return normalize_price_ticker(
                {
                    "id": config.id,
                    "symbol": config.symbol,
                    "name": config.name,
                    "asset_class": config.asset_class,
                    "unit": config.unit,
                    "value": seed.get("price"),
                    "change": seed.get("change"),
                    "change_pct": seed.get("change_pct"),
                    "last_updated": seed.get("as_of"),
                    "history_points": history_points,
                    "history_meta": meta,
                },
                now=datetime.utcnow(),
                source="seed",
                provider="seed",
                status="seed",
                error="seeded fallback",
                error_reason="seed_fallback",
            )
        if latest is None and not history:
            return normalize_price_ticker(
                {
                    "id": config.id,
                    "symbol": config.symbol,
                    "name": config.name,
                    "asset_class": config.asset_class,
                    "unit": config.unit,
                    "value": "N/A",
                    "last_updated": "N/A",
                    "history_points": history_points,
                    "history_meta": meta,
                },
                now=datetime.utcnow(),
                source=None,
                provider=None,
                status="empty",
                error="No price data available from stooq/yfinance",
                error_reason="no_price_data",
            )

        # status is required by PriceTicker schema; force a safe non-null default.
        safe_status = (latest or {}).get("status") or "cached"
        tried_sources = (latest or {}).get("tried_sources") or []
        error_reason = (latest or {}).get("error_reason")
        if safe_status == "error" and not error_reason:
            error_reason = "no_price_data"
        if (latest or {}).get("value") is None:
            seed = seed or _seed_for_config(config)
            if seed and allow_seed:
                return normalize_price_ticker(
                    {
                        "id": config.id,
                        "symbol": config.symbol,
                        "name": config.name,
                        "asset_class": config.asset_class,
                        "unit": config.unit,
                        "value": seed.get("price"),
                        "change": seed.get("change"),
                        "change_pct": seed.get("change_pct"),
                        "last_updated": seed.get("as_of"),
                        "history_points": history_points,
                        "history_meta": meta,
                    },
                    now=datetime.utcnow(),
                    source="seed",
                    provider="seed",
                    status="seed",
                    error="seeded fallback",
                    error_reason="seed_fallback",
                    tried_sources=tried_sources,
                )
            safe_status = "empty"
            error_reason = error_reason or "no_price_data"
            if error_reason in {"no_sources_configured", "unsupported_symbol"}:
                safe_status = "unsupported"
            elif error_reason not in {"no_price_data", "no_cached_price"}:
                safe_status = "error"
            latest = {
                "value": "N/A",
                "change": "N/A",
                "change_pct": None,
                "last_updated": "N/A",
                "source": None,
            }

        resolved_source = latest.get("source") if latest else None
        if safe_status in {"stale", "cached"} and not resolved_source:
            resolved_source = "db"
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
            source=resolved_source,
            provider=(latest or {}).get("provider") if latest else None,
            status=safe_status,
            error=None if safe_status not in {"error", "empty"} else "No price data available",
            error_reason=error_reason,
            tried_sources=tried_sources,
        )
    except Exception as exc:
        logger.exception("Failed to build ticker payload for %s", config.id)
        return await _build_cached_payload(config, store, error=f"Ticker fetch failed: {exc}", allow_seed=allow_seed)


async def _build_cached_payload(
    config: PriceConfig,
    store: PriceHistoryStore,
    error: str | None = None,
    allow_seed: bool = False,
) -> dict:
    cached = await store.get_latest(config.id)
    history = await store.get_history(config.id)
    meta = _history_meta(history)
    spark_points = history[-SPARKLINE_POINTS:] if history else []
    history_points = [PriceHistoryPoint(date=p.date, value=p.value) for p in spark_points]
    if cached is None:
        seed = _seed_for_config(config)
        if seed and allow_seed:
            return normalize_price_ticker(
                {
                    "id": config.id,
                    "symbol": config.symbol,
                    "name": config.name,
                    "asset_class": config.asset_class,
                    "unit": config.unit,
                    "value": seed.get("price"),
                    "change": seed.get("change"),
                    "change_pct": seed.get("change_pct"),
                    "last_updated": seed.get("as_of"),
                    "history_points": history_points,
                    "history_meta": meta,
                },
                now=datetime.utcnow(),
                source="seed",
                provider="seed",
                status="seed",
                error="seeded fallback",
                error_reason="seed_fallback",
                tried_sources=[],
            )
        return normalize_price_ticker(
            {
                "id": config.id,
                "symbol": config.symbol,
                "name": config.name,
                "asset_class": config.asset_class,
                "unit": config.unit,
                "value": "N/A",
                "last_updated": "N/A",
                "history_points": history_points,
                "history_meta": meta,
            },
            now=datetime.utcnow(),
            source=None,
            provider=None,
            status="empty",
            error=error or "No cached price data",
            error_reason="no_cached_price",
            tried_sources=[],
        )
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
        provider=cached.get("source") if cached else None,
        status="stale" if cached else "empty",
        error=error or ("No cached price data" if not cached else None),
        error_reason="fallback_db" if cached else "no_cached_price",
        tried_sources=[],
    )


async def get_price_history_payload(symbol: str, range_key: str) -> PriceHistoryResponse:
    config = resolve_price_config(symbol)
    if not config:
        raise ValueError(f"Unknown symbol: {symbol}")

    store = await _get_store()
    settings = get_settings()
    timeout_seconds = min(settings.price_fetch_timeout_seconds, 6.0)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        history = await _ensure_history(store, client, config, timeout_seconds)

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


async def get_price_provider_health_payload() -> PriceProviderHealthResponse:
    settings = get_settings()
    store = await _get_store()
    configs = list(PRICE_TICKERS)
    statuses = {"live": 0, "fallback": 0}
    instruments: list[PriceProviderHealthItem] = []
    for config in configs:
        mapping = _resolve_symbols(config)
        cached = await store.get_latest(config.id)
        cache_status = "empty"
        last_error = None
        if cached:
            updated_at = cached.get("last_updated")
            if _is_fresh(updated_at, LATEST_TTL):
                cache_status = "cache"
            else:
                cache_status = "stale"
            statuses["fallback"] += 1
        else:
            updated_at = None

        age_seconds = _age_seconds(updated_at)
        has_mapping = bool(mapping.get("stooq") or mapping.get("yfinance"))
        if not has_mapping:
            last_error = "invalid_symbol_mapping"
            cache_status = "error"

        instruments.append(
            PriceProviderHealthItem(
                id=config.id,
                symbol=config.symbol,
                stooq_symbol=mapping.get("stooq"),
                yfinance_symbol=mapping.get("yfinance"),
                has_mapping=has_mapping,
                cache_status=cache_status,
                last_updated=updated_at,
                last_error=last_error,
                age_seconds=age_seconds,
            )
        )

    try:
        live_payload = await get_prices_payload(bypass_cache=True)
        statuses["live"] = live_payload.summary.get("live", 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Price live diagnostics failed: %s", exc)

    total = max(1, len(configs))
    live_ratio = statuses["live"] / total
    overall_status = "ok" if live_ratio >= 0.7 else "degraded" if live_ratio > 0 else "down"
    if not settings.allow_seed_prices and any(item.cache_status == "seed" for item in instruments):
        overall_status = "degraded"

    return PriceProviderHealthResponse(
        as_of=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        overall_status=overall_status,
        live_ratio=round(live_ratio, 3),
        instruments=instruments,
    )
