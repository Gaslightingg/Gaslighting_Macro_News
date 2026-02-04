from app.services.price_service import _normalize_prices_payload
from app.models.schemas import PricesResponse


def test_normalize_prices_payload_legacy():
    legacy = {
        "as_of": "2024-01-01T00:00:00Z",
        "tickers": [
            {"symbol": "EUR/USD", "price": 1.1},
            {"symbol": "S&P500", "price": 4800.0, "change_pct": 0.5},
        ],
    }
    normalized = _normalize_prices_payload(legacy)
    parsed = PricesResponse(**normalized)
    assert parsed.tickers[0].id
    assert parsed.tickers[0].value is not None
