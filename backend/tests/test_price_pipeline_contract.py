from __future__ import annotations

import pytest

from app.services.price_catalog import PRICE_TICKERS
from app.services.price_service import get_price_provider_health_payload
from app.services.symbols import get_symbol_mapping


def test_symbol_mapping_for_all_price_tickers():
    for config in PRICE_TICKERS:
        mapping = get_symbol_mapping(config.id)
        assert mapping.get("stooq") or mapping.get("yfinance"), f"{config.id} has no provider symbol mapping"


@pytest.mark.asyncio
async def test_price_health_endpoint_payload_shape():
    payload = await get_price_provider_health_payload()
    assert payload.as_of
    assert payload.overall_status in {"ok", "degraded", "down"}
    assert 0.0 <= payload.live_ratio <= 1.0
    assert payload.instruments
