from __future__ import annotations

import pytest

from app.services.backtest import _build_trades, _compute_metrics, _parse_date
from app.models.schemas import BacktestSeriesPoint


def test_parse_date_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        _parse_date("2024/01/01")


def test_build_trades_long_then_flat() -> None:
    points = [
        {"t": "2024-01-01", "value": 100.0},
        {"t": "2024-01-02", "value": 110.0},
        {"t": "2024-01-03", "value": 105.0},
    ]
    positions = [0, 1, 0]
    trades = _build_trades(points, positions)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_time == "2024-01-02"
    assert trade.exit_time == "2024-01-03"
    assert trade.direction == "LONG"
    assert trade.pnl == pytest.approx((105.0 - 110.0) / 110.0, abs=1e-6)


def test_compute_metrics_basic() -> None:
    curve = [
        BacktestSeriesPoint(date="2024-01-01", value=1.0),
        BacktestSeriesPoint(date="2024-01-02", value=1.1),
        BacktestSeriesPoint(date="2024-01-03", value=1.05),
    ]
    metrics = _compute_metrics([], curve)
    assert metrics.cumulative_return == pytest.approx(0.05, abs=1e-6)
    assert metrics.max_drawdown > 0
