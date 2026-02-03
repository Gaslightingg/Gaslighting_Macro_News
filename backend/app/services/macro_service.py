from __future__ import annotations

from ..models.schemas import MacroResponse
from ..providers import MarketDataProvider, get_provider


def get_macro_payload(provider: MarketDataProvider | None = None) -> MacroResponse:
    provider = provider or get_provider()
    return provider.get_macro()
