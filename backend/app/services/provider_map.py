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
        latest_chain=(ProviderAttempt("yfinance", "^GSPC"), ProviderAttempt("yfinance", "SPY")),
        history_chain=(ProviderAttempt("yfinance", "^GSPC"), ProviderAttempt("yfinance", "SPY")),
    ),
    "nas100": InstrumentProviderPlan(
        latest_chain=(ProviderAttempt("yfinance", "^NDX"), ProviderAttempt("yfinance", "QQQ")),
        history_chain=(ProviderAttempt("yfinance", "^NDX"), ProviderAttempt("yfinance", "QQQ")),
    ),
    "nqmini": InstrumentProviderPlan(
        latest_chain=(ProviderAttempt("yfinance", "MNQ=F"), ProviderAttempt("yfinance", "NQ=F")),
        history_chain=(ProviderAttempt("yfinance", "MNQ=F"), ProviderAttempt("yfinance", "NQ=F")),
    ),
    "eurusd": InstrumentProviderPlan(
        latest_chain=(ProviderAttempt("stooq", "eurusd"), ProviderAttempt("yfinance", "EURUSD=X")),
        history_chain=(ProviderAttempt("stooq", "eurusd"), ProviderAttempt("yfinance", "EURUSD=X")),
    ),
    "gbpusd": InstrumentProviderPlan(
        latest_chain=(ProviderAttempt("stooq", "gbpusd"), ProviderAttempt("yfinance", "GBPUSD=X")),
        history_chain=(ProviderAttempt("stooq", "gbpusd"), ProviderAttempt("yfinance", "GBPUSD=X")),
    ),
    "gbpjpy": InstrumentProviderPlan(
        latest_chain=(ProviderAttempt("stooq", "gbpjpy"), ProviderAttempt("yfinance", "GBPJPY=X")),
        history_chain=(ProviderAttempt("stooq", "gbpjpy"), ProviderAttempt("yfinance", "GBPJPY=X")),
    ),
    "xauusd": InstrumentProviderPlan(
        latest_chain=(ProviderAttempt("yfinance", "GC=F"), ProviderAttempt("stooq", "xauusd")),
        history_chain=(ProviderAttempt("yfinance", "GC=F"), ProviderAttempt("stooq", "xauusd")),
    ),
}


def get_provider_plan(ticker_id: str) -> InstrumentProviderPlan | None:
    return PROVIDER_MAP.get(ticker_id)
