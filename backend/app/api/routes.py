from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models.schemas import (
    HealthResponse,
    MacroCategoriesResponse,
    MacroLatestResponse,
    MacroResponse,
    MacroSeriesResponse,
    PricesResponse,
    SignalsResponse,
)
from ..services.macro_series_service import (
    clear_cache,
    get_categories_payload,
    get_latest_payload,
    get_series_payload,
)
from ..services.macro_service import get_macro_payload
from ..services.price_service import get_prices_payload
from ..services.signal_service import get_signals_payload

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    return HealthResponse(status="ok", version="1.0.0")


@router.get("/prices", response_model=PricesResponse)
async def prices() -> PricesResponse:
    return await get_prices_payload()


@router.get("/prices/series/{ticker_id}")
async def price_series(ticker_id: str, range: str = "1y") -> dict[str, object]:
    # Placeholder: use stored history only; no upstream fetch to avoid fake data.
    from ..utils.database import MarketDataStore
    from ..utils.settings import get_settings

    settings = get_settings()
    store = MarketDataStore(settings.resolved_database_path())
    latest = {row["symbol"]: row for row in store.load_prices()}
    history = store.load_price_history(ticker_id)
    points = [
        {"date": row["as_of"], "value": row["price"]}
        for row in history
        if row.get("price") is not None
    ]
    status = "live" if points else "unavailable"
    latest_row = latest.get(ticker_id)
    return {
        "ticker_id": ticker_id,
        "name": ticker_id,
        "unit": "USD",
        "source": latest_row["source"] if latest_row else None,
        "expected_frequency": "daily",
        "status": status,
        "points": points,
        "history_points": len(points),
    }


@router.get("/macro", response_model=MacroResponse)
async def macro() -> MacroResponse:
    return await get_macro_payload()


@router.get("/macro/categories", response_model=MacroCategoriesResponse)
async def macro_categories() -> MacroCategoriesResponse:
    return get_categories_payload()


@router.get("/macro/latest", response_model=MacroLatestResponse)
async def macro_latest() -> MacroLatestResponse:
    return await get_latest_payload()


@router.get("/macro/series/{indicator_id}", response_model=MacroSeriesResponse)
async def macro_series(indicator_id: str, range: str = "1y") -> MacroSeriesResponse:
    try:
        return await get_series_payload(indicator_id, range)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/macro/refresh")
def macro_refresh() -> dict[str, str]:
    clear_cache()
    return {"status": "ok"}


@router.get("/macro/series", response_model=MacroSeriesResponse)
async def macro_series_query(indicator: str, range: str = "1y") -> MacroSeriesResponse:
    try:
        return await get_series_payload(indicator, range)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/signals", response_model=SignalsResponse)
async def signals() -> SignalsResponse:
    return await get_signals_payload()
