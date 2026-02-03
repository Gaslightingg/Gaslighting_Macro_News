from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router as api_router
from .utils.logging import configure_logging
from .utils.settings import get_settings

configure_logging()

app = FastAPI(title="Gaslighting Macro News API")
settings = get_settings()
origins = settings.parsed_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)
