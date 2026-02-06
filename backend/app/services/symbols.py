from __future__ import annotations

from typing import TypedDict


class SymbolMapping(TypedDict, total=False):
    stooq: str
    yfinance: str


SYMBOL_MAPPING: dict[str, SymbolMapping] = {
    "sp500": {"stooq": "spx", "yfinance": "^GSPC"},
    "nas100": {"stooq": "ndx", "yfinance": "^NDX"},
    "nqmini": {"yfinance": "NQ=F"},
    "eurusd": {"stooq": "eurusd", "yfinance": "EURUSD=X"},
    "gbpusd": {"stooq": "gbpusd", "yfinance": "GBPUSD=X"},
    "gbpjpy": {"stooq": "gbpjpy", "yfinance": "GBPJPY=X"},
    "xauusd": {"stooq": "xauusd", "yfinance": "XAUUSD=X"},
}


def get_symbol_mapping(ticker_id: str) -> SymbolMapping:
    return SYMBOL_MAPPING.get(ticker_id, {})
