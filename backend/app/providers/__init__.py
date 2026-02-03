from .base import MarketDataProvider
from .mock_provider import MockMarketDataProvider
from .real_provider import RealMarketDataProvider
from ..utils.settings import get_settings


def get_provider() -> MarketDataProvider:
    settings = get_settings()
    if settings.market_data_provider.lower() == "real":
        return RealMarketDataProvider(settings=settings)
    return MockMarketDataProvider()


__all__ = [
    "MarketDataProvider",
    "MockMarketDataProvider",
    "RealMarketDataProvider",
    "get_provider",
]
