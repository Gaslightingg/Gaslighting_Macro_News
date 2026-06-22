from app.services.provider_map import PROVIDER_MAP


def test_provider_map_contains_all_instruments():
    expected = {"sp500", "nas100", "nqmini", "eurusd", "gbpusd", "gbpjpy", "xauusd"}
    assert set(PROVIDER_MAP.keys()) == expected


def test_index_instruments_have_stooq_index_fallbacks():
    assert PROVIDER_MAP["sp500"].latest_chain[0].symbol == "^spx"
    assert PROVIDER_MAP["nas100"].latest_chain[0].symbol == "^ndx"
    assert PROVIDER_MAP["nqmini"].latest_chain[0].symbol == "MNQ=F"
    assert PROVIDER_MAP["nqmini"].latest_chain[-1].symbol == "^ndx"
