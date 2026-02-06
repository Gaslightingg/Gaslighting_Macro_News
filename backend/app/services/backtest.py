from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import sqrt
from statistics import mean, pstdev
from typing import Iterable

from ..models.schemas import (
    BacktestMetric,
    BacktestRequest,
    BacktestResponse,
    BacktestResult,
    BacktestSeriesPoint,
    BacktestTrade,
    TestsRunCache,
    TestsRunMetric,
    TestsRunPeriod,
    TestsRunRequest,
    TestsRunResponse,
    TestsRunSignal,
)
from ..services.macro_catalog import find_indicator
from ..services.macro_series_service import get_series_payload
from ..services.price_service import get_price_history_payload
from ..services.signal_engine import (
    _agreement,
    _build_bullets,
    _clamp,
    _closest_past_value,
    _default_config,
    _directional_split,
)
from ..services.ai_openai import OpenAIPredictor
from ..services.ai_provider import AiPrediction
from ..services.tests_cache import build_hist_cache_key, get_tests_cache
from ..utils.settings import get_settings


BACKTEST_CACHE_TTL_SECONDS = 5 * 60
DEFAULT_MODEL = "signal_engine"
DEFAULT_SIGNAL = "HOLD"


@dataclass(frozen=True)
class SignalSnapshot:
    signal: str
    score: float
    confidence: int


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date: {value}. Expected YYYY-MM-DD.") from exc


def _resolve_period(now: datetime | None = None) -> tuple[str, str]:
    anchor = now or datetime.utcnow()
    end = anchor.date()
    start = (anchor - timedelta(days=365 * 5)).date()
    return start.isoformat(), end.isoformat()


def _date_range_points(points: list[dict], start: date, end: date) -> list[dict]:
    return [p for p in points if start <= datetime.strptime(p["t"], "%Y-%m-%d").date() <= end]


def _normalized_indicator_at_date(indicator_id: str, points: list[dict], as_of: date, config) -> tuple[dict | None, str | None]:
    if len(points) < 3:
        return None, "not enough points"
    ordered = sorted(points, key=lambda item: item["t"])
    filtered = [p for p in ordered if datetime.strptime(p["t"], "%Y-%m-%d").date() <= as_of]
    if len(filtered) < 3:
        return None, "not enough points"

    latest = filtered[-1]
    indicator = find_indicator(indicator_id)
    frequency = indicator.frequency if indicator else "daily"
    delta_days = (config.delta_by_frequency or {}).get(frequency, config.default_interval_days)
    prev_value = _closest_past_value(filtered, latest["t"], delta_days)
    if prev_value is None:
        return None, "no prior point for delta window"

    roll_start = as_of - timedelta(days=365 * config.rolling_years)
    roll_values = [
        item["value"]
        for item in filtered
        if datetime.strptime(item["t"], "%Y-%m-%d").date() >= roll_start
    ]
    if len(roll_values) < 5:
        return None, "insufficient rolling window"

    mu = mean(roll_values)
    sigma = pstdev(roll_values)
    if sigma == 0:
        return None, "zero variance"
    zscore = (latest["value"] - mu) / sigma
    polarity = (config.indicator_polarity or {}).get(indicator_id, 1)
    normalized_score = _clamp(zscore / config.z_max, -1, 1) * polarity
    normalized = {
        "latest": latest["value"],
        "delta": latest["value"] - prev_value,
        "zscore": zscore,
        "normalizedScore": normalized_score,
        "lastUpdated": latest["t"],
    }
    return normalized, None


def _factors_at_date(as_of: date, indicator_series: dict[str, list[dict]], config) -> dict[str, dict]:
    factor_defs = config.factor_definitions or {}
    factors: dict[str, dict] = {}
    for factor_name, defs in factor_defs.items():
        total_weight = sum(weight for _, weight in defs)
        used_weight = 0.0
        score_sum = 0.0
        contributors = []
        for indicator_id, weight in defs:
            points = indicator_series.get(indicator_id)
            if not points:
                continue
            normalized, _ = _normalized_indicator_at_date(indicator_id, points, as_of, config)
            if not normalized:
                continue
            used_weight += weight
            contribution = normalized["normalizedScore"] * weight
            score_sum += contribution
            contributors.append(
                {
                    "indicator": indicator_id,
                    "contribution": round(contribution, 4),
                    "zscore": round(normalized["zscore"], 4),
                    "delta": round(normalized["delta"], 4),
                }
            )
        coverage = used_weight / total_weight if total_weight > 0 else 0.0
        factor_score = score_sum / used_weight if used_weight > 0 else None
        factors[factor_name] = {
            "score": round(factor_score, 4) if factor_score is not None else None,
            "contributors": contributors,
            "coverage": round(_clamp(coverage, 0, 1), 4),
        }
    return factors


def _signal_at_date(ticker: str, factors: dict[str, dict], config) -> SignalSnapshot:
    exposures = (config.exposures_by_ticker or {}).get(ticker, {})
    contributions: list[float] = []
    ticker_score = 0.0
    coverage_values: list[float] = []
    top_contributors: list[tuple[float, str, dict, float]] = []

    for factor_name, factor in factors.items():
        weight = exposures.get(factor_name, 0.0)
        if weight == 0:
            continue
        if factor["score"] is None:
            continue
        contribution = factor["score"] * weight
        ticker_score += contribution
        contributions.append(contribution)
        coverage_values.append(factor["coverage"])
        for contributor in factor["contributors"]:
            top_contributors.append((abs(contributor["contribution"] * weight), factor_name, contributor, weight))

    raw_strength = abs(ticker_score)
    data_coverage = mean(coverage_values) if coverage_values else 0.0
    agreement = _agreement(contributions)
    confidence = round(_clamp((raw_strength / config.score_max) * 60 + data_coverage * 25 + agreement * 15, 0, 100))

    signal = "NEUTRAL"
    if ticker_score >= config.signal_threshold:
        signal = "BULLISH"
    elif ticker_score <= -config.signal_threshold:
        signal = "BEARISH"

    _directional_split(
        ticker_score,
        confidence=confidence,
        k=config.directional_k,
        flat_bias_threshold=config.flat_bias_threshold,
    )
    _build_bullets(top_contributors, data_coverage)
    return SignalSnapshot(signal=signal, score=round(ticker_score, 4), confidence=confidence)


def _positions_from_signals(signals: Iterable[SignalSnapshot]) -> list[int]:
    positions = []
    for signal in signals:
        if signal.signal == "BULLISH":
            positions.append(1)
        elif signal.signal == "BEARISH":
            positions.append(-1)
        else:
            positions.append(0)
    return positions


def _build_equity_curve(points: list[dict], positions: list[int]) -> list[BacktestSeriesPoint]:
    equity = 1.0
    curve = [BacktestSeriesPoint(date=points[0]["t"], value=equity, signal=None, position=positions[0])]
    for idx in range(1, len(points)):
        prev_price = points[idx - 1]["value"]
        price = points[idx]["value"]
        position = positions[idx - 1]
        if prev_price != 0:
            equity *= 1 + ((price / prev_price) - 1) * position
        curve.append(
            BacktestSeriesPoint(
                date=points[idx]["t"],
                value=round(equity, 6),
                signal=None,
                position=positions[idx],
            )
        )
    return curve


def _signal_to_position(signal: str) -> int:
    if signal == "BUY":
        return 1
    if signal == "SELL":
        return -1
    return 0


def _normalize_signal(signal: str) -> str:
    normalized = signal.upper()
    if normalized in {"BULLISH", "BUY"}:
        return "BUY"
    if normalized in {"BEARISH", "SELL"}:
        return "SELL"
    return DEFAULT_SIGNAL


def _build_trades(points: list[dict], positions: list[int]) -> list[BacktestTrade]:
    trades: list[BacktestTrade] = []
    entry = None
    entry_pos = 0
    for idx in range(1, len(points)):
        prev_pos = positions[idx - 1]
        current_pos = positions[idx]
        if prev_pos == current_pos:
            continue
        if prev_pos != 0 and entry is not None:
            exit_price = points[idx]["value"]
            exit_time = points[idx]["t"]
            pnl = (exit_price - entry["price"]) / entry["price"] if entry["price"] else 0.0
            if entry_pos == -1:
                pnl = (entry["price"] - exit_price) / entry["price"] if entry["price"] else 0.0
            trades.append(
                BacktestTrade(
                    entry_time=entry["time"],
                    entry_price=entry["price"],
                    exit_time=exit_time,
                    exit_price=exit_price,
                    pnl=round(pnl, 6),
                    return_pct=round(pnl * 100, 4),
                    direction="LONG" if entry_pos == 1 else "SHORT",
                )
            )
            entry = None
            entry_pos = 0
        if current_pos != 0:
            entry = {"time": points[idx]["t"], "price": points[idx]["value"]}
            entry_pos = current_pos

    if entry is not None:
        exit_price = points[-1]["value"]
        exit_time = points[-1]["t"]
        pnl = (exit_price - entry["price"]) / entry["price"] if entry["price"] else 0.0
        if entry_pos == -1:
            pnl = (entry["price"] - exit_price) / entry["price"] if entry["price"] else 0.0
        trades.append(
            BacktestTrade(
                entry_time=entry["time"],
                entry_price=entry["price"],
                exit_time=exit_time,
                exit_price=exit_price,
                pnl=round(pnl, 6),
                return_pct=round(pnl * 100, 4),
                direction="LONG" if entry_pos == 1 else "SHORT",
            )
        )
    return trades


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        if value > peak:
            peak = value
        drawdown = (value - peak) / peak if peak else 0.0
        max_dd = min(max_dd, drawdown)
    return abs(max_dd)


def _compute_metrics(trades: list[BacktestTrade], equity_curve: list[BacktestSeriesPoint]) -> BacktestMetric:
    equity_values = [point.value for point in equity_curve]
    cumulative_return = equity_values[-1] - equity_values[0]
    returns = [equity_values[i] / equity_values[i - 1] - 1 for i in range(1, len(equity_values))]
    sharpe = None
    if returns:
        avg = mean(returns)
        std = pstdev(returns)
        sharpe = round((avg / std) * sqrt(252), 4) if std > 0 else None
    wins = [trade for trade in trades if trade.pnl > 0]
    avg_trade = mean([trade.pnl for trade in trades]) if trades else 0.0
    win_rate = (len(wins) / len(trades)) if trades else 0.0
    return BacktestMetric(
        cumulative_return=round(cumulative_return, 6),
        max_drawdown=round(_max_drawdown(equity_values), 6),
        win_rate=round(win_rate, 4),
        trades=len(trades),
        avg_trade_return=round(avg_trade, 6),
        sharpe=sharpe,
    )


def _tests_metrics(trades: list[BacktestTrade], equity_curve: list[BacktestSeriesPoint]) -> TestsRunMetric:
    if not equity_curve:
        return TestsRunMetric(cumulative_return=0.0, max_drawdown=0.0, trades_count=len(trades))
    equity_values = [point.value for point in equity_curve]
    cumulative_return = equity_values[-1] - equity_values[0]
    max_dd = _max_drawdown(equity_values)
    return TestsRunMetric(
        cumulative_return=round(cumulative_return, 6),
        max_drawdown=round(max_dd, 6),
        trades_count=len(trades),
    )


def _ai_provider_from_settings() -> OpenAIPredictor | None:
    settings = get_settings()
    if not settings.tests_ai_enabled:
        return None
    if settings.tests_ai_provider != "openai":
        return None
    if not settings.tests_ai_api_key:
        return None
    return OpenAIPredictor(settings.tests_ai_api_key, settings.tests_ai_model)


async def run_backtest(request: BacktestRequest) -> BacktestResponse:
    config = _default_config()
    start = _parse_date(request.start_date)
    end = _parse_date(request.end_date)
    if start >= end:
        raise ValueError("start_date must be before end_date")
    if request.interval_days < 1:
        raise ValueError("interval_days must be >= 1")
    model = request.model or DEFAULT_MODEL

    results: list[BacktestResult] = []
    for ticker in request.tickers:
        cache_key = f"backtest:{ticker}:{request.start_date}:{request.end_date}:{request.interval_days}:{model}"
        cache = get_tests_cache()
        cached = cache.get(cache_key)
        if cached:
            results.append(BacktestResult(**cached, cached=True))
            continue

        price_payload = await get_price_history_payload(ticker, "max")
        price_points = [{"t": p.date, "value": p.value} for p in price_payload.points]
        points = _date_range_points(price_points, start, end)
        if len(points) < 2:
            results.append(
                BacktestResult(
                    ticker=ticker,
                    start_date=request.start_date,
                    end_date=request.end_date,
                    metrics=None,
                    trades=[],
                    equity_curve=[],
                    status="error",
                    error="Not enough price data for selected period.",
                    cached=False,
                )
            )
            continue

        if request.interval_days > 1:
            interval_points = []
            last_date = None
            for point in points:
                point_date = datetime.strptime(point["t"], "%Y-%m-%d").date()
                if last_date is None or (point_date - last_date).days >= request.interval_days:
                    interval_points.append(point)
                    last_date = point_date
            points = interval_points

        indicator_ids = {indicator for defs in (config.factor_definitions or {}).values() for indicator, _ in defs}
        indicator_series: dict[str, list[dict]] = {}
        for indicator_id in indicator_ids:
            payload = await get_series_payload(indicator_id, "10y")
            indicator_series[indicator_id] = [{"t": p.date, "value": p.value} for p in payload.points]

        signals: list[SignalSnapshot] = []
        for point in points:
            as_of = datetime.strptime(point["t"], "%Y-%m-%d").date()
            factors = _factors_at_date(as_of, indicator_series, config)
            signals.append(_signal_at_date(ticker, factors, config))

        positions = _positions_from_signals(signals)
        equity_curve = _build_equity_curve(points, positions)
        trades = _build_trades(points, positions)
        metrics = _compute_metrics(trades, equity_curve)

        result = BacktestResult(
            ticker=ticker,
            start_date=request.start_date,
            end_date=request.end_date,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            status="ok",
            error=None,
            cached=False,
        )
        cache.set(cache_key, result.model_dump())
        results.append(result)

    return BacktestResponse(results=results)


async def run_tests(request: TestsRunRequest) -> TestsRunResponse:
    config = _default_config()
    ticker = request.ticker
    start_date, end_date = _resolve_period()
    cache = get_tests_cache()
    cache_key = build_hist_cache_key(ticker, request.interval_days, start_date, end_date)
    hist_cached = cache.get(cache_key)
    cache_hit = hist_cached is not None

    if hist_cached is None:
        try:
            price_payload = await get_price_history_payload(ticker, "max")
        except ValueError as exc:
            return TestsRunResponse(
                period=TestsRunPeriod(start=start_date, end=end_date),
                cache=TestsRunCache(hit=False, key=cache_key),
                status="error",
                metrics=None,
                signals=[],
                trades=[],
                equity_curve=[],
                error=str(exc),
            )
        price_points = [{"t": p.date, "value": p.value} for p in price_payload.points]
        points = _date_range_points(price_points, _parse_date(start_date), _parse_date(end_date))
        if len(points) < 2:
            return TestsRunResponse(
                period=TestsRunPeriod(start=start_date, end=end_date),
                cache=TestsRunCache(hit=False, key=cache_key),
                status="error",
                metrics=None,
                signals=[],
                trades=[],
                equity_curve=[],
                error="Not enough price data for selected period.",
            )

        if request.interval_days > 1:
            interval_points = []
            last_date = None
            for point in points:
                point_date = datetime.strptime(point["t"], "%Y-%m-%d").date()
                if last_date is None or (point_date - last_date).days >= request.interval_days:
                    interval_points.append(point)
                    last_date = point_date
            points = interval_points

        indicator_ids = {indicator for defs in (config.factor_definitions or {}).values() for indicator, _ in defs}
        indicator_series: dict[str, list[dict]] = {}
        for indicator_id in indicator_ids:
            payload = await get_series_payload(indicator_id, "10y")
            indicator_series[indicator_id] = [{"t": p.date, "value": p.value} for p in payload.points]

        hist_cached = {"price_points": points, "indicator_series": indicator_series}
        cache.set(cache_key, hist_cached)

    points = hist_cached["price_points"]
    indicator_series = hist_cached["indicator_series"]

    ai_provider = _ai_provider_from_settings()
    signals: list[TestsRunSignal] = []
    positions: list[int] = []
    for point in points:
        as_of = datetime.strptime(point["t"], "%Y-%m-%d").date()
        factors = _factors_at_date(as_of, indicator_series, config)
        base_signal = _signal_at_date(ticker, factors, config)
        signal_value = _normalize_signal(base_signal.signal)
        confidence = base_signal.confidence / 100.0
        notes = None
        ai_prediction: AiPrediction | None = None

        if ai_provider:
            context = {
                "ticker": ticker,
                "date": point["t"],
                "signal": base_signal.signal,
                "score": base_signal.score,
                "confidence": base_signal.confidence,
            }
            ai_prediction = await ai_provider.predict(context)
        if ai_prediction:
            signal_value = _normalize_signal(ai_prediction.signal)
            confidence = ai_prediction.confidence
            notes = ai_prediction.notes

        signals.append(TestsRunSignal(date=point["t"], signal=signal_value, confidence=confidence, notes=notes))
        positions.append(_signal_to_position(signal_value))

    equity_curve = _build_equity_curve(points, positions)
    trades = _build_trades(points, positions)
    metrics = _tests_metrics(trades, equity_curve)

    return TestsRunResponse(
        period=TestsRunPeriod(start=start_date, end=end_date),
        cache=TestsRunCache(hit=cache_hit, key=cache_key),
        status="success",
        metrics=metrics,
        signals=signals,
        trades=trades,
        equity_curve=equity_curve,
        error=None,
    )
