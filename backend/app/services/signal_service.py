from __future__ import annotations

from ..analytics import build_signals
from ..models.schemas import SignalsResponse
from .macro_service import get_macro_payload
from .price_service import get_prices_payload


def get_signals_payload() -> SignalsResponse:
    macro = get_macro_payload()
    prices = get_prices_payload()
    tickers = [item.symbol for item in prices.tickers]
    return SignalsResponse(
        as_of=macro.as_of,
        disclaimer="Not financial advice",
        signals=build_signals(macro.series, tickers),
    )
