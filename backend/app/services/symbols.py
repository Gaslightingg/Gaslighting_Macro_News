from __future__ import annotations

from typing import TypedDict

from .price_catalog import get_price_config
from .provider_map import get_provider_plan


class SymbolMapping(TypedDict, total=False):
    stooq: str
    stooq_fallbacks: tuple[str, ...]
    yfinance: str

def get_symbol_mapping(ticker_id: str) -> SymbolMapping:
    plan = get_provider_plan(ticker_id)
    if plan:
        stooq_symbols = [item.symbol for item in plan.latest_chain if item.provider == "stooq"]
        yf_symbols = [item.symbol for item in plan.latest_chain if item.provider == "yfinance"]
        return {
            "stooq": stooq_symbols[0] if stooq_symbols else "",
            "stooq_fallbacks": tuple(stooq_symbols[1:]),
            "yfinance": yf_symbols[0] if yf_symbols else "",
        }
    config = get_price_config(ticker_id)
    if not config:
        return {}
    return {
        "stooq": config.stooq_symbol or "",
        "stooq_fallbacks": tuple(config.stooq_fallback_symbols or ()),
        "yfinance": config.yfinance_symbol or "",
    }
