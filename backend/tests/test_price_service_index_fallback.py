from __future__ import annotations

import pytest

from app.services.price_catalog import get_price_config
from app.services import price_service


@pytest.mark.asyncio
async def test_index_uses_stooq_fallback_symbol(monkeypatch):
    config = get_price_config("sp500")
    assert config is not None
    assert "^spx" in config.stooq_fallback_symbols

    calls: list[str] = []

    async def fake_fetch_latest(client, symbol):
        calls.append(symbol)
        if symbol == "^spx":
            return 5000.0, 0.5, "2026-01-01"
        return None

    class FakeStore:
        async def get_latest(self, _):
            return None

        async def get_meta(self, _):
            return None

        async def upsert_latest(self, *_args, **_kwargs):
            return None

        async def set_meta(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(price_service, "_fetch_stooq_latest", fake_fetch_latest)

    payload = await price_service._ensure_latest(FakeStore(), client=None, config=config)

    assert payload is not None
    assert payload["source"] == "stooq"
    assert payload["status"] == "live"
    assert calls[:2] == ["spx", "^spx"]
