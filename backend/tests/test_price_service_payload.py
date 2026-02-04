from app.services.price_service import _history_meta, _parse_range
from app.utils.price_history_db import HistoryPoint


def test_parse_range_defaults():
    assert _parse_range("1y").days == 365
    assert _parse_range("max") is None


def test_history_meta_handles_points():
    meta = _history_meta([HistoryPoint(date="2020-01-01", value=1.0), HistoryPoint(date="2020-01-02", value=2.0)])
    assert meta.data_start == "2020-01-01"
    assert meta.data_end == "2020-01-02"
    assert meta.points_count == 2
