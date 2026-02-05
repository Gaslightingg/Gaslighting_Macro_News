from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import logging

logger = logging.getLogger(__name__)


class FredClient:
    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=timeout),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )

    async def get_series_observations(
        self,
        series_id: str,
        limit: int = 500,
        sort_order: str = "desc",
        retries: int = 2,
        observation_start: str | None = None,
    ) -> list[dict[str, Any]]:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "api_key": self.api_key,
            "series_id": series_id,
            "file_type": "json",
            "sort_order": sort_order,
            "limit": limit,
        }
        if observation_start:
            params["observation_start"] = observation_start
        attempt = 0
        while True:
            try:
                response = await self._client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                return payload.get("observations", [])
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if 400 <= status_code < 500:
                    logger.warning("FRED returned %s for %s; skipping series", status_code, series_id)
                    return []
                attempt += 1
                if attempt > retries:
                    logger.warning("FRED request failed for %s: %s", series_id, exc)
                    return []
            except httpx.HTTPError as exc:
                attempt += 1
                if attempt > retries:
                    logger.warning("FRED request failed for %s: %s", series_id, exc)
                    return []

    async def close(self) -> None:
        await self._client.aclose()


def parse_fred_points(observations: list[dict[str, Any]]) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []
    for obs in observations:
        value = obs.get("value")
        date = obs.get("date")
        if value in (None, ".") or date is None:
            continue
        try:
            numeric = float(value)
        except ValueError:
            continue
        points.append((date, numeric))
    return points


def latest_from_points(points: list[tuple[str, float]]) -> tuple[str, float] | None:
    if not points:
        return None
    points_sorted = sorted(points, key=lambda item: item[0])
    return points_sorted[-1]


def change_from_points(points: list[tuple[str, float]]) -> float | None:
    if len(points) < 2:
        return None
    points_sorted = sorted(points, key=lambda item: item[0])
    latest = points_sorted[-1][1]
    previous = points_sorted[-2][1]
    return latest - previous


def format_series_date(date: str) -> str:
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        return dt.date().isoformat()
    except ValueError:
        return date
