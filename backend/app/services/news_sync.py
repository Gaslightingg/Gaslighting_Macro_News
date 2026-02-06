from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging

from ..providers.news_provider import NewsProvider, StaticNewsProvider, TradingEconomicsNewsProvider, TradingViewNewsProvider
from ..services.news_utils import add_months
from ..utils.settings import get_settings
from ..utils.news_db import NewsStore

logger = logging.getLogger(__name__)

SYNC_CHUNK_DAYS = 30
SYNC_TIMEOUT_S = 8


def build_provider_chain() -> list[NewsProvider]:
    settings = get_settings()
    chain: list[NewsProvider] = []
    api_key = settings.tradingeconomics_api_key
    if api_key:
        chain.append(TradingEconomicsNewsProvider(api_key))
    else:
        chain.append(TradingEconomicsNewsProvider("guest:guest"))
    chain.append(StaticNewsProvider())
    # TradingView last resort
    chain.append(TradingViewNewsProvider())
    return chain


async def sync_news_range(store: NewsStore, months_back: int = 6, months_forward: int = 1) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    start = add_months(now, -months_back)
    end = add_months(now, months_forward)

    provider_chain = build_provider_chain()
    created = 0
    updated = 0

    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=SYNC_CHUNK_DAYS), end)
        events = await _fetch_from_chain(provider_chain, cursor, chunk_end)
        if events:
            await store.upsert_events([e.__dict__ for e in events])
            created += len(events)
            updated += len(events)
        cursor = chunk_end + timedelta(days=1)

    return {"created": created, "updated": updated}


async def _fetch_from_chain(providers: list[NewsProvider], start: datetime, end: datetime):
    for provider in providers:
        try:
            events = await asyncio.wait_for(provider.list_events(start=start, end=end), timeout=SYNC_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("News provider timeout (%s - %s)", start.date(), end.date())
            events = []
        if events:
            return events
    return []
