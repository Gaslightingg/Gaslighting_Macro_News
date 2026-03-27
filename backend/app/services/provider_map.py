from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    symbol: str


@dataclass(frozen=True)
class InstrumentProviderPlan:
    latest_chain: tuple[ProviderAttempt, ...]
    history_chain: tuple[ProviderAttempt, ...]


PROVIDER_MAP: dict[str, InstrumentProviderPlan] = {
    "sp500": InstrumentProviderPlan(
        latest_chain=(
            ProviderAttempt("stooq", "spy.us"),
            ProviderAttempt("yfinance", "^GSPC"),
            ProviderAttempt("yfinance", "SPY"),
        ),
        history_chain=(
            ProviderAttempt("stooq", "spy.us"),
            ProviderAttempt("yfinance", "^GSPC"),
            ProviderAttempt("yfinance", "SPY"),
        ),
    ),
    "nas100": InstrumentProviderPlan(
        latest_chain=(
            ProviderAttempt("stooq", "qqq.us"),
            ProviderAttempt("yfinance", "^NDX"),
            ProviderAttempt("yfinance", "QQQ"),
        ),
        history_chain=(
            ProviderAttempt("stooq", "qqq.us"),
            ProviderAttempt("yfinance", "^NDX"),
            ProviderAttempt("yfinance", "QQQ"),
        ),
    ),
    "nqmini": InstrumentProviderPlan(
        latest_chain=(
            ProviderAttempt("yfinance", "MNQ=F"),
            ProviderAttempt("yfinance", "NQ=F"),
            ProviderAttempt("stooq", "qqq.us"),
            ProviderAttempt("stooq", "ndx.us"),
        ),
        history_chain=(
            ProviderAttempt("yfinance", "MNQ=F"),
            ProviderAttempt("yfinance", "NQ=F"),
            ProviderAttempt("stooq", "qqq.us"),
            ProviderAttempt("stooq", "ndx.us"),
        ),
    ),
    "eurusd": InstrumentProviderPlan(
        latest_chain=(
            ProviderAttempt("frankfurter", "EUR/USD"),
            ProviderAttempt("stooq", "eurusd"),
            ProviderAttempt("stooq", "eurusd.f"),
            ProviderAttempt("yfinance", "EURUSD=X"),
        ),
        history_chain=(
            ProviderAttempt("stooq", "eurusd"),
            ProviderAttempt("stooq", "eurusd.f"),
            ProviderAttempt("yfinance", "EURUSD=X"),
        ),
    ),
    "gbpusd": InstrumentProviderPlan(
        latest_chain=(
            ProviderAttempt("frankfurter", "GBP/USD"),
            ProviderAttempt("stooq", "gbpusd"),
            ProviderAttempt("stooq", "gbpusd.f"),
            ProviderAttempt("yfinance", "GBPUSD=X"),
        ),
        history_chain=(
            ProviderAttempt("stooq", "gbpusd"),
            ProviderAttempt("stooq", "gbpusd.f"),
            ProviderAttempt("yfinance", "GBPUSD=X"),
        ),
    ),
    "gbpjpy": InstrumentProviderPlan(
        latest_chain=(
            ProviderAttempt("frankfurter", "GBP/JPY"),
            ProviderAttempt("stooq", "gbpjpy"),
            ProviderAttempt("stooq", "gbpjpy.f"),
            ProviderAttempt("yfinance", "GBPJPY=X"),
        ),
        history_chain=(
            ProviderAttempt("stooq", "gbpjpy"),
            ProviderAttempt("stooq", "gbpjpy.f"),
            ProviderAttempt("yfinance", "GBPJPY=X"),
        ),
    ),
    "xauusd": InstrumentProviderPlan(
        latest_chain=(
            ProviderAttempt("stooq", "xauusd"),
            ProviderAttempt("stooq", "xauusd.f"),
            ProviderAttempt("yfinance", "XAUUSD=X"),
            ProviderAttempt("yfinance", "GC=F"),
        ),
        history_chain=(
            ProviderAttempt("stooq", "xauusd"),
            ProviderAttempt("stooq", "xauusd.f"),
            ProviderAttempt("yfinance", "XAUUSD=X"),
            ProviderAttempt("yfinance", "GC=F"),
        ),
    ),
}


def get_provider_plan(ticker_id: str) -> InstrumentProviderPlan | None:
    return PROVIDER_MAP.get(ticker_id)
