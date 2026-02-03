from __future__ import annotations

from typing import List

from pydantic import BaseModel


class PriceTicker(BaseModel):
    symbol: str
    price: float
    change_pct: float


class PricesResponse(BaseModel):
    as_of: str
    tickers: List[PriceTicker]


class MacroSeriesItem(BaseModel):
    name: str
    value: str
    change: str
    updated: str


class MacroResponse(BaseModel):
    as_of: str
    series: List[MacroSeriesItem]
    commentary: str


class SignalItem(BaseModel):
    ticker: str
    direction: str
    confidence: float
    reasons: List[str]


class SignalsResponse(BaseModel):
    as_of: str
    disclaimer: str
    signals: List[SignalItem]


class HealthResponse(BaseModel):
    status: str
    version: str
