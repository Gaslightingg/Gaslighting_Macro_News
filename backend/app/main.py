from __future__ import annotations

import logging
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router as api_router
from .utils.cache_db import CacheStore
from .utils.logging import configure_logging
from .utils.settings import get_settings

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Gaslighting Macro News API")
settings = get_settings()
origins = settings.parsed_cors_origins()
CacheStore(settings.cache_db_url)
logger.info("CORS origins: %s", origins)
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
