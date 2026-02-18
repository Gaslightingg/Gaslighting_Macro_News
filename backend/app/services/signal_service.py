from __future__ import annotations

from datetime import datetime

from ..models.schemas import FactorsSnapshot, RecomputeResponse, SignalCard, SignalsApiResponse, SignalsDebugResponse
from ..services.signal_engine import MacroDataProvider, SignalEngine, SiteTickerProvider
from ..utils.cache_db import CacheStore
from ..utils.settings import get_settings

_engine_cache: SignalEngine | None = None


def _get_engine() -> SignalEngine:
    global _engine_cache
    if _engine_cache is not None:
        return _engine_cache
    settings = get_settings()
    _engine_cache = SignalEngine(
        data_provider=MacroDataProvider(),
        ticker_provider=SiteTickerProvider(),
        cache=CacheStore(settings.cache_db_url),
    )
    return _engine_cache


async def get_signals_payload(force_recompute: bool = False) -> SignalsApiResponse:
    engine = _get_engine()
    cards = await engine.computeSignals({"forceRecompute": force_recompute})
    updated_at = cards[0].updated_at if cards else datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return SignalsApiResponse(
        updated_at=updated_at,
        signals=cards,
        errors=[],
        missing_inputs=engine.get_missing_inputs(),
    )


async def get_signal_payload(ticker: str, force_recompute: bool = False) -> SignalCard:
    engine = _get_engine()
    return await engine.computeSignal(ticker, {"forceRecompute": force_recompute})


async def get_factors_payload(force_recompute: bool = False) -> FactorsSnapshot:
    engine = _get_engine()
    return await engine.getFactorsSnapshot({"forceRecompute": force_recompute})


async def get_signals_debug_payload() -> SignalsDebugResponse:
    engine = _get_engine()
    return await engine.getDebugSnapshot()


async def recompute_signals_payload() -> RecomputeResponse:
    engine = _get_engine()
    signals = await engine.computeSignals({"forceRecompute": True})
    updated_at = signals[0].updated_at if signals else datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return RecomputeResponse(ok=True, updated_at=updated_at)
