from __future__ import annotations

from ..analytics import build_signals
from ..models.schemas import SignalsResponse
from ..providers import MarketDataProvider, get_provider
from .macro_service import get_macro_payload
from .price_service import get_prices_payload


def get_signals_payload(provider: MarketDataProvider | None = None) -> SignalsResponse:
    provider = provider or get_provider()
    macro = get_macro_payload(provider)
    prices = get_prices_payload(provider)
    tickers = [item.symbol for item in prices.tickers]
    return SignalsResponse(
        as_of=macro.as_of,
        disclaimer="Not financial advice",
        signals=build_signals(macro.series, tickers),
    )
