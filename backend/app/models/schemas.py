from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, model_validator


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


class FactorContributor(BaseModel):
    indicator: str
    contribution: float
    zscore: float
    delta: float


class FactorSnapshot(BaseModel):
    score: float | None
    contributors: List[FactorContributor]
    coverage: float


class FactorsSnapshot(BaseModel):
    updated_at: str
    factors: dict[str, FactorSnapshot]
    cacheStatus: str


class SignalDebug(BaseModel):
    score: float
    factorScores: dict[str, float | None]
    missingInputs: List[str]
    cacheStatus: str
    migrated: bool | None = None


class SignalCard(BaseModel):
    ticker: str
    signal: str
    confidence: int
    long_pct: int | None = None
    short_pct: int | None = None
    direction_label: str | None = None
    bias: str | None = None
    bullets: List[str]
    updated_at: str
    debug: SignalDebug

    @model_validator(mode="after")
    def _fill_directional_defaults(self) -> "SignalCard":
        long_pct = 50 if self.long_pct is None else int(self.long_pct)
        short_pct = 50 if self.short_pct is None else int(self.short_pct)
        if long_pct + short_pct != 100:
            total = max(1, long_pct + short_pct)
            long_pct = int(round((long_pct / total) * 100))
            short_pct = 100 - long_pct
        long_pct = max(0, min(100, long_pct))
        short_pct = 100 - long_pct

        self.long_pct = long_pct
        self.short_pct = short_pct
        if not self.direction_label:
            self.direction_label = f"{long_pct}% long / {short_pct}% short"
        if not self.bias:
            self.bias = "LONG" if long_pct > 55 else "SHORT" if long_pct < 45 else "FLAT"
        return self


class SignalsApiResponse(BaseModel):
    updated_at: str
    signals: List[SignalCard]
    errors: List[str]


class BacktestRequest(BaseModel):
    tickers: List[str]
    start_date: str
    end_date: str
    interval_days: int = Field(default=1, ge=1)
    model: str | None = Field(default="signal_engine")


class BacktestMetric(BaseModel):
    cumulative_return: float
    max_drawdown: float
    win_rate: float
    trades: int
    avg_trade_return: float
    sharpe: float | None


class BacktestTrade(BaseModel):
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    pnl: float
    return_pct: float
    direction: str


class BacktestSeriesPoint(BaseModel):
    date: str
    value: float
    signal: str | None = None
    position: int | None = None


class BacktestResult(BaseModel):
    ticker: str
    start_date: str
    end_date: str
    metrics: BacktestMetric | None
    trades: List[BacktestTrade]
    equity_curve: List[BacktestSeriesPoint]
    status: str
    error: str | None = None
    cached: bool = False


class BacktestResponse(BaseModel):
    results: List[BacktestResult]


class SignalsDebugTicker(BaseModel):
    ticker: str
    signal: str
    confidence: int
    missing_inputs: List[str]
    reason: str | None = None


class SignalsDebugResponse(BaseModel):
    updated_at: str
    tickers: List[str]
    indicator_errors: dict[str, str]
    provider_errors: List[str]
    tickers_debug: List[SignalsDebugTicker]


class RecomputeResponse(BaseModel):
    ok: bool
    updated_at: str


# Backward-compatible legacy signal schema (kept for internal imports/tests).
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


class NewsImpactWindow(BaseModel):
    direction: str
    move: float | None
    window: str
    data_quality: str
    reason: str | None = None


class NewsEventItem(BaseModel):
    id: str
    source: str
    title: str
    country: str
    importance: str
    datetime_utc: str
    datetime_local: str
    unit: str | None = None
    previous: str | None = None
    forecast: str | None = None
    actual: str | None = None
    revised: str | None = None
    status: str
    updated_at: str
    surprise: float | None = None
    surprise_pct: float | None = None
    impacts: dict[str, dict[str, NewsImpactWindow]] = Field(default_factory=dict)


class NewsResponse(BaseModel):
    updated_at: str
    provider_status: str
    events: List[NewsEventItem]
    debug: dict | None = None


class NewsSyncResponse(BaseModel):
    ok: bool
    created: int
    updated: int
    impacts_computed: int
