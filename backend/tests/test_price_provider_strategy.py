from app.services.price_catalog import get_price_config
from app.services.symbols import get_symbol_mapping


def test_index_instruments_use_yfinance_primary():
    for ticker_id, expected_symbol in (
        ("sp500", "^GSPC"),
        ("nas100", "^NDX"),
        ("nqmini", "MNQ=F"),
    ):
        config = get_price_config(ticker_id)
        assert config is not None
        mapping = get_symbol_mapping(ticker_id)
        assert mapping.get("yfinance") == expected_symbol
        assert not mapping.get("stooq")
