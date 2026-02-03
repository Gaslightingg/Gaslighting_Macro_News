from __future__ import annotations

from ..models.schemas import PricesResponse


def get_prices_payload() -> PricesResponse:
    return PricesResponse(
        as_of="2024-03-01T12:00:00Z",
        tickers=[
            {"symbol": "S&P500", "price": 5041.3, "change_pct": 0.62},
            {"symbol": "NAS100", "price": 17812.9, "change_pct": 1.08},
            {"symbol": "EUR/USD", "price": 1.0824, "change_pct": -0.18},
            {"symbol": "GBP/USD", "price": 1.2689, "change_pct": 0.24},
            {"symbol": "GBP/JPY", "price": 191.42, "change_pct": -0.41},
            {"symbol": "XAUUSD", "price": 2034.7, "change_pct": 0.73},
        ],
    )
