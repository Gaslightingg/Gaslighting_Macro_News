from __future__ import annotations

from typing import TypedDict

from .price_catalog import get_price_config


class SymbolMapping(TypedDict, total=False):
    stooq: str
    stooq_fallbacks: tuple[str, ...]
    yfinance: str

def get_symbol_mapping(ticker_id: str) -> SymbolMapping:
    config = get_price_config(ticker_id)
    if not config:
        return {}
    return {
        "stooq": config.stooq_symbol or "",
        "stooq_fallbacks": tuple(config.stooq_fallback_symbols or ()),
        "yfinance": config.yfinance_symbol or "",
    }
