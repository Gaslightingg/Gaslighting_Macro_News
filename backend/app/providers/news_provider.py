from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Protocol

import httpx

from ..utils.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class NewsEvent:
    id: str
    source: str
    title: str
    country: str
    importance: str
    datetime_utc: str
    datetime_local: str
    unit: str | None
    previous: str | None
    forecast: str | None
    actual: str | None
    revised: str | None
    status: str
    updated_at: str


class NewsProvider(Protocol):
    async def list_events(
        self,
        start: datetime,
        end: datetime,
        countries: list[str] | None = None,
        importance: list[str] | None = None,
    ) -> list[NewsEvent]: ...

    async def get_event_details(self, event_id: str) -> NewsEvent | None: ...


class TradingViewNewsProvider:
    base_url = "https://economic-calendar.tradingview.com/events"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=12.0)

    async def list_events(
        self,
        start: datetime,
        end: datetime,
        countries: list[str] | None = None,
        importance: list[str] | None = None,
    ) -> list[NewsEvent]:
        params = {
            "from": start.strftime("%Y-%m-%d"),
            "to": end.strftime("%Y-%m-%d"),
        }
        if countries:
            params["countries"] = ",".join(c.lower() for c in countries)
        try:
            resp = await self._client.get(self.base_url, params=params)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.warning("TradingView news provider failed: %s", exc)
            return []

        items = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []

        now = datetime.now(timezone.utc)
        out: list[NewsEvent] = []
        for row in items:
            title = row.get("title") or row.get("event") or "Macro event"
            dt_raw = row.get("date") or row.get("datetime")
            if not dt_raw:
                continue
            try:
                dt = datetime.fromisoformat(str(dt_raw).replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                continue
            impact = str(row.get("importance") or row.get("importance_num") or "MED").upper()
            if impact not in {"LOW", "MED", "HIGH"}:
                impact = "HIGH" if impact in {"3", "4"} else "MED" if impact in {"2"} else "LOW"
            event = NewsEvent(
                id=f"tv:{row.get('id', title)}:{dt.strftime('%Y%m%d%H%M')}",
                source="tradingview",
                title=title,
                country=str(row.get("country") or "US").upper(),
                importance=impact,
                datetime_utc=dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                datetime_local=dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                unit=row.get("unit"),
                previous=_to_text(row.get("previous")),
                forecast=_to_text(row.get("forecast")),
                actual=_to_text(row.get("actual")),
                revised=_to_text(row.get("revised")),
                status="RELEASED" if row.get("actual") not in (None, "") or dt <= now else "UPCOMING",
                updated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            out.append(event)
        return out

    async def get_event_details(self, event_id: str) -> NewsEvent | None:
        return None


class StaticNewsProvider:
    async def list_events(
        self,
        start: datetime,
        end: datetime,
        countries: list[str] | None = None,
        importance: list[str] | None = None,
    ) -> list[NewsEvent]:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        templates = [
            ("US CPI YoY", "US", "HIGH", -1, "3.2", "3.1", "3.0", "%"),
            ("Non-Farm Payrolls", "US", "HIGH", -3, "210K", "190K", "175K", "k"),
            ("FOMC Rate Decision", "US", "HIGH", 2, "5.25", "5.25", None, "%"),
            ("ECB Rate Decision", "EU", "HIGH", 5, "4.00", "4.00", None, "%"),
            ("UK CPI YoY", "UK", "MED", 1, "3.9", "3.7", None, "%"),
            ("BoJ Policy Rate", "JP", "MED", -5, "0.10", "0.10", "0.10", "%"),
        ]
        events: list[NewsEvent] = []
        for idx, (title, ctry, imp, day_shift, previous, forecast, actual, unit) in enumerate(templates):
            dt = now + timedelta(days=day_shift)
            if dt < start - timedelta(days=1) or dt > end + timedelta(days=1):
                continue
            if countries and ctry not in countries:
                continue
            if importance and imp not in importance:
                continue
            events.append(
                NewsEvent(
                    id=f"static:{idx}:{dt.strftime('%Y%m%d%H%M')}",
                    source="static",
                    title=title,
                    country=ctry,
                    importance=imp,
                    datetime_utc=dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    datetime_local=dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                    unit=unit,
                    previous=previous,
                    forecast=forecast,
                    actual=actual,
                    revised=None,
                    status="RELEASED" if actual is not None else "UPCOMING",
                    updated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )
        return events

    async def get_event_details(self, event_id: str) -> NewsEvent | None:
        return None


def _to_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_news_provider() -> NewsProvider:
    settings = get_settings()
    mode = getattr(settings, "news_provider", "auto")
    if mode in {"tradingview", "auto"}:
        return TradingViewNewsProvider()
    return StaticNewsProvider()
