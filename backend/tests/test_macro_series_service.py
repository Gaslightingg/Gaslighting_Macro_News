import pytest

from app.services.macro_series_service import get_latest_payload, get_series_payload
from app.services.macro_catalog import all_indicators


@pytest.mark.asyncio
async def test_macro_latest_payload_returns_items():
    payload = await get_latest_payload()
    assert payload.latest
    assert len(payload.latest) == len(all_indicators())


@pytest.mark.asyncio
async def test_macro_series_unknown_indicator():
    with pytest.raises(ValueError):
        await get_series_payload("unknown-indicator", "1y")
