from __future__ import annotations

import asyncio
from datetime import datetime

from app.services import macro_series_service
from app.services.macro_catalog import find_indicator


def test_observation_start_for_10y_window():
    start = macro_series_service._observation_start_for_range("10y")
    assert start is not None
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    delta_days = (datetime.utcnow() - start_dt).days
    assert 3640 <= delta_days <= 3665


def test_fetch_series_uses_observation_start_for_10y():
    indicator = find_indicator("pmi")
    assert indicator is not None

    captured: dict[str, str | int | None] = {}

    class FakeSettings:
        fred_api_key = "dummy"
        fred_pmi_series_id = "NAPM"

    class FakeClient:
        async def get_series_observations(
            self,
            series_id: str,
            limit: int = 500,
            sort_order: str = "desc",
            retries: int = 2,
            observation_start: str | None = None,
        ):
            captured["series_id"] = series_id
            captured["limit"] = limit
            captured["observation_start"] = observation_start
            return []

    original_get_settings = macro_series_service.get_settings
    macro_series_service.get_settings = lambda: FakeSettings()
    try:
        asyncio.run(
            macro_series_service._fetch_series(
                indicator,
                "10y",
                FakeClient(),
                asyncio.Semaphore(1),
            )
        )
    finally:
        macro_series_service.get_settings = original_get_settings

    assert captured["series_id"] == "NAPM"
    assert captured["limit"] == 4000
    assert captured["observation_start"] is not None
