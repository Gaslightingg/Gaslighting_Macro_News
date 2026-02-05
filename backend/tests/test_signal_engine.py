from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.signal_engine import SignalEngine, _agreement, _build_bullets, _default_config
from app.utils.cache_db import CacheStore


class FakeDataProvider:
    def __init__(self, series: dict[str, list[tuple[str, float]]]) -> None:
        self.series = series

    async def listAvailableIndicators(self) -> list[str]:
        return list(self.series.keys())

    async def getSeries(self, indicatorId: str, opts: dict[str, str | None]) -> dict:
        points = [{"t": d, "value": v} for d, v in self.series.get(indicatorId, [])]
        return {"points": points, "lastUpdated": points[-1]["t"] if points else None, "source": "test"}


class FakeTickerProvider:
    def __init__(self, tickers: list[str]) -> None:
        self._tickers = tickers

    async def listTickers(self) -> list[str]:
        return self._tickers


def _series(base: float, step: float, count: int, start_days_ago: int = 1500) -> list[tuple[str, float]]:
    start = datetime.utcnow().date() - timedelta(days=start_days_ago)
    out = []
    for i in range(count):
        out.append(((start + timedelta(days=i)).isoformat(), base + step * i))
    return out


@pytest.mark.asyncio
async def test_normalization_and_zscore_pipeline():
    config = _default_config()
    data = FakeDataProvider({"unemployment": _series(3.0, 0.001, 1200)})
    engine = SignalEngine(data, FakeTickerProvider(["sp500"]), CacheStore("sqlite:///./cache.db"), config)

    normalized = await engine._normalized_indicator("unemployment", force_recompute=True)

    assert normalized is not None
    assert "zscore" in normalized
    assert -1 <= normalized["normalizedScore"] <= 1


@pytest.mark.asyncio
async def test_factor_aggregation_and_coverage():
    series = {
        "unemployment": _series(4.0, 0.0, 1400),
        "jobless_claims": _series(200.0, 0.01, 1400),
        "continuing_claims": _series(1700.0, 0.02, 1400),
    }
    engine = SignalEngine(FakeDataProvider(series), FakeTickerProvider(["sp500"]), CacheStore("sqlite:///./cache.db"), _default_config())

    snapshot = await engine.getFactorsSnapshot({"forceRecompute": True})

    assert "laborFactor" in snapshot.factors
    assert snapshot.factors["laborFactor"].coverage > 0


@pytest.mark.asyncio
async def test_ticker_scoring_and_classification():
    series = {
        "unemployment": _series(4.8, -0.001, 1400),
        "jobless_claims": _series(280.0, -0.01, 1400),
        "continuing_claims": _series(1900.0, -0.02, 1400),
        "jolts": _series(8000.0, 0.02, 1400),
        "vix": _series(30.0, -0.01, 1400),
    }
    engine = SignalEngine(FakeDataProvider(series), FakeTickerProvider(["sp500"]), CacheStore("sqlite:///./cache.db"), _default_config())

    card = await engine.computeSignal("sp500", {"forceRecompute": True})

    assert card.signal in {"BULLISH", "BEARISH", "NEUTRAL"}
    assert 0 <= card.confidence <= 100


def test_bullet_generation_is_deterministic():
    from app.models.schemas import FactorContributor

    contributors = [
        (0.8, "laborFactor", FactorContributor(indicator="unemployment", contribution=-0.5, zscore=1.2, delta=0.2), 0.4),
        (0.3, "volFactor", FactorContributor(indicator="vix", contribution=-0.2, zscore=0.7, delta=1.0), -0.3),
    ]
    b1 = _build_bullets(contributors, 0.4)
    b2 = _build_bullets(contributors, 0.4)
    assert b1 == b2
    assert any("Insufficient macro coverage" in bullet for bullet in b1)


def test_agreement_reduces_on_conflict():
    assert _agreement([0.4, 0.4, 0.4]) > _agreement([0.8, -0.8, 0.1])
