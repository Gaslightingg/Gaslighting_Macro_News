from __future__ import annotations

import asyncio
import math
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, pstdev
from typing import Protocol

from ..models.schemas import (
    FactorContributor,
    FactorSnapshot,
    FactorsSnapshot,
    SignalCard,
    SignalDebug,
    SignalsDebugResponse,
    SignalsDebugTicker,
)
from ..services.macro_catalog import ALL_CATEGORIES, all_indicators, find_indicator
from ..services.macro_series_service import get_series_payload
from ..services.price_catalog import PRICE_TICKERS
from ..utils.cache_db import CacheStore
from ..utils.settings import get_settings

logger = logging.getLogger(__name__)

SIGNALS_CACHE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class EngineConfig:
    z_max: float = 2.5
    score_max: float = 1.0
    signal_threshold: float = 0.25
    rolling_years: int = 3
    default_interval_days: int = 1
    ttl_seconds: int = 12 * 3600
    directional_k: float = 4.0
    flat_bias_threshold: int = 5
    delta_by_frequency: dict[str, int] | None = None
    indicator_polarity: dict[str, int] | None = None
    factor_definitions: dict[str, list[tuple[str, float]]] | None = None
    factor_weights: dict[str, float] | None = None
    exposures_by_ticker: dict[str, dict[str, float]] | None = None


def _build_factor_definitions() -> dict[str, list[tuple[str, float]]]:
    base: dict[str, list[tuple[str, float]]] = {
        "laborFactor": [
            ("unemployment", 0.35),
            ("jobless_claims", 0.25),
            ("continuing_claims", 0.2),
            ("jolts", 0.2),
        ],
        "volFactor": [("vix", 0.7), ("vvix", 0.2), ("move", 0.1)],
        "ratesFactor": [("us10y", 0.45), ("us2y", 0.25), ("fed_funds", 0.2), ("real_yield_10y", 0.1)],
        "inflationFactor": [("cpi", 0.4), ("core_cpi", 0.35), ("pce", 0.25)],
        "usdFactor": [("dxy", 0.8), ("us10y", 0.2)],
    }
    used_indicators = {indicator for defs in base.values() for indicator, _ in defs}
    for category in ALL_CATEGORIES:
        factor_name = category.id
        if factor_name in base:
            continue
        indicators = [item for item in category.indicators if item.fred_series and item.id not in used_indicators]
        if not indicators:
            continue
        weight = 1 / len(indicators)
        base[factor_name] = [(item.id, weight) for item in indicators]
        used_indicators.update(item.id for item in indicators)
    return base


def _build_factor_weights(factor_defs: dict[str, list[tuple[str, float]]]) -> dict[str, float]:
    if not factor_defs:
        return {}
    weight = 1 / len(factor_defs)
    return {name: weight for name in factor_defs}


def _default_config() -> EngineConfig:
    factor_defs = _build_factor_definitions()
    return EngineConfig(
        delta_by_frequency={"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 90},
        indicator_polarity={
            "unemployment": -1,
            "jobless_claims": -1,
            "continuing_claims": -1,
            "jolts": 1,
            "vix": -1,
            "vvix": -1,
            "move": -1,
            "us10y": -1,
            "us2y": -1,
            "fed_funds": -1,
            "real_yield_10y": -1,
            "cpi": -1,
            "core_cpi": -1,
            "pce": -1,
            "dxy": 1,
        },
        factor_definitions=factor_defs,
        factor_weights=_build_factor_weights(factor_defs),
        exposures_by_ticker={
            "sp500": {"laborFactor": 0.35, "volFactor": 0.35, "ratesFactor": 0.2, "inflationFactor": 0.1},
            "nas100": {"laborFactor": 0.3, "volFactor": 0.3, "ratesFactor": 0.3, "inflationFactor": 0.1},
            "nqmini": {"laborFactor": 0.3, "volFactor": 0.3, "ratesFactor": 0.3, "inflationFactor": 0.1},
            "eurusd": {"usdFactor": -0.7, "volFactor": -0.15, "ratesFactor": 0.1, "laborFactor": 0.05},
            "gbpusd": {"usdFactor": -0.7, "volFactor": -0.1, "ratesFactor": 0.1, "laborFactor": 0.1},
            "gbpjpy": {"volFactor": 0.45, "laborFactor": 0.2, "usdFactor": 0.2, "ratesFactor": 0.15},
            "xauusd": {"volFactor": -0.35, "usdFactor": -0.35, "ratesFactor": -0.2, "inflationFactor": 0.1},
        },
    )


class DataProvider(Protocol):
    async def getSeries(self, indicatorId: str, opts: dict[str, str | None]) -> dict:
        ...

    async def listAvailableIndicators(self) -> list[str]:
        ...


class TickerProvider(Protocol):
    async def listTickers(self) -> list[str]:
        ...


class MacroDataProvider:
    async def getSeries(self, indicatorId: str, opts: dict[str, str | None]) -> dict:
        payload = await get_series_payload(indicatorId, "10y")
        points = [{"t": p.date, "value": p.value} for p in payload.points]
        return {"points": points, "lastUpdated": payload.last_updated, "source": payload.source, "error": payload.error}

    async def listAvailableIndicators(self) -> list[str]:
        return [item.id for item in all_indicators() if item.fred_series]


class SiteTickerProvider:
    async def listTickers(self) -> list[str]:
        return [item.id for item in PRICE_TICKERS]


class SignalEngine:
    def __init__(self, data_provider: DataProvider, ticker_provider: TickerProvider, cache: CacheStore, config: EngineConfig | None = None) -> None:
        self.data_provider = data_provider
        self.ticker_provider = ticker_provider
        self.cache = cache
        self.config = config or _default_config()
        self._last_indicator_errors: dict[str, str] = {}
        self._last_provider_errors: list[str] = []
        self._last_missing_inputs: list[str] = []

    async def computeSignals(self, opts: dict | None = None) -> list[SignalCard]:
        opts = opts or {}
        force_recompute = bool(opts.get("forceRecompute", False))
        cache_key = "signals-engine:final"
        if not force_recompute:
            cached = self.cache.get_cache(cache_key)
            if cached:
                try:
                    cards, migrated = self._load_cached_cards(cached)
                    if migrated:
                        logger.info(
                            "signals cache schema mismatch: cached=%s current=%s; recomputing",
                            self._cached_schema_version(cached),
                            SIGNALS_CACHE_SCHEMA_VERSION,
                        )
                        self._save_signals_cache(cards)
                    return cards
                except Exception as exc:  # noqa: BLE001
                    logger.warning("signals cache invalid; recomputing fresh: %s", exc)

        factors = await self.getFactorsSnapshot({"forceRecompute": force_recompute})
        tickers = await self.ticker_provider.listTickers()
        cards = [await self.computeSignal(t, {"factors": factors, "cacheStatus": "FRESH"}) for t in tickers]
        self._save_signals_cache(cards)
        return cards

    def _save_signals_cache(self, cards: list[SignalCard]) -> None:
        payload = {
            "schema_version": SIGNALS_CACHE_SCHEMA_VERSION,
            "items": [card.model_dump() for card in cards],
        }
        self.cache.set_cache("signals-engine:final", payload, self.config.ttl_seconds)

    def _cached_schema_version(self, cached: object) -> int:
        if isinstance(cached, dict):
            version = cached.get("schema_version")
            if isinstance(version, int):
                return version
        return 1

    def _load_cached_cards(self, cached: object) -> tuple[list[SignalCard], bool]:
        migrated = False
        if isinstance(cached, dict):
            version = cached.get("schema_version", 1)
            raw_items = cached.get("items", [])
            if not isinstance(raw_items, list):
                raise ValueError("cached signals payload has invalid items")
        elif isinstance(cached, list):
            version = 1
            raw_items = cached
        else:
            raise ValueError("cached signals payload has unknown type")

        if version < SIGNALS_CACHE_SCHEMA_VERSION:
            migrated = True
            raw_items = [self._migrate_cached_item(item) for item in raw_items]

        cards: list[SignalCard] = []
        for item in raw_items:
            card = SignalCard(**item)
            card.debug.cacheStatus = "CACHED"
            cards.append(card)
        return cards, migrated

    def _migrate_cached_item(self, item: dict) -> dict:
        if not isinstance(item, dict):
            return self._default_migrated_item("unknown")
        debug = item.get("debug") if isinstance(item.get("debug"), dict) else {}
        score = debug.get("score", item.get("score", 0.0))
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            score_f = 0.0
        confidence = item.get("confidence", 0)
        try:
            confidence_i = int(confidence)
        except (TypeError, ValueError):
            confidence_i = 0

        directional = _directional_split(
            score_f,
            confidence=confidence_i,
            k=self.config.directional_k,
            flat_bias_threshold=self.config.flat_bias_threshold,
        )
        migrated = dict(item)
        migrated["long_pct"] = int(directional["long_pct"])
        migrated["short_pct"] = int(directional["short_pct"])
        migrated["direction_label"] = str(directional["direction_label"])
        migrated["bias"] = str(directional["bias"])
        debug_payload = dict(debug)
        debug_payload["migrated"] = True
        debug_payload.setdefault("cacheStatus", "CACHED")
        debug_payload.setdefault("score", round(score_f, 4))
        debug_payload.setdefault("factorScores", {})
        debug_payload.setdefault("missingInputs", [])
        migrated["debug"] = debug_payload
        migrated.setdefault("bullets", ["Insufficient data: migrated from old cache schema."])
        migrated.setdefault("signal", "NEUTRAL")
        migrated.setdefault("updated_at", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        migrated.setdefault("ticker", "unknown")
        return migrated

    def _default_migrated_item(self, ticker: str) -> dict:
        directional = _directional_split(0.0, confidence=0, k=self.config.directional_k, flat_bias_threshold=self.config.flat_bias_threshold)
        return {
            "ticker": ticker,
            "signal": "NEUTRAL",
            "confidence": 0,
            "long_pct": directional["long_pct"],
            "short_pct": directional["short_pct"],
            "direction_label": directional["direction_label"],
            "bias": directional["bias"],
            "bullets": ["Insufficient data: migrated from old cache schema."],
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "debug": {
                "score": 0.0,
                "factorScores": {},
                "missingInputs": [],
                "cacheStatus": "CACHED",
                "migrated": True,
            },
        }


    async def computeSignal(self, ticker: str, opts: dict | None = None) -> SignalCard:
        opts = opts or {}
        factors: FactorsSnapshot = opts.get("factors") or await self.getFactorsSnapshot({"forceRecompute": bool(opts.get("forceRecompute", False))})
        exposures = (self.config.exposures_by_ticker or {}).get(ticker, {})
        factor_weights = self.config.factor_weights or {}
        explicit_weight_sum = sum(weight for weight in exposures.values() if weight)
        default_factors = [name for name in factors.factors.keys() if name not in exposures]
        default_total = sum(factor_weights.get(name, 0.0) for name in default_factors)
        remaining = max(0.0, 1.0 - explicit_weight_sum)
        default_scale = (remaining / default_total) if default_total > 0 else 0.0

        contributions: list[float] = []
        ticker_score = 0.0
        coverage_values: list[float] = []
        missing_inputs: list[str] = list(self._last_missing_inputs)
        top_contributors: list[tuple[float, str, FactorContributor, float]] = []

        for factor_name, factor in factors.factors.items():
            if factor_name in exposures:
                weight = exposures.get(factor_name, 0.0)
            else:
                weight = factor_weights.get(factor_name, 0.0) * default_scale
            if weight == 0:
                continue
            if factor.score is None:
                missing_inputs.append(f"{factor_name}: no data")
                continue
            contribution = factor.score * weight
            ticker_score += contribution
            contributions.append(contribution)
            coverage_values.append(factor.coverage)
            for contributor in factor.contributors:
                top_contributors.append((abs(contributor.contribution * weight), factor_name, contributor, weight))

        raw_strength = abs(ticker_score)
        data_coverage = mean(coverage_values) if coverage_values else 0.0
        agreement = _agreement(contributions)
        confidence = round(_clamp((raw_strength / self.config.score_max) * 60 + data_coverage * 25 + agreement * 15, 0, 100))

        signal = "NEUTRAL"
        if ticker_score >= self.config.signal_threshold:
            signal = "BULLISH"
        elif ticker_score <= -self.config.signal_threshold:
            signal = "BEARISH"

        directional = _directional_split(
            ticker_score,
            confidence=confidence,
            k=self.config.directional_k,
            flat_bias_threshold=self.config.flat_bias_threshold,
        )
        bullets, top_positive, top_negative = _build_bullets(top_contributors, data_coverage)
        bullets.insert(0, f"Debug: score={ticker_score:+.3f}, p_long_raw={directional['p_long_raw']:.3f}, p_long_adj={directional['p_long_adj']:.3f}, k={self.config.directional_k:.2f}")
        return SignalCard(
            ticker=ticker,
            signal=signal,
            confidence=confidence,
            long_pct=directional["long_pct"],
            short_pct=directional["short_pct"],
            direction_label=directional["direction_label"],
            bias=directional["bias"],
            bullets=bullets,
            updated_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            debug=SignalDebug(
                score=round(ticker_score, 4),
                factorScores={name: f.score for name, f in factors.factors.items()},
                missingInputs=sorted(set(missing_inputs)),
                cacheStatus=opts.get("cacheStatus", "FRESH"),
                factorContributions={name: factors.factors[name].contributors for name in factors.factors},
                topPositiveDrivers=top_positive,
                topNegativeDrivers=top_negative,
            ),
        )

    async def getFactorsSnapshot(self, opts: dict | None = None) -> FactorsSnapshot:
        opts = opts or {}
        force_recompute = bool(opts.get("forceRecompute", False))
        cache_key = "signals-engine:factors"
        if not force_recompute:
            cached = self.cache.get_cache(cache_key)
            if cached:
                data = FactorsSnapshot(**cached)
                data.cacheStatus = "CACHED"
                return data

        available = set(await self.data_provider.listAvailableIndicators())
        factor_defs = self.config.factor_definitions or {}
        needed_indicators = sorted({indicator for defs in factor_defs.values() for indicator, _ in defs if indicator in available})

        self._last_indicator_errors = {}
        self._last_provider_errors = []
        self._last_missing_inputs = []
        settings = get_settings()
        if not settings.fred_api_key:
            self._last_missing_inputs.append("FRED_API_KEY missing")
        if not settings.bea_api_key:
            self._last_missing_inputs.append("BEA_API_KEY missing")
        norm_map: dict[str, dict | None] = {}
        tasks = [self._normalized_indicator(indicator_id, force_recompute) for indicator_id in needed_indicators]
        results = await asyncio.gather(*tasks)
        for indicator_id, result in zip(needed_indicators, results):
            normalized = result.get("data")
            reason = result.get("reason")
            norm_map[indicator_id] = normalized
            if reason:
                self._last_indicator_errors[indicator_id] = reason
                self._last_missing_inputs.append(f"{indicator_id}: {reason}")

        factors: dict[str, FactorSnapshot] = {}
        for factor_name, defs in factor_defs.items():
            total_weight = sum(weight for _, weight in defs)
            used_weight = 0.0
            score_sum = 0.0
            contributors: list[FactorContributor] = []
            for indicator_id, weight in defs:
                normalized = norm_map.get(indicator_id)
                if not normalized:
                    continue
                used_weight += weight
                contribution = normalized["normalizedScore"] * weight
                score_sum += contribution
                contributors.append(
                    FactorContributor(
                        indicator=indicator_id,
                        contribution=round(contribution, 4),
                        zscore=round(normalized["zscore"], 4),
                        delta=round(normalized["delta"], 4),
                        weight=weight,
                        factor=factor_name,
                    )
                )
            coverage = used_weight / total_weight if total_weight > 0 else 0.0
            factor_score = score_sum / used_weight if used_weight > 0 else None
            factors[factor_name] = FactorSnapshot(
                score=round(factor_score, 4) if factor_score is not None else None,
                contributors=sorted(contributors, key=lambda x: abs(x.contribution), reverse=True),
                coverage=round(_clamp(coverage, 0, 1), 4),
            )

        snapshot = FactorsSnapshot(
            updated_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            factors=factors,
            cacheStatus="FRESH",
        )
        self.cache.set_cache(cache_key, snapshot.model_dump(), self.config.ttl_seconds)
        return snapshot

    async def getDebugSnapshot(self) -> SignalsDebugResponse:
        cards = await self.computeSignals({"forceRecompute": False})
        tickers = await self.ticker_provider.listTickers()
        tickers_debug = [
            SignalsDebugTicker(
                ticker=card.ticker,
                signal=card.signal,
                confidence=card.confidence,
                missing_inputs=card.debug.missingInputs,
                reason=("Insufficient data" if card.confidence <= 20 else None),
            )
            for card in cards
        ]
        return SignalsDebugResponse(
            updated_at=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            tickers=tickers,
            indicator_errors=dict(self._last_indicator_errors),
            provider_errors=list(self._last_provider_errors),
            tickers_debug=tickers_debug,
        )

    def get_missing_inputs(self) -> list[str]:
        return list(self._last_missing_inputs)

    async def _normalized_indicator(self, indicator_id: str, force_recompute: bool) -> dict:
        cache_key = f"signals-engine:norm:{indicator_id}"
        if not force_recompute:
            cached = self.cache.get_cache(cache_key)
            if cached:
                return {"data": cached, "reason": None}

        raw = await self._raw_series(indicator_id, force_recompute)
        points = raw.get("points", []) if raw else []
        if len(points) < 3:
            return {"data": None, "reason": raw.get("error") or "not enough points"}

        ordered = sorted(points, key=lambda item: item["t"])
        latest = ordered[-1]
        frequency = (find_indicator(indicator_id).frequency if find_indicator(indicator_id) else "daily")
        delta_days = (self.config.delta_by_frequency or {}).get(frequency, self.config.default_interval_days)
        prev_value = _closest_past_value(ordered, latest["t"], delta_days)
        if prev_value is None:
            return {"data": None, "reason": "no prior point for delta window"}

        roll_start = datetime.strptime(latest["t"], "%Y-%m-%d") - timedelta(days=365 * self.config.rolling_years)
        roll_values = [item["value"] for item in ordered if datetime.strptime(item["t"], "%Y-%m-%d") >= roll_start]
        if len(roll_values) < 5:
            return {"data": None, "reason": "insufficient rolling window"}

        mu = mean(roll_values)
        sigma = pstdev(roll_values)
        if sigma == 0:
            return {"data": None, "reason": "zero variance"}
        zscore = (latest["value"] - mu) / sigma
        polarity = (self.config.indicator_polarity or {}).get(indicator_id, 1)
        normalized_score = _clamp(zscore / self.config.z_max, -1, 1) * polarity

        normalized = {
            "latest": latest["value"],
            "delta": latest["value"] - prev_value,
            "zscore": zscore,
            "normalizedScore": normalized_score,
            "lastUpdated": latest["t"],
            "source": raw.get("source"),
        }
        self.cache.set_cache(cache_key, normalized, self.config.ttl_seconds)
        return {"data": normalized, "reason": None}


    async def _raw_series(self, indicator_id: str, force_recompute: bool) -> dict:
        cache_key = f"signals-engine:raw:{indicator_id}"
        if not force_recompute:
            cached = self.cache.get_cache(cache_key)
            if cached:
                return cached

        end = datetime.utcnow().date().isoformat()
        start = (datetime.utcnow() - timedelta(days=365 * 10)).date().isoformat()
        try:
            data = await self.data_provider.getSeries(indicator_id, {"start": start, "end": end, "interval": "auto"})
        except Exception as exc:  # noqa: BLE001
            self._last_provider_errors.append(f"{indicator_id}: {exc}")
            data = {"points": [], "lastUpdated": None, "source": None}
        self.cache.set_cache(cache_key, data, self.config.ttl_seconds)
        return data


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _closest_past_value(points: list[dict], latest_date: str, delta_days: int) -> float | None:
    latest_dt = datetime.strptime(latest_date, "%Y-%m-%d")
    target = latest_dt - timedelta(days=delta_days)
    candidates = [p for p in points if datetime.strptime(p["t"], "%Y-%m-%d") <= target]
    if not candidates:
        return None
    return candidates[-1]["value"]


def _agreement(contributions: list[float]) -> float:
    if not contributions:
        return 0.0
    if len(contributions) == 1:
        return 1.0
    var = pstdev(contributions) ** 2
    return _clamp(1 - _clamp(var, 0, 1), 0, 1)



def _directional_split(score: float, confidence: int, k: float, flat_bias_threshold: int) -> dict[str, float | int | str]:
    p_long_raw = 1.0 / (1.0 + math.exp(-k * score))
    strength = _clamp(confidence / 100.0, 0.0, 1.0)
    p_long_adj = 0.5 + (p_long_raw - 0.5) * strength
    long_pct = int(round(_clamp(p_long_adj, 0.0, 1.0) * 100))
    short_pct = 100 - long_pct
    bias = "FLAT"
    if long_pct >= 50 + flat_bias_threshold:
        bias = "LONG"
    elif long_pct <= 50 - flat_bias_threshold:
        bias = "SHORT"
    return {
        "p_long_raw": p_long_raw,
        "p_long_adj": p_long_adj,
        "long_pct": long_pct,
        "short_pct": short_pct,
        "direction_label": f"{long_pct}% long / {short_pct}% short",
        "bias": bias,
    }

def _build_bullets(top_contributors: list[tuple[float, str, FactorContributor, float]], data_coverage: float) -> tuple[list[str], list[str], list[str]]:
    bullets: list[str] = []
    ranked = sorted(top_contributors, key=lambda item: item[0], reverse=True)
    signed_ranked = sorted(
        top_contributors,
        key=lambda item: (item[2].contribution * item[3]),
        reverse=True,
    )
    positive = [
        f"{contributor.indicator} via {factor_name}"
        for _, factor_name, contributor, exposure in signed_ranked
        if contributor.contribution * exposure > 0
    ][:2]
    negative = [
        f"{contributor.indicator} via {factor_name}"
        for _, factor_name, contributor, exposure in reversed(signed_ranked)
        if contributor.contribution * exposure < 0
    ][:2]
    if positive:
        bullets.append(f"Top +: {', '.join(positive)}")
    if negative:
        bullets.append(f"Top -: {', '.join(negative)}")
    for _, factor_name, contributor, exposure in ranked[:5]:
        direction = "supports upside" if contributor.contribution * exposure >= 0 else "adds downside pressure"
        bullets.append(
            f"{contributor.indicator} Δ={contributor.delta:+.2f}, z={contributor.zscore:+.2f} via {factor_name} {direction}."
        )
    if data_coverage < 0.6:
        bullets.append("Insufficient macro coverage; signal confidence reduced.")
    if not bullets:
        bullets.append("Macro inputs are mixed; no dominant factor.")
    return bullets[:5], positive, negative
