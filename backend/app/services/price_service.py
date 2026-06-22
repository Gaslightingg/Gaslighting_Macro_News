from __future__ import annotations

from datetime import datetime, timedelta
import asyncio
from contextvars import ContextVar
import csv
import logging
import sqlite3
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
from ..providers.price_normalizer import normalize_optional_float, normalize_price_ticker
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
    ("stooq", "ndx"): "unsupported_symbol",
    ("stooq", "nq.f"): "unsupported_symbol",
    ("stooq", "nq=f"): "unsupported_symbol",
}

_prices_cache: dict[str, object] = {"payload": None, "fetched_at": None}
_prices_cache_lock = asyncio.Lock()
_prices_refresh_task: asyncio.Task | None = None
_prices_refresh_lock = asyncio.Lock()
_provider_cooldowns: dict[str, datetime] = {}
_inflight_task_created_at: datetime | None = None
_inflight_task_result_published_at: datetime | None = None
_snapshot_written_at: datetime | None = None
_inflight_task_done_at: datetime | None = None
_inflight_owner_rid: str | None = None
_cache_snapshot_version: int = 0
_price_request_id: ContextVar[str] = ContextVar("price_request_id", default="-")
_price_debug_enabled: ContextVar[bool] = ContextVar("price_debug_enabled", default=False)
SHARED_WAITER_TIMEOUT_SECONDS = 2.2
_persistence_worker_lock = asyncio.Lock()
_persistence_worker_task: asyncio.Task | None = None
_persistence_pending_by_symbol: dict[str, dict] = {}
_shared_http_client: httpx.AsyncClient | None = None


_store_cache: dict[str, PriceHistoryStore] = {}
_store_cache_lock = asyncio.Lock()

_seed_cache: dict[str, dict[str, object]] | None = None


def _yfinance_symbol_key(symbol: str) -> str:
    return f"yfinance:{symbol.upper()}"


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


def _provider_on_cooldown(provider: str) -> bool:
    expires_at = _provider_cooldowns.get(provider)
    if not expires_at:
        return False
    if datetime.utcnow() >= expires_at:
        _provider_cooldowns.pop(provider, None)
        return False
    return True


def _set_provider_cooldown(provider: str, seconds: int) -> None:
    _provider_cooldowns[provider] = datetime.utcnow() + timedelta(seconds=seconds)


def _log_attempt(message: str, *args) -> None:
    logger.info("[PRICE_FETCH][rid=%s] " + message, _price_request_id.get(), *args)


def _get_shared_http_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        limits = httpx.Limits(max_connections=40, max_keepalive_connections=20)
        _shared_http_client = httpx.AsyncClient(timeout=timeout, limits=limits, trust_env=False)
    else:
        _shared_http_client.timeout = timeout
    return _shared_http_client


def _sanitize_ticker_numeric_fields(ticker: dict) -> dict:
    raw_value = ticker.get("value")
    raw_change = ticker.get("change")
    raw_change_pct = ticker.get("change_pct")
    ticker["value"] = normalize_optional_float(raw_value)
    ticker["change"] = normalize_optional_float(raw_change)
    ticker["change_pct"] = normalize_optional_float(raw_change_pct)

    invalid_numeric = False
    for raw, normalized in (
        (raw_value, ticker["value"]),
        (raw_change, ticker["change"]),
        (raw_change_pct, ticker["change_pct"]),
    ):
        if raw is not None and normalized is None:
            invalid_numeric = True

    if invalid_numeric:
        ticker["error_reason"] = ticker.get("error_reason") or "invalid_numeric_payload"
        ticker["error"] = ticker.get("error") or "invalid numeric value in ticker payload"
        if ticker.get("status") in {"live", "cached", "stale", "seed"}:
            ticker["status"] = "error"
    return ticker


def _terminal_status_from_ticker(ticker: dict) -> str:
    status = str(ticker.get("status") or "empty")
    if status == "live":
        return "ok_live"
    if status in {"cached", "seed"}:
        return "ok_cached"
    if status == "stale":
        return "stale_db"
    if status == "empty":
        return "empty"
    return "error"


def _build_prices_summary(tickers: list[dict]) -> dict[str, int]:
    summary = {
        "ok_live": 0,
        "ok_cached": 0,
        "stale_db": 0,
        "empty": 0,
        "error": 0,
        "provider_attempt_errors": 0,
        "provider_rate_limits": 0,
        "provider_parse_errors": 0,
        "live": 0,
        "cache": 0,
        "stale": 0,
        "seed": 0,
        "unsupported": 0,
    }
    for ticker in tickers:
        terminal = _terminal_status_from_ticker(ticker)
        summary[terminal] += 1
        raw_status = str(ticker.get("status") or "")
        if raw_status == "live":
            summary["live"] += 1
        elif raw_status == "cached":
            summary["cache"] += 1
        elif raw_status == "stale":
            summary["stale"] += 1
        elif raw_status == "seed":
            summary["seed"] += 1
        elif raw_status == "unsupported":
            summary["unsupported"] += 1
        error_reason = str(ticker.get("error_reason") or "")
        if error_reason:
            summary["provider_attempt_errors"] += 1
            if "rate_limit" in error_reason or "429" in error_reason:
                summary["provider_rate_limits"] += 1
            if "parse" in error_reason or "missing_columns" in error_reason or "non_csv" in error_reason:
                summary["provider_parse_errors"] += 1
    return summary


async def _persist_latest_with_retry(store: PriceHistoryStore, ticker_id: str, latest_payload: dict) -> None:
    updated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for attempt in range(3):
        try:
            if hasattr(store, "upsert_latest_with_meta"):
                await store.upsert_latest_with_meta(
                    ticker_id,
                    latest_payload,
                    f"latest:{ticker_id}:updated_at",
                    updated_at,
                )
            else:
                await store.upsert_latest(ticker_id, latest_payload)
                await store.set_meta(
                    f"latest:{ticker_id}:updated_at",
                    updated_at,
                )
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 2:
                raise
            backoff = 0.2 * (attempt + 1)
            logger.warning(
                "Price persistence locked for %s; retrying attempt=%s backoff=%.2fs",
                ticker_id,
                attempt + 1,
                backoff,
            )
            await asyncio.sleep(backoff)


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_datetime_flexible(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = _parse_iso_timestamp(value)
    if parsed is not None:
        return parsed
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _should_replace_pending(existing: dict, incoming: dict) -> bool:
    existing_dt = _parse_iso_timestamp(existing.get("as_of"))
    incoming_dt = _parse_iso_timestamp(incoming.get("as_of"))
    if existing_dt and incoming_dt:
        return incoming_dt >= existing_dt
    if incoming_dt and not existing_dt:
        return True
    if not incoming_dt and existing_dt:
        return False
    return incoming.get("enqueued_at", datetime.utcnow()) >= existing.get("enqueued_at", datetime.utcnow())


async def _run_persistence_worker(store: PriceHistoryStore) -> None:
    global _persistence_worker_task
    while True:
        async with _persistence_worker_lock:
            if not _persistence_pending_by_symbol:
                _persistence_worker_task = None
                return
            pending_items = list(_persistence_pending_by_symbol.values())
            _persistence_pending_by_symbol.clear()

        request_ids = sorted({str(item.get("request_id") or "-") for item in pending_items})
        joined_rids = ",".join(request_ids)
        logger.info("persistence_batch_started rid=%s batch_size=%s", joined_rids, len(pending_items))
        started = perf_counter()
        persistence_errors = 0

        for item in pending_items:
            ticker_id = str(item["symbol"])
            request_id = str(item.get("request_id") or "-")
            try:
                await _persist_latest_with_retry(store, ticker_id, item["latest_payload"])
            except Exception as exc:  # noqa: BLE001
                persistence_errors += 1
                logger.warning(
                    "persistence_symbol_failed rid=%s symbol=%s error=%s",
                    request_id,
                    ticker_id,
                    exc,
                )
            else:
                logger.info("persistence_symbol_success rid=%s symbol=%s", request_id, ticker_id)

        elapsed_ms = (perf_counter() - started) * 1000
        logger.info(
            "persistence_batch_completed rid=%s batch_size=%s persistence_errors=%s duration_ms=%.1f",
            joined_rids,
            len(pending_items),
            persistence_errors,
            elapsed_ms,
        )


async def _schedule_persistence(store: PriceHistoryStore, tickers: list[dict], request_id: str) -> None:
    global _persistence_worker_task
    if not tickers:
        return
    enqueued = 0
    skipped = 0
    replaced = 0
    now = datetime.utcnow()
    async with _persistence_worker_lock:
        for ticker in tickers:
            ticker_id = str(ticker.get("id") or "")
            if not ticker_id:
                continue
            latest_payload = {
                "value": ticker.get("value"),
                "change": ticker.get("change"),
                "change_pct": ticker.get("change_pct"),
                "last_updated": ticker.get("last_updated"),
                "source": ticker.get("provider") or ticker.get("source"),
            }
            incoming = {
                "symbol": ticker_id,
                "request_id": request_id,
                "as_of": ticker.get("last_updated") or ticker.get("as_of"),
                "latest_payload": latest_payload,
                "enqueued_at": now,
            }
            existing = _persistence_pending_by_symbol.get(ticker_id)
            if existing is None:
                _persistence_pending_by_symbol[ticker_id] = incoming
                enqueued += 1
                continue
            if _should_replace_pending(existing, incoming):
                _persistence_pending_by_symbol[ticker_id] = incoming
                replaced += 1
            else:
                skipped += 1
                logger.info("persistence_batch_skipped_as_stale rid=%s symbol=%s", request_id, ticker_id)

        logger.info(
            "persistence_batch_enqueued rid=%s enqueued=%s replaced=%s skipped=%s pending=%s",
            request_id,
            enqueued,
            replaced,
            skipped,
            len(_persistence_pending_by_symbol),
        )
        if replaced:
            logger.info("persistence_batch_coalesced rid=%s symbols_replaced=%s", request_id, replaced)
        if _persistence_worker_task and not _persistence_worker_task.done():
            logger.info("persistence_batch_waiting rid=%s", request_id)
            return
        _persistence_worker_task = asyncio.create_task(_run_persistence_worker(store))


async def _wait_for_persistence_idle(timeout_seconds: float = 5.0) -> None:
    deadline = perf_counter() + timeout_seconds
    while perf_counter() < deadline:
        worker = _persistence_worker_task
        if (worker is None or worker.done()) and not _persistence_pending_by_symbol:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("Persistence worker did not drain pending batches in time")


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


def _response_snippet(response: httpx.Response | None, limit: int = 300) -> str:
    if response is None:
        return ""
    text = (response.text or "").replace("\r", " ").replace("\n", " ").strip()
    return text[:limit]


async def _request_with_retries(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str],
    retries: int = 2,
) -> httpx.Response | None:
    attempt = 0
    while True:
        request = client.build_request("GET", url, params=params)
        request_url = str(request.url)
        try:
            logger.info("HTTP price provider request method=GET url=%s attempt=%s", request_url, attempt + 1)
            response = await client.send(request)
            logger.info(
                "HTTP price provider response url=%s status=%s bytes=%s",
                request_url,
                response.status_code,
                len(response.content or b""),
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            attempt += 1
            status_code = exc.response.status_code if exc.response is not None else None
            body_snippet = _response_snippet(exc.response)
            if status_code == 429:
                if "query1.finance.yahoo.com" in request_url or "query2.finance.yahoo.com" in request_url:
                    symbol = request_url.rstrip("/").split("/")[-1].split("?", 1)[0]
                    _set_provider_cooldown(_yfinance_symbol_key(symbol), seconds=45)
                    _set_provider_cooldown("yfinance", seconds=8)
                logger.warning(
                    "Rate limit for price provider url=%s status=429 body_snippet=%r; enabling cooldown",
                    request_url,
                    body_snippet,
                )
                return None
            # 4xx is usually a permanent error (bad symbol/params), so retrying only adds latency.
            if status_code is not None and 400 <= status_code < 500:
                logger.warning(
                    "Price provider request failed url=%s status=%s body_snippet=%r error=%s",
                    request_url,
                    status_code,
                    body_snippet,
                    exc,
                )
                return None
            backoff = 0.4 * attempt
            if attempt > retries:
                logger.warning(
                    "Price provider request failed url=%s status=%s attempts=%s body_snippet=%r error=%s",
                    request_url,
                    status_code,
                    attempt,
                    body_snippet,
                    exc,
                )
                return None
            await asyncio.sleep(backoff)
        except httpx.RequestError as exc:
            attempt += 1
            backoff = 0.4 * attempt
            if attempt > retries:
                logger.warning(
                    "Price provider connection failed url=%s attempts=%s error_type=%s error=%s",
                    request_url,
                    attempt,
                    type(exc).__name__,
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
    if _provider_on_cooldown("yfinance") or _provider_on_cooldown(_yfinance_symbol_key(symbol)):
        return [], "rate_limited_cooldown"
    response = None
    last_error = "provider_error"
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{symbol}"
        response = await _request_with_retries(client, url, {"interval": "1d", "range": range_key}, retries=1)
        if response is not None:
            break
        if _provider_on_cooldown(_yfinance_symbol_key(symbol)):
            last_error = "rate_limited_cooldown"
    if response is None:
        return [], last_error
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
) -> tuple[tuple[float, float, str] | None, str | None]:
    points, error = await _fetch_yahoo_chart_rows(client, symbol, range_key="5d")
    if error is not None:
        return None, error
    if len(points) < 2:
        return None, "yfinance_no_data"
    latest_date, latest_close = points[-1]
    _prev_date, prev_close = points[-2]
    if prev_close == 0:
        return None, "yfinance_no_data"
    change_pct = ((latest_close - prev_close) / prev_close) * 100
    return (latest_close, round(change_pct, 2), latest_date), None


async def _fetch_yfinance_history(
    client: httpx.AsyncClient,
    symbol: str,
    timeout_seconds: float,
) -> list[HistoryPoint]:
    points, _error = await _fetch_yahoo_chart_rows(client, symbol, range_key="10y")
    return [HistoryPoint(date=date, value=value, change_pct=None) for date, value in points]


async def _fetch_frankfurter_latest(
    client: httpx.AsyncClient,
    pair_symbol: str,
) -> tuple[tuple[float, float, str] | None, str | None]:
    try:
        base, quote = [chunk.strip().upper() for chunk in pair_symbol.split("/", 1)]
    except ValueError:
        return None, "invalid_fx_symbol"

    latest_url = "https://api.frankfurter.app/latest"
    latest_resp = await _request_with_retries(client, latest_url, {"from": base, "to": quote}, retries=1)
    if latest_resp is None:
        return None, "frankfurter_request_failed"
    try:
        latest_payload = latest_resp.json()
        latest_date = str(latest_payload.get("date") or "")
        latest_rate = float((latest_payload.get("rates") or {}).get(quote))
    except Exception:
        return None, "frankfurter_parse_error"
    if not latest_date:
        return None, "frankfurter_no_data"

    latest_dt = datetime.strptime(latest_date, "%Y-%m-%d")
    prev_rate: float | None = None
    for shift in range(1, 5):
        prev_dt = latest_dt - timedelta(days=shift)
        prev_resp = await _request_with_retries(
            client,
            f"https://api.frankfurter.app/{prev_dt.strftime('%Y-%m-%d')}",
            {"from": base, "to": quote},
            retries=0,
        )
        if prev_resp is None:
            continue
        try:
            prev_payload = prev_resp.json()
            prev_rate = float((prev_payload.get("rates") or {}).get(quote))
        except Exception:
            continue
        if prev_rate:
            break
    if prev_rate in {None, 0.0}:
        return (latest_rate, 0.0, latest_date), None
    change_pct = ((latest_rate - prev_rate) / prev_rate) * 100
    return (latest_rate, round(change_pct, 4), latest_date), None


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
    planned_chain: list[str] | None = None,
    provider_loop_started: bool | None = None,
    deadline_remaining_ms: float | None = None,
    no_attempts_reason: str | None = None,
) -> None:
    logger.info(
        "price_fetch rid=%s ticker=%s symbol=%s provider=%s status=%s source=%s latency_ms=%.1f error_reason=%s planned_chain=%s attempted_chain=%s provider_loop_started=%s deadline_remaining_ms=%.1f no_attempts_reason=%s",
        _price_request_id.get(),
        config.id,
        config.symbol,
        provider or "none",
        status,
        source or "none",
        latency_ms,
        error_reason or "none",
        ",".join(planned_chain or []),
        ",".join(fallback_chain or []),
        provider_loop_started,
        deadline_remaining_ms or 0.0,
        no_attempts_reason or "none",
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
        started = perf_counter()
        _log_attempt("ticker=%s history_attempt provider=%s symbol=%s", config.id, attempt.provider, attempt.symbol)
        if attempt.provider == "stooq":
            fresh_points = await _fetch_stooq_history(client, attempt.symbol)
        elif attempt.provider == "yfinance":
            fresh_points = await _fetch_yfinance_history(client, attempt.symbol, timeout_seconds=timeout_seconds)
        else:
            fresh_points = []
        latency_ms = (perf_counter() - started) * 1000
        if fresh_points:
            _log_attempt(
                "ticker=%s history_result=SUCCESS provider=%s symbol=%s points=%s latency_ms=%.1f",
                config.id,
                attempt.provider,
                attempt.symbol,
                len(fresh_points),
                latency_ms,
            )
            return fresh_points
        _log_attempt(
            "ticker=%s history_result=ERROR provider=%s symbol=%s latency_ms=%.1f",
            config.id,
            attempt.provider,
            attempt.symbol,
            latency_ms,
        )
    return points


async def _get_refresh_history_points(store: PriceHistoryStore, ticker_id: str) -> list[HistoryPoint]:
    try:
        return await store.get_history(ticker_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("History read failed during refresh for %s: %s", ticker_id, exc)
        return []


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
        cached["provider_loop_started"] = False
        cached["no_attempts_reason"] = "FRESH_CACHE_HIT"
        return cached

    plan = get_provider_plan(config.id)
    if not plan:
        return {
            "status": "error",
            "error_reason": "no_sources_configured",
            "tried_sources": [],
            "provider_loop_started": False,
            "no_attempts_reason": "NO_PROVIDER_PLAN",
        }
    tried_sources: list[str] = []
    error_reason: str | None = None
    first_attempt_started_at: str | None = None
    for attempt in plan.latest_chain:
        if first_attempt_started_at is None:
            first_attempt_started_at = datetime.utcnow().isoformat()
        started = perf_counter()
        tried_sources.append(f"{attempt.provider}:{attempt.symbol}")
        latest: tuple[float, float, str] | None = None
        current_error: str | None = None
        _log_attempt("ticker=%s Attempting provider=%s symbol=%s", config.id, attempt.provider, attempt.symbol)
        if attempt.provider == "stooq":
            latest, current_error = await _fetch_stooq_latest(client, attempt.symbol)
        elif attempt.provider == "frankfurter":
            latest, current_error = await _fetch_frankfurter_latest(client, attempt.symbol)
        elif attempt.provider == "yfinance":
            latest, current_error = await _fetch_yfinance_latest(client, attempt.symbol, timeout_seconds=timeout_seconds)
            if latest is None and current_error is None:
                current_error = "yfinance_failed"
        latency_ms = (perf_counter() - started) * 1000
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
                "first_attempt_started_at": first_attempt_started_at,
            }
            _log_attempt(
                "ticker=%s Result=SUCCESS provider=%s symbol=%s latency_ms=%.1f",
                config.id,
                attempt.provider,
                attempt.symbol,
                latency_ms,
            )
            return payload
        _log_attempt(
            "ticker=%s Result=ERROR provider=%s symbol=%s latency_ms=%.1f error_type=%s",
            config.id,
            attempt.provider,
            attempt.symbol,
            latency_ms,
            current_error or "provider_error",
        )
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
        cached["provider_loop_started"] = bool(tried_sources)
        cached["first_attempt_started_at"] = first_attempt_started_at
        if not tried_sources:
            cached["no_attempts_reason"] = "fallback_db_triggered_before_live_attempts"
        return cached
    return {
        "status": "error",
        "error_reason": error_reason,
        "tried_sources": tried_sources,
        "provider_loop_started": bool(tried_sources),
        "first_attempt_started_at": first_attempt_started_at,
        "no_attempts_reason": "no_attempts_executed" if not tried_sources else None,
    }


async def get_prices_payload(*, bypass_cache: bool = False, request_id: str | None = None) -> PricesResponse:
    global _prices_refresh_task
    settings = get_settings()
    request_token = _price_request_id.set(request_id or datetime.utcnow().strftime("%H%M%S%f"))
    debug_token = _price_debug_enabled.set(settings.debug_price_fetch)
    if not bypass_cache:
        now = datetime.utcnow()
        async with _prices_cache_lock:
            cached_payload = _prices_cache.get("payload")
            cached_at = _prices_cache.get("fetched_at")
            cache_ttl = timedelta(seconds=settings.cache_ttl_prices)
            if isinstance(cached_at, datetime) and cached_payload and now - cached_at < cache_ttl:
                logger.info("Prices cache hit (age=%.1fs)", (now - cached_at).total_seconds())
                _price_request_id.reset(request_token)
                _price_debug_enabled.reset(debug_token)
                return cached_payload

    async with _prices_refresh_lock:
        global _prices_refresh_task
        created = False
        if _prices_refresh_task is None or _prices_refresh_task.done():
            _prices_refresh_task = asyncio.create_task(_refresh_prices_payload(bypass_cache=bypass_cache))
            global _inflight_task_created_at, _inflight_owner_rid
            _inflight_task_created_at = datetime.utcnow()
            _inflight_owner_rid = _price_request_id.get()
            logger.info("inflight_task_created rid=%s created_at=%s", _price_request_id.get(), _inflight_task_created_at.isoformat())
            created = True
        refresh_task = _prices_refresh_task
        if not created:
            logger.info(
                "inflight_task_reused inflight_owner_rid=%s inflight_waiter_rid=%s done=%s cancelled=%s",
                _inflight_owner_rid or "none",
                _price_request_id.get(),
                refresh_task.done(),
                refresh_task.cancelled(),
            )

    def _read_fresh_done_task(task: asyncio.Task) -> PricesResponse | None:
        if not task.done() or task.cancelled():
            return None
        exc = task.exception()
        if exc is not None:
            raise exc
        return task.result()

    waiter_started = perf_counter()
    waiter_started_at = datetime.utcnow()
    owner_refresh_budget_seconds = max(4.0, min(settings.price_fetch_timeout_seconds, 1.8) * 3)
    refresh_age_seconds = (
        (waiter_started_at - _inflight_task_created_at).total_seconds()
        if _inflight_task_created_at is not None
        else 0.0
    )
    owner_refresh_deadline_ms = max(0.0, (owner_refresh_budget_seconds - refresh_age_seconds) * 1000)
    follower_timeout_budget_seconds = SHARED_WAITER_TIMEOUT_SECONDS
    if not created:
        follower_timeout_budget_seconds = max(
            SHARED_WAITER_TIMEOUT_SECONDS,
            max(0.0, owner_refresh_budget_seconds - refresh_age_seconds) + 0.35,
        )
    waiter_deadline = waiter_started + follower_timeout_budget_seconds
    waiter_seen_snapshot_version = _cache_snapshot_version
    if created:
        logger.info(
            "owner_path_entered rid=%s inflight_owner_rid=%s wait_started_on_task_done=%s cache_snapshot_version=%s follower_wait_started_at=%s refresh_age_ms=%.1f owner_refresh_deadline_ms=%.1f",
            _price_request_id.get(),
            _inflight_owner_rid or "none",
            refresh_task.done(),
            waiter_seen_snapshot_version,
            waiter_started_at.isoformat(),
            refresh_age_seconds * 1000,
            owner_refresh_deadline_ms,
        )
    else:
        logger.info(
            "follower_path_entered rid=%s inflight_owner_rid=%s waiter_deadline_at=%.6f wait_started_on_task_done=%s cache_snapshot_version=%s follower_wait_started_at=%s follower_timeout_budget_ms=%.1f refresh_age_ms=%.1f owner_refresh_deadline_ms=%.1f",
            _price_request_id.get(),
            _inflight_owner_rid or "none",
            waiter_deadline,
            refresh_task.done(),
            waiter_seen_snapshot_version,
            waiter_started_at.isoformat(),
            follower_timeout_budget_seconds * 1000,
            refresh_age_seconds * 1000,
            owner_refresh_deadline_ms,
        )
    try:
        payload = _read_fresh_done_task(refresh_task)
        if payload is not None:
            logger.info(
                "%s rid=%s inflight_owner_rid=%s",
                "owner_path_returning_fresh" if created else "follower_path_returning_fresh",
                _price_request_id.get(),
                _inflight_owner_rid or "none",
            )
        else:
            if created:
                payload = await asyncio.shield(refresh_task)
                logger.info(
                    "owner_path_returning_fresh rid=%s inflight_owner_rid=%s",
                    _price_request_id.get(),
                    _inflight_owner_rid or "none",
                )
            else:
                remaining = max(0.0, waiter_deadline - perf_counter())
                payload = await asyncio.wait_for(asyncio.shield(refresh_task), timeout=remaining)
                logger.info(
                    "follower_path_returning_fresh rid=%s inflight_owner_rid=%s",
                    _price_request_id.get(),
                    _inflight_owner_rid or "none",
                )
        if payload is None:
            logger.error("Prices refresh returned None; using controlled snapshot")
            payload = await _build_prices_snapshot(error_reason="refresh_none")
            if payload is None:
                return await _build_empty_prices_response("refresh_none_no_snapshot")
        return payload
    except asyncio.TimeoutError:
        waiter_elapsed_ms = (perf_counter() - waiter_started) * 1000
        if _price_request_id.get() == (_inflight_owner_rid or "") and _inflight_task_result_published_at is not None:
            logger.error(
                "invariant_violation_owner_timeout_after_publish rid=%s published_at=%s",
                _price_request_id.get(),
                _inflight_task_result_published_at.isoformat(),
            )
        logger.warning(
            "timeout_branch_entered rid=%s inflight_owner_rid=%s waiter_timeout_ms=%.1f follower_timeout_budget_ms=%.1f refresh_age_ms=%.1f owner_refresh_deadline_ms=%.1f",
            _price_request_id.get(),
            _inflight_owner_rid or "none",
            waiter_elapsed_ms,
            follower_timeout_budget_seconds * 1000,
            refresh_age_seconds * 1000,
            owner_refresh_deadline_ms,
        )
        try:
            payload = _read_fresh_done_task(refresh_task)
        except Exception as exc:  # noqa: BLE001
            logger.warning("inflight_task_done_after_timeout but result failed: %s", exc)
        else:
            if payload is not None:
                logger.info(
                    "timeout_branch_skipped_due_to_task_done rid=%s inflight_owner_rid=%s waiter_timeout_ms=%.1f task_done_at=%s task_result_read_at=%s",
                    _price_request_id.get(),
                    _inflight_owner_rid or "none",
                    waiter_elapsed_ms,
                    _inflight_task_done_at.isoformat() if _inflight_task_done_at else "none",
                    datetime.utcnow().isoformat(),
                )
                logger.info(
                    "follower_path_returning_fresh rid=%s inflight_owner_rid=%s",
                    _price_request_id.get(),
                    _inflight_owner_rid or "none",
                )
                return payload
        async with _prices_cache_lock:
            cached_payload = _prices_cache.get("payload")
            cached_at = _prices_cache.get("fetched_at")
            snapshot_is_fresh = _cache_snapshot_version > waiter_seen_snapshot_version
            if cached_payload and isinstance(cached_at, datetime) and snapshot_is_fresh:
                logger.info(
                    "follower_received_fresh_snapshot=true rid=%s snapshot_written_at=%s waiter_timeout_ms=%.1f snapshot_is_fresh=%s cache_snapshot_version=%s",
                    _price_request_id.get(),
                    cached_at.isoformat(),
                    waiter_elapsed_ms,
                    snapshot_is_fresh,
                    _cache_snapshot_version,
                )
                return cached_payload
        snapshot = await _build_prices_snapshot(error_reason="refresh_timeout")
        if snapshot is not None:
            owner_succeeded_shortly_after = False
            if _inflight_task_done_at is not None:
                owner_succeeded_shortly_after = (
                    (_inflight_task_done_at - waiter_started_at).total_seconds() * 1000
                ) <= (waiter_elapsed_ms + 1200.0)
            logger.warning(
                "follower_returning_stale rid=%s inflight_owner_rid=%s inflight_waiter_rid=%s waiter_timeout_ms=%.1f stale_served_reason=refresh_timeout stale_snapshot_used=true owner_succeeded_shortly_after_timeout=%s",
                _price_request_id.get(),
                _inflight_owner_rid or "none",
                _price_request_id.get(),
                waiter_elapsed_ms,
                owner_succeeded_shortly_after,
            )
            return snapshot
        logger.error("Shared prices refresh timed out and no snapshot available")
        return await _build_empty_prices_response("refresh_timeout_no_snapshot")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Shared prices refresh failed: %s", exc)
        async with _prices_refresh_lock:
            if _prices_refresh_task is refresh_task:
                _prices_refresh_task = None
        snapshot = await _build_prices_snapshot(error_reason="refresh_failed")
        if snapshot is not None:
            return snapshot
        return await _build_empty_prices_response("refresh_failed_no_snapshot")
    finally:
        _price_request_id.reset(request_token)
        _price_debug_enabled.reset(debug_token)


async def _refresh_prices_payload(*, bypass_cache: bool = False) -> PricesResponse:
    store = await _get_store()
    configs = list(PRICE_TICKERS)
    now = datetime.utcnow()
    start_time = datetime.utcnow()
    settings = get_settings()
    effective_timeout = min(settings.price_fetch_timeout_seconds, 1.8)
    cache_hit = False
    logger.info("Prices cache miss (bypass=%s)", bypass_cache)
    refresh_started_at = datetime.utcnow()
    spot_fetch_phase_started_at = datetime.utcnow()

    errors: dict[str, str] = {}
    timed_out = False
    refresh_deadline = perf_counter() + max(4.0, effective_timeout * 3)
    effective_spot_concurrency = max(settings.price_fetch_concurrency, min(len(configs), 8))
    _log_attempt(
        "spot_fetch_phase_started_at=%s configured_concurrency=%s effective_spot_concurrency=%s tickers=%s",
        spot_fetch_phase_started_at.isoformat(),
        settings.price_fetch_concurrency,
        effective_spot_concurrency,
        len(configs),
    )
    http_timeout = httpx.Timeout(
        timeout=effective_timeout,
        connect=min(1.0, effective_timeout),
    )
    pre_scheduling_started_at = datetime.utcnow()
    client = _get_shared_http_client(http_timeout)
    pre_scheduling_ready_at = datetime.utcnow()
    _log_attempt(
        "pre_scheduling_started_at=%s pre_scheduling_ready_at=%s pre_scheduling_duration_ms=%.1f",
        pre_scheduling_started_at.isoformat(),
        pre_scheduling_ready_at.isoformat(),
        (pre_scheduling_ready_at - pre_scheduling_started_at).total_seconds() * 1000,
    )
    semaphore = asyncio.Semaphore(effective_spot_concurrency)
    ticker_by_id: dict[str, dict] = {}
    all_spot_tasks_scheduled_at: datetime | None = None

    async def run_for_ticker(config: PriceConfig, order: int) -> tuple[str, dict]:
        started = perf_counter()
        scheduled_at = datetime.utcnow()
        scheduled_at_iso = scheduled_at.isoformat()
        deadline_remaining_ms = max(0.0, (refresh_deadline - started) * 1000)
        _log_attempt(
            "ticker=%s scheduler_start_order=%s scheduled_at=%s deadline_remaining_ms=%.1f",
            config.id,
            order,
            scheduled_at_iso,
            deadline_remaining_ms,
        )
        try:
            if deadline_remaining_ms <= 1:
                ticker = await _build_cached_payload(
                    config,
                    store,
                    error="deadline_exhausted",
                    allow_seed=settings.allow_seed_prices,
                )
                ticker["provider_loop_started"] = False
                ticker["no_attempts_reason"] = "global_deadline_exceeded_before_attempts"
                ticker["cancellation_reason"] = "global_deadline_exceeded_before_attempts"
                ticker["deadline_remaining_ms"] = 0.0
                return config.id, ticker
            ticker = await _build_ticker_payload(
                config,
                store,
                client,
                semaphore,
                timeout_seconds=effective_timeout,
                allow_seed=settings.allow_seed_prices,
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
            ticker["provider_loop_started"] = False
            ticker["no_attempts_reason"] = "cancelled_before_provider_loop"
            ticker["cancellation_reason"] = "ticker_timeout_before_first_attempt"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ticker fetch failed for %s: %s", config.id, exc)
            ticker = await _build_cached_payload(
                config,
                store,
                error=f"provider_error:{exc}",
                allow_seed=settings.allow_seed_prices,
            )
            ticker["provider_loop_started"] = False
            ticker["no_attempts_reason"] = "provider_tasks_not_awaited"
            ticker["cancellation_reason"] = "provider_task_exception"
        latency_ms = (perf_counter() - started) * 1000
        ticker["fetch_latency_ms"] = round(latency_ms, 2)
        age_reference = ticker.get("as_of") if ticker.get("status") == "live" else ticker.get("last_updated")
        ticker["age_seconds"] = _age_seconds(age_reference)
        ticker["freshness_seconds"] = int(LATEST_TTL.total_seconds())
        ticker["deadline_remaining_ms"] = round(max(0.0, (refresh_deadline - perf_counter()) * 1000), 2)
        ticker["scheduled_at"] = scheduled_at_iso
        if "provider_loop_started" not in ticker:
            ticker["provider_loop_started"] = bool(ticker.get("tried_sources"))
        if not ticker.get("tried_sources") and not ticker.get("no_attempts_reason"):
            ticker["no_attempts_reason"] = "no_attempts_executed"
        if ticker.get("provider_loop_started") and not ticker.get("tried_sources"):
            logger.warning(
                "ticker=%s provider_loop_started=true but attempted_chain is empty; resetting flag",
                config.id,
            )
            ticker["provider_loop_started"] = False
            ticker["no_attempts_reason"] = ticker.get("no_attempts_reason") or "provider_loop_flag_without_attempts"
        if (not ticker.get("provider_loop_started")) and ticker.get("tried_sources"):
            ticker["provider_loop_started"] = True
        first_attempt_at = _parse_datetime_flexible(ticker.get("first_attempt_started_at"))
        delay_before_first_attempt_ms: float | None = None
        if first_attempt_at is not None:
            delay_before_first_attempt_ms = max(0.0, (first_attempt_at - scheduled_at).total_seconds() * 1000)
        ticker["delay_before_first_attempt_ms"] = round(delay_before_first_attempt_ms, 2) if delay_before_first_attempt_ms is not None else None
        plan = get_provider_plan(config.id)
        planned_chain = [
            f"{item.provider}:{item.symbol}"
            for item in (plan.latest_chain if plan else ())
        ]
        _log_ticker_result(
            config=config,
            provider=ticker.get("provider") or ticker.get("source"),
            status=str(ticker.get("status") or "unknown"),
            source=ticker.get("source"),
            latency_ms=latency_ms,
            error_reason=ticker.get("error_reason") or ticker.get("error"),
            fallback_chain=list(ticker.get("tried_sources") or []),
            planned_chain=planned_chain,
            provider_loop_started=ticker.get("provider_loop_started"),
            deadline_remaining_ms=ticker.get("deadline_remaining_ms"),
            no_attempts_reason=ticker.get("no_attempts_reason"),
        )
        _log_attempt(
            "ticker=%s provider_loop_started=%s first_attempt_started_at=%s delay_before_first_attempt_ms=%s first_provider=%s final_latency_ms=%.1f deadline_remaining_ms=%.1f no_attempts_reason=%s cancellation_reason=%s",
            config.id,
            ticker.get("provider_loop_started"),
            ticker.get("first_attempt_started_at") or "none",
            ticker.get("delay_before_first_attempt_ms"),
            (ticker.get("tried_sources") or ["none"])[0],
            latency_ms,
            ticker.get("deadline_remaining_ms") or 0.0,
            ticker.get("no_attempts_reason"),
            ticker.get("cancellation_reason") or "none",
        )
        return config.id, ticker

    tasks = [asyncio.create_task(run_for_ticker(config, idx)) for idx, config in enumerate(configs)]
    all_spot_tasks_scheduled_at = datetime.utcnow()
    _log_attempt(
        "all_spot_tasks_scheduled_at=%s pre_scheduling_duration_ms=%.1f",
        all_spot_tasks_scheduled_at.isoformat(),
        (all_spot_tasks_scheduled_at - pre_scheduling_started_at).total_seconds() * 1000,
    )
    results = await asyncio.gather(*tasks, return_exceptions=True)
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

    spot_fetch_phase_completed_at = datetime.utcnow()
    _log_attempt(
        "spot_fetch_phase_completed_at=%s phase_duration_ms=%.1f",
        spot_fetch_phase_completed_at.isoformat(),
        (spot_fetch_phase_completed_at - spot_fetch_phase_started_at).total_seconds() * 1000,
    )
    tickers = [_sanitize_ticker_numeric_fields(ticker_by_id.get(config.id)) for config in configs if ticker_by_id.get(config.id)]
    live_tickers_for_persistence = [
        ticker for ticker in tickers
        if ticker.get("status") == "live" and isinstance(ticker.get("value"), (int, float))
    ]
    payload_built_at = datetime.utcnow()
    _log_attempt("payload_built_at=%s", payload_built_at.isoformat())
    await _schedule_persistence(store, live_tickers_for_persistence, _price_request_id.get())
    _log_attempt("persistence_enqueued_at=%s", datetime.utcnow().isoformat())
    for ticker in tickers:
        if ticker.get("error") or ticker.get("status") in {"error", "empty", "unsupported"}:
            errors[str(ticker.get("id"))] = str(ticker.get("error") or ticker.get("error_reason") or "error")

    summary = _build_prices_summary(tickers)
    summary["persistence_errors"] = 0
    summary["persistence_queued"] = len(live_tickers_for_persistence)
    response = PricesResponse(
        as_of=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        tickers=tickers,
        errors=errors,
        timed_out=timed_out,
        cache_bypassed=bypass_cache,
        summary=summary,
    )
    duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
    logger.info(
        "Prices fetch completed in %.1fms (tickers=%s, ok_live=%s, ok_cached=%s, stale_db=%s, empty=%s, error=%s, provider_attempt_errors=%s, timed_out=%s, cache_hit=%s, refresh_started_at=%s, spot_fetch_phase_started_at=%s, all_spot_tasks_scheduled_at=%s, spot_fetch_phase_completed_at=%s, payload_built_at=%s)",
        duration_ms,
        len(tickers),
        summary.get("ok_live", 0),
        summary.get("ok_cached", 0),
        summary.get("stale_db", 0),
        summary.get("empty", 0),
        summary.get("error", 0),
        summary.get("provider_attempt_errors", 0),
        timed_out,
        cache_hit,
        refresh_started_at.isoformat(),
        spot_fetch_phase_started_at.isoformat(),
        all_spot_tasks_scheduled_at.isoformat() if all_spot_tasks_scheduled_at else "none",
        spot_fetch_phase_completed_at.isoformat(),
        payload_built_at.isoformat(),
    )
    async with _prices_cache_lock:
        if not bypass_cache:
            _prices_cache["payload"] = response
            _prices_cache["fetched_at"] = now
            global _snapshot_written_at, _inflight_task_result_published_at, _inflight_task_done_at, _cache_snapshot_version
            _snapshot_written_at = datetime.utcnow()
            _inflight_task_result_published_at = datetime.utcnow()
            _inflight_task_done_at = datetime.utcnow()
            _cache_snapshot_version += 1
            logger.info(
                "inflight_task_result_published_at=%s snapshot_written_at=%s cache_snapshot_version=%s",
                _inflight_task_result_published_at.isoformat(),
                _snapshot_written_at.isoformat(),
                _cache_snapshot_version,
            )
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
        history = await _get_refresh_history_points(store, config.id)
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
        normalized = normalize_price_ticker(
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
        normalized["provider_loop_started"] = (latest or {}).get("provider_loop_started", bool(tried_sources))
        normalized["no_attempts_reason"] = (latest or {}).get("no_attempts_reason")
        normalized["first_attempt_started_at"] = (latest or {}).get("first_attempt_started_at")
        return normalized
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
            payload = normalize_price_ticker(
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
            payload["provider_loop_started"] = False
            payload["no_attempts_reason"] = "early_return_to_stale_snapshot"
            return payload
        payload = normalize_price_ticker(
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
        payload["provider_loop_started"] = False
        payload["no_attempts_reason"] = "no_attempts_executed"
        return payload
    payload = normalize_price_ticker(
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
    payload["provider_loop_started"] = False
    payload["no_attempts_reason"] = "fallback_db_triggered_before_live_attempts"
    return payload


async def _build_prices_snapshot(error_reason: str | None = None) -> PricesResponse:
    store = await _get_store()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    settings = get_settings()
    tickers: list[dict] = []
    errors: dict[str, str] = {}
    for config in PRICE_TICKERS:
        ticker = await _build_cached_payload(
            config,
            store,
            error=error_reason,
            allow_seed=settings.allow_seed_prices,
        )
        if error_reason and ticker.get("status") == "empty":
            ticker["error_reason"] = error_reason
            ticker["error"] = error_reason
        tickers.append(ticker)
        if ticker.get("status") in {"error", "empty", "unsupported"}:
            errors[str(ticker.get("id"))] = str(ticker.get("error_reason") or ticker.get("error") or "error")
    summary = _build_prices_summary(tickers)
    return PricesResponse(
        as_of=now,
        tickers=tickers,
        errors=errors,
        timed_out=True if error_reason else False,
        cache_bypassed=False,
        summary=summary,
    )


async def _build_empty_prices_response(reason: str) -> PricesResponse:
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    tickers = []
    for config in PRICE_TICKERS:
        tickers.append(
            normalize_price_ticker(
                {
                    "id": config.id,
                    "symbol": config.symbol,
                    "name": config.name,
                    "asset_class": config.asset_class,
                    "unit": config.unit,
                    "value": "N/A",
                    "last_updated": "N/A",
                    "history_points": [],
                    "history_meta": _history_meta([]),
                },
                now=datetime.utcnow(),
                source=None,
                provider=None,
                status="empty",
                error=reason,
                error_reason=reason,
                tried_sources=[],
            )
        )
    return PricesResponse(
        as_of=now,
        tickers=tickers,
        errors={t["id"]: reason for t in tickers},
        timed_out=True,
        summary=_build_prices_summary(tickers),
    )


async def get_price_history_payload(symbol: str, range_key: str) -> PriceHistoryResponse:
    config = resolve_price_config(symbol)
    if not config:
        raise ValueError(f"Unknown symbol: {symbol}")

    store = await _get_store()
    settings = get_settings()
    timeout_seconds = min(settings.price_fetch_timeout_seconds, 6.0)
    async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=False) as client:
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
