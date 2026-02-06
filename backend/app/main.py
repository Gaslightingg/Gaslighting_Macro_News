from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from time import perf_counter
from contextlib import suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from bot.bot_app import start_bot, stop_bot
from .api.routes import router as api_router
from .utils.cache_db import CacheStore
from .utils.logging import configure_logging
from .utils.settings import get_settings
from .services.macro_series_service import get_latest_payload
from .services.price_service import get_prices_payload
from .services.signal_service import get_signals_payload

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Gaslighting Macro News API")
settings = get_settings()
origins = settings.parsed_cors_origins()
CacheStore(settings.cache_db_url)
logger.info("CORS origins: %s", origins)


def _key_present(value: str | None) -> str:
    return "yes" if value and value.strip() else "no"


logger.info("FRED_API_KEY present: %s", _key_present(settings.fred_api_key))
logger.info("BEA_API_KEY present: %s", _key_present(settings.bea_api_key))
logger.info(
    "Telegram config: enabled=%s, token_present=%s, allowed_user_id=%s",
    settings.telegram_enabled,
    _key_present(settings.telegram_bot_token),
    settings.telegram_allowed_user_id,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "Gaslighting Macro News API is running.",
        "health": "/api/health",
    }


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = perf_counter()
    response = await call_next(request)
    duration_ms = (perf_counter() - start_time) * 1000
    logger.info(
        "%s %s -> %s (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response
app.include_router(api_router)


def _seconds_until_next_quarter() -> float:
    now = datetime.now()
    minute = (now.minute // 15 + 1) * 15
    next_tick = now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minute)
    if minute >= 60:
        next_tick = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return (next_tick - now).total_seconds()


async def _refresh_loop() -> None:
    await asyncio.sleep(_seconds_until_next_quarter())
    while True:
        try:
            await asyncio.gather(
                get_latest_payload(),
                get_prices_payload(),
                get_signals_payload(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Background refresh failed: %s", exc)
        await asyncio.sleep(15 * 60)


@app.on_event("startup")
async def start_scheduler() -> None:
    if not app.state.__dict__.get("refresh_task"):
        app.state.refresh_task = asyncio.create_task(_refresh_loop())
    if not app.state.__dict__.get("bot_app"):
        try:
            app.state.bot_app = await start_bot()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Telegram bot failed to start: %s", exc)


@app.on_event("shutdown")
async def shutdown_services() -> None:
    refresh_task = app.state.__dict__.get("refresh_task")
    if refresh_task:
        refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await refresh_task
    try:
        await stop_bot(app.state.__dict__.get("bot_app"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Telegram bot shutdown failed: %s", exc)
