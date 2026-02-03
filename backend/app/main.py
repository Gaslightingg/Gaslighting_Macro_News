from __future__ import annotations

from fastapi import FastAPI

from .api.routes import router as api_router
from .utils.logging import configure_logging

configure_logging()

app = FastAPI(title="Gaslighting Macro News API")
app.include_router(api_router)
