from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def _post(self, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()

    async def get_prices(self) -> dict:
        return await self._get("/api/prices")

    async def get_signals(self) -> dict:
        return await self._get("/api/signals")

    async def get_signal(self, ticker: str) -> dict:
        return await self._get(f"/api/signals/{ticker}")

    async def get_news(self, days: int = 7, page: int = 1, page_size: int = 5) -> dict:
        end = datetime.utcnow().date()
        start = (end - timedelta(days=days)).isoformat()
        params = {"start": start, "end": end.isoformat()}
        payload = await self._get("/api/news", params=params)
        events = payload.get("events", []) or []
        start_idx = (page - 1) * page_size
        return {**payload, "events": events[start_idx : start_idx + page_size], "total": len(events)}

    async def get_health(self) -> dict:
        return await self._get("/api/health")

    async def refresh_macro(self) -> dict:
        return await self._post("/api/macro/refresh", payload={})

    async def get_macro_latest(self) -> dict:
        return await self._get("/api/macro/latest")
