from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PriceConfig:
    id: str
    symbol: str
    name: str
    asset_class: str
    unit: str | None
    stooq_symbol: str
    stooq_fallback_symbols: tuple[str, ...] = ()
    yfinance_symbol: str = ""


PRICE_TICKERS: Iterable[PriceConfig] = (
    PriceConfig(
        id="sp500",
        symbol="S&P500",
        name="S&P 500",
        asset_class="index",
        unit="pts",
        stooq_symbol="spx",
        stooq_fallback_symbols=("^spx", "spx.us"),
        yfinance_symbol="^GSPC",
    ),
    PriceConfig(
        id="nas100",
        symbol="NAS100",
        name="Nasdaq 100",
        asset_class="index",
        unit="pts",
        stooq_symbol="ndx",
        stooq_fallback_symbols=("^ndx", "ndx.us"),
        yfinance_symbol="^NDX",
    ),
    PriceConfig(
        id="nqmini",
        symbol="NASDAQ mini",
        name="Nasdaq Mini",
        asset_class="index",
        unit="pts",
        stooq_symbol="nq.f",
        stooq_fallback_symbols=("nq=F",),
        yfinance_symbol="NQ=F",
    ),
    PriceConfig(
        id="eurusd",
        symbol="EUR/USD",
        name="EUR/USD",
        asset_class="fx",
        unit="USD",
        stooq_symbol="eurusd",
        yfinance_symbol="EURUSD=X",
    ),
    PriceConfig(
        id="gbpusd",
        symbol="GBP/USD",
        name="GBP/USD",
        asset_class="fx",
        unit="USD",
        stooq_symbol="gbpusd",
        yfinance_symbol="GBPUSD=X",
    ),
    PriceConfig(
        id="gbpjpy",
        symbol="GBP/JPY",
        name="GBP/JPY",
        asset_class="fx",
        unit="JPY",
        stooq_symbol="gbpjpy",
        yfinance_symbol="GBPJPY=X",
    ),
    PriceConfig(
        id="xauusd",
        symbol="XAUUSD",
        name="Gold Spot",
        asset_class="commodity",
        unit="USD",
        stooq_symbol="xauusd",
        yfinance_symbol="XAUUSD=X",
    ),
)


def get_price_config(ticker_id: str) -> PriceConfig | None:
    for item in PRICE_TICKERS:
        if item.id == ticker_id:
            return item
    return None


def resolve_price_config(identifier: str) -> PriceConfig | None:
    key = identifier.strip().lower()
    for item in PRICE_TICKERS:
        if key in {item.id.lower(), item.symbol.lower(), item.name.lower()}:
            return item
    return None
