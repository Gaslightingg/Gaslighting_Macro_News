from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.schemas import MacroResponse, PricesResponse


class MarketDataProvider(ABC):
    """Base interface for market data providers."""

    @abstractmethod
    def get_prices(self) -> PricesResponse:
        """Return the latest prices payload."""

    @abstractmethod
    def get_macro(self) -> MacroResponse:
        """Return the latest macro payload."""
