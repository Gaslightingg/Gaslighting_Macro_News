import pytest

from app.services.macro_series_service import get_latest_payload, get_series_payload
from app.services.macro_catalog import all_indicators
from app.utils.cache_db import CacheStore


@pytest.mark.asyncio
async def test_macro_latest_payload_returns_items():
    payload = await get_latest_payload()
    assert payload.latest
    assert len(payload.latest) == len(all_indicators())


@pytest.mark.asyncio
async def test_macro_latest_payload_cached_with_stale(monkeypatch):
    cached_payload = {
        "as_of": "2024-01-01",
        "latest": [
            {
                "indicator_id": "mock",
                "name": "Mock",
                "value": 1.0,
                "change": 0.1,
                "unit": "%",
                "last_updated": "2024-01-01",
                "category": "Mock",
                "status": "cached",
                "source": "FRED",
                "history_points": 1,
                "expected_frequency": "daily",
                "stale_after_seconds": 3600,
                "quality": "high",
                "error": None,
                "stale": True,
            }
        ],
        "stale": True,
    }

    def fake_get_cache(self, key):  # noqa: ANN001
        return cached_payload

    monkeypatch.setattr(CacheStore, "get_cache", fake_get_cache)
    payload = await get_latest_payload()
    assert payload.stale is True
    assert payload.latest[0].indicator_id == "mock"


@pytest.mark.asyncio
async def test_macro_series_unknown_indicator():
    with pytest.raises(ValueError):
        await get_series_payload("unknown-indicator", "1y")
