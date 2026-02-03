from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


class FredClient:
    def __init__(self, api_key: str, timeout: float = 10.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def get_series_observations(
        self,
        series_id: str,
        limit: int = 500,
        sort_order: str = "desc",
    ) -> list[dict[str, Any]]:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "api_key": self.api_key,
            "series_id": series_id,
            "file_type": "json",
            "sort_order": sort_order,
            "limit": limit,
        }
        response = httpx.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return payload.get("observations", [])


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
