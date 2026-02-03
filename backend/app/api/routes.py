from __future__ import annotations

from fastapi import APIRouter

from ..models.schemas import HealthResponse, MacroResponse, PricesResponse, SignalsResponse
from ..services.macro_service import get_macro_payload
from ..services.price_service import get_prices_payload
from ..services.signal_service import get_signals_payload

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    return HealthResponse(status="ok", version="1.0.0")


@router.get("/prices", response_model=PricesResponse)
def prices() -> PricesResponse:
    return get_prices_payload()


@router.get("/macro", response_model=MacroResponse)
def macro() -> MacroResponse:
    return get_macro_payload()


@router.get("/signals", response_model=SignalsResponse)
def signals() -> SignalsResponse:
    return get_signals_payload()
