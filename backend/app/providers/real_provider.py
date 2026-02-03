from __future__ import annotations

import csv
import logging
from datetime import datetime
from io import StringIO
from typing import Any, Iterable

import httpx

from ..models.schemas import MacroResponse, PricesResponse
from ..utils.database import MarketDataStore
from ..utils.settings import Settings, get_settings
from .base import MarketDataProvider

logger = logging.getLogger(__name__)


class RealMarketDataProvider(MarketDataProvider):
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.store = MarketDataStore(self.settings.resolved_database_path())
        self.timeout = self.settings.request_timeout

    def get_prices(self) -> PricesResponse:
        price_rows: list[dict[str, Any]] = []
        as_of = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        for config in _PRICE_SOURCES:
            symbol = config["symbol"]
            label = config["label"]
            source = None
            data = _fetch_stooq_price(symbol, self.timeout)
            if data:
                price, change_pct, updated = data
                source = "stooq"
            else:
                data = _fetch_yfinance_price(config["yfinance"], self.timeout)
                if data:
                    price, change_pct, updated = data
                    source = "yfinance"
                else:
                    cached = _get_cached_price(self.store, label)
                    if cached:
                        price, change_pct, updated, source = cached
                    else:
                        logger.warning("No price data for %s", label)
                        continue

            as_of = max(as_of, updated)
            price_rows.append(
                {
                    "symbol": label,
                    "price": price,
                    "change_pct": change_pct,
                    "as_of": updated,
                    "source": source,
                }
            )

        if price_rows:
            self.store.upsert_prices(price_rows)

        return PricesResponse(
            as_of=as_of,
            tickers=[
                {
                    "symbol": row["symbol"],
                    "price": row["price"],
                    "change_pct": row["change_pct"],
                }
                for row in price_rows
            ],
        )

    def get_macro(self) -> MacroResponse:
        macro_rows: list[dict[str, str]] = []
        as_of = datetime.utcnow().strftime("%Y-%m-%d")

        for config in _MACRO_SOURCES:
            name = config["name"]
            value = None
            change = None
            updated = None
            source = None

            if config.get("bea") and self.settings.bea_api_key:
                bea_result = _fetch_bea_series(
                    api_key=self.settings.bea_api_key,
                    dataset=config["bea"]["dataset"],
                    table_name=config["bea"]["table"],
                    line_number=config["bea"]["line"],
                    frequency=config["bea"]["frequency"],
                    timeout=self.timeout,
                )
                if bea_result:
                    value, change, updated = bea_result
                    source = "bea"

            if value is None and config.get("fred"):
                if not self.settings.fred_api_key:
                    logger.info("FRED API key not set; skipping %s", name)
                else:
                    fred_result = _fetch_fred_series(
                        api_key=self.settings.fred_api_key,
                        series_id=config["fred"],
                        timeout=self.timeout,
                        unit=config.get("unit"),
                    )
                    if fred_result:
                        value, change, updated = fred_result
                        source = "fred"

            if value is None:
                cached = _get_cached_macro(self.store, name)
                if cached:
                    value, change, updated, source = cached
                else:
                    logger.warning("No macro data for %s", name)
                    continue

            as_of = max(as_of, updated)
            macro_rows.append(
                {
                    "name": name,
                    "value": value,
                    "change": change,
                    "updated": updated,
                    "as_of": as_of,
                    "source": source,
                }
            )

        if macro_rows:
            self.store.upsert_macro(macro_rows)

        return MacroResponse(
            as_of=as_of,
            series=[
                {
                    "name": row["name"],
                    "value": row["value"],
                    "change": row["change"],
                    "updated": row["updated"],
                }
                for row in macro_rows
            ],
            commentary="Real-time macro feeds with local caching.",
        )


def _fetch_stooq_price(symbol: str, timeout: float) -> tuple[float, float, str] | None:
    url = "https://stooq.com/q/d/l/"
    params = {"s": symbol, "i": "d"}
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("Stooq request failed for %s: %s", symbol, exc)
        return None

    reader = csv.DictReader(StringIO(response.text))
    rows = list(reader)
    if len(rows) < 2:
        return None

    latest = rows[-1]
    previous = rows[-2]
    try:
        latest_close = float(latest["Close"])
        previous_close = float(previous["Close"])
    except (KeyError, ValueError):
        return None

    change_pct = ((latest_close - previous_close) / previous_close) * 100
    return latest_close, round(change_pct, 2), latest["Date"]


def _fetch_yfinance_price(symbol: str, timeout: float) -> tuple[float, float, str] | None:
    try:
        import yfinance as yf
    except ImportError:
        logger.info("yfinance not installed; skipping %s", symbol)
        return None

    try:
        ticker = yf.Ticker(symbol)
        history = ticker.history(period="5d", interval="1d", timeout=timeout)
    except Exception as exc:
        logger.info("yfinance failed for %s: %s", symbol, exc)
        return None

    if history.empty or len(history) < 2:
        return None

    latest = history.iloc[-1]
    previous = history.iloc[-2]
    latest_close = float(latest["Close"])
    previous_close = float(previous["Close"])
    change_pct = ((latest_close - previous_close) / previous_close) * 100
    updated = latest.name.strftime("%Y-%m-%d")
    return latest_close, round(change_pct, 2), updated


def _fetch_fred_series(
    api_key: str, series_id: str, timeout: float, unit: str | None = None
) -> tuple[str, str, str] | None:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "api_key": api_key,
        "series_id": series_id,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 2,
    }
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("FRED request failed for %s: %s", series_id, exc)
        return None

    payload = response.json()
    observations = payload.get("observations", [])
    if len(observations) < 2:
        return None

    latest = observations[0]
    previous = observations[1]
    try:
        latest_value = float(latest["value"])
        previous_value = float(previous["value"])
    except (KeyError, ValueError):
        return None

    change = latest_value - previous_value
    formatted_change = _format_change(change, unit)
    formatted_value = _format_value(latest_value, unit)
    return formatted_value, formatted_change, latest["date"]


def _fetch_bea_series(
    api_key: str,
    dataset: str,
    table_name: str,
    line_number: str,
    frequency: str,
    timeout: float,
) -> tuple[str, str, str] | None:
    url = "https://apps.bea.gov/api/data"
    params = {
        "UserID": api_key,
        "method": "GetData",
        "DatasetName": dataset,
        "TableName": table_name,
        "LineNumber": line_number,
        "Frequency": frequency,
        "ResultFormat": "JSON",
    }
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("BEA request failed: %s", exc)
        return None

    payload = response.json()
    data = (
        payload.get("BEAAPI", {})
        .get("Results", {})
        .get("Data", [])
    )
    if len(data) < 2:
        return None

    latest = data[0]
    previous = data[1]
    try:
        latest_value = float(latest["DataValue"].replace(",", ""))
        previous_value = float(previous["DataValue"].replace(",", ""))
    except (KeyError, ValueError):
        return None

    change = latest_value - previous_value
    formatted_change = _format_change(change, None)
    formatted_value = _format_value(latest_value, None)
    return formatted_value, formatted_change, latest["TimePeriod"]


def _format_change(change: float, unit: str | None) -> str:
    suffix = _unit_suffix(unit)
    return f"{change:+.2f}{suffix}"


def _format_value(value: float, unit: str | None) -> str:
    suffix = _unit_suffix(unit)
    if unit == "percent":
        return f"{value:.2f}%"
    if unit == "basis_points":
        return f"{value:.2f}%"
    return f"{value:.2f}{suffix}"


def _unit_suffix(unit: str | None) -> str:
    if unit == "percent":
        return "pp"
    if unit == "basis_points":
        return "bp"
    if unit == "thousands":
        return "k"
    return ""


def _get_cached_price(
    store: MarketDataStore, label: str
) -> tuple[float, float, str, str] | None:
    rows = store.load_prices()
    for row in rows:
        if row["symbol"] == label:
            return row["price"], row["change_pct"], row["as_of"], row["source"]
    return None


def _get_cached_macro(
    store: MarketDataStore, name: str
) -> tuple[str, str, str, str] | None:
    rows = store.load_macro()
    for row in rows:
        if row["name"] == name:
            return row["value"], row["change"], row["updated"], row["source"]
    return None


_PRICE_SOURCES: Iterable[dict[str, str]] = (
    {"label": "S&P500", "symbol": "spx", "yfinance": "^GSPC"},
    {"label": "NAS100", "symbol": "ndx", "yfinance": "^NDX"},
    {"label": "NASDAQ mini", "symbol": "nq.f", "yfinance": "NQ=F"},
    {"label": "GBP/USD", "symbol": "gbpusd", "yfinance": "GBPUSD=X"},
    {"label": "EUR/USD", "symbol": "eurusd", "yfinance": "EURUSD=X"},
    {"label": "GBP/JPY", "symbol": "gbpjpy", "yfinance": "GBPJPY=X"},
    {"label": "XAUUSD", "symbol": "xauusd", "yfinance": "XAUUSD=X"},
)

_MACRO_SOURCES: Iterable[dict[str, Any]] = (
    {
        "name": "CPI",
        "fred": "CPIAUCSL",
        "unit": "percent",
    },
    {
        "name": "Core CPI",
        "fred": "CPILFESL",
        "unit": "percent",
    },
    {
        "name": "PCE",
        "bea": {
            "dataset": "NIPA",
            "table": "T20804",
            "line": "1",
            "frequency": "Q",
        },
        "fred": "PCEPI",
        "unit": "percent",
    },
    {
        "name": "PMI/ISM",
        "fred": "NAPM",
        "unit": None,
    },
    {
        "name": "NFP",
        "fred": "PAYEMS",
        "unit": "thousands",
    },
    {
        "name": "Unemployment Rate",
        "fred": "UNRATE",
        "unit": "percent",
    },
    {
        "name": "Jobless Claims",
        "fred": "ICSA",
        "unit": "thousands",
    },
    {
        "name": "Fed Funds Rate",
        "fred": "DFF",
        "unit": "basis_points",
    },
    {
        "name": "US10Y",
        "fred": "DGS10",
        "unit": "basis_points",
    },
    {
        "name": "US2Y",
        "fred": "DGS2",
        "unit": "basis_points",
    },
    {
        "name": "US30Y",
        "fred": "DGS30",
        "unit": "basis_points",
    },
    {
        "name": "VIX",
        "fred": "VIXCLS",
        "unit": None,
    },
    {
        "name": "DXY",
        "fred": "DTWEXBGS",
        "unit": None,
    },
)
