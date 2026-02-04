from __future__ import annotations

from typing import List

from pydantic import BaseModel


class PriceHistoryPoint(BaseModel):
    date: str
    value: float


class PriceHistoryMeta(BaseModel):
    data_start: str | None
    data_end: str | None
    interval: str
    points_count: int


class PriceTicker(BaseModel):
    id: str
    symbol: str
    name: str
    asset_class: str
    unit: str | None
    value: float | None
    change: float | None
    change_pct: float | None
    last_updated: str | None
    source: str | None
    status: str
    quality: str
    error: str | None = None
    history_points: List[PriceHistoryPoint]
    history_meta: PriceHistoryMeta | None


class PricesResponse(BaseModel):
    as_of: str
    tickers: List[PriceTicker]


class PriceHistoryResponse(BaseModel):
    id: str
    symbol: str
    name: str
    asset_class: str
    unit: str | None
    source: str | None
    status: str
    quality: str
    points: List[PriceHistoryPoint]
    history_meta: PriceHistoryMeta


class MacroSeriesItem(BaseModel):
    name: str
    value: str
    change: str
    updated: str


class MacroResponse(BaseModel):
    as_of: str
    series: List[MacroSeriesItem]
    commentary: str


class MacroIndicatorMeta(BaseModel):
    id: str
    name: str
    category: str
    frequency: str
    units: str
    source: str
    why_it_matters: str
    asset_impact: str
    direction_hint: str


class MacroCategory(BaseModel):
    id: str
    name: str
    indicators: List[MacroIndicatorMeta]


class MacroCategoriesResponse(BaseModel):
    categories: List[MacroCategory]


class MacroLatestItem(BaseModel):
    indicator_id: str
    name: str
    value: float | None
    change: float | None
    unit: str | None
    last_updated: str | None
    category: str
    status: str
    source: str | None
    history_points: int
    expected_frequency: str
    stale_after_seconds: int
    quality: str
    error: str | None = None


class MacroLatestResponse(BaseModel):
    as_of: str
    latest: List[MacroLatestItem]


class MacroSeriesPoint(BaseModel):
    date: str
    value: float


class MacroSeriesResponse(BaseModel):
    indicator_id: str
    name: str
    category: str
    unit: str | None
    last_updated: str | None
    status: str
    source: str | None
    expected_frequency: str
    stale_after_seconds: int
    quality: str
    points: List[MacroSeriesPoint]
    history_points: int
    error: str | None = None


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
