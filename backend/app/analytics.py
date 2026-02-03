from __future__ import annotations

import re
from dataclasses import dataclass


_NUMBER_RE = re.compile(r"-?\\d+(?:\\.\\d+)?")


def _parse_number(value: str) -> float:
    match = _NUMBER_RE.search(value)
    if not match:
        return 0.0
    return float(match.group(0))


def _parse_change(value: str) -> float:
    if not value:
        return 0.0
    return _parse_number(value)


@dataclass(frozen=True)
class MacroFactors:
    risk_on: float
    usd_strength: float
    rates: float
    inflation: float


def derive_factors(series: list[dict]) -> MacroFactors:
    by_name = {item["name"]: item for item in series}

    pmi = _parse_number(by_name.get("PMI/ISM", {}).get("value", "0"))
    vix = _parse_number(by_name.get("VIX", {}).get("value", "0"))
    nfp = _parse_number(by_name.get("NFP", {}).get("value", "0"))
    unemployment = _parse_number(by_name.get("Unemployment Rate", {}).get("value", "0"))

    risk_on = 0.0
    if pmi >= 50:
        risk_on += 1.0
    if vix and vix < 20:
        risk_on += 1.0
    if nfp > 0:
        risk_on += 1.0
    if unemployment > 4.2:
        risk_on -= 1.0

    dxy_change = _parse_change(by_name.get("DXY", {}).get("change", "0"))
    rates_change = _parse_change(by_name.get("US10Y", {}).get("change", "0"))
    usd_strength = 0.0
    if dxy_change > 0:
        usd_strength += 1.0
    if rates_change > 0:
        usd_strength += 1.0
    if rates_change < 0:
        usd_strength -= 0.5

    rates = 1.0 if rates_change > 0 else -1.0 if rates_change < 0 else 0.0

    cpi = _parse_number(by_name.get("CPI", {}).get("value", "0"))
    core_cpi = _parse_number(by_name.get("Core CPI", {}).get("value", "0"))
    pce = _parse_number(by_name.get("PCE", {}).get("value", "0"))
    inflation = 0.0
    if cpi >= 3:
        inflation += 1.0
    if core_cpi >= 3:
        inflation += 1.0
    if pce >= 2.5:
        inflation += 1.0

    return MacroFactors(
        risk_on=risk_on,
        usd_strength=usd_strength,
        rates=rates,
        inflation=inflation,
    )


def _confidence(score: float) -> float:
    return min(1.0, round(abs(score) / 2.5, 2))


def _direction(score: float) -> str:
    if score >= 0.25:
        return "UP"
    if score <= -0.25:
        return "DOWN"
    return "NEUTRAL"


def _reason_label(factor: str, value: float) -> str:
    if factor == "risk_on":
        return "Risk-on tone from PMI/VIX/NFP" if value > 0 else "Risk-off tone from PMI/VIX/NFP"
    if factor == "usd_strength":
        return "USD strength from DXY and rate spread" if value > 0 else "USD softness from DXY/rates"
    if factor == "rates":
        return "Rates moving higher (US10Y)" if value > 0 else "Rates easing (US10Y)"
    if factor == "inflation":
        return "Inflation pressure elevated" if value > 0 else "Inflation cooling"
    return "Macro backdrop mixed"


def build_signals(series: list[dict], tickers: list[str]) -> list[dict]:
    factors = derive_factors(series)
    weights = {
        "S&P500": {"risk_on": 0.7, "usd_strength": -0.2, "rates": -0.4, "inflation": -0.3},
        "NAS100": {"risk_on": 0.6, "usd_strength": -0.2, "rates": -0.5, "inflation": -0.3},
        "EUR/USD": {"risk_on": 0.2, "usd_strength": -0.8, "rates": -0.3, "inflation": 0.1},
        "GBP/USD": {"risk_on": 0.2, "usd_strength": -0.7, "rates": -0.3, "inflation": 0.1},
        "GBP/JPY": {"risk_on": 0.6, "usd_strength": 0.1, "rates": 0.2, "inflation": 0.1},
        "XAUUSD": {"risk_on": 0.2, "usd_strength": -0.7, "rates": -0.4, "inflation": 0.4},
    }
    factor_values = {
        "risk_on": factors.risk_on,
        "usd_strength": factors.usd_strength,
        "rates": factors.rates,
        "inflation": factors.inflation,
    }

    signals = []
    for ticker in tickers:
        ticker_weights = weights.get(ticker, {})
        score = 0.0
        contributions = []
        for factor, weight in ticker_weights.items():
            value = factor_values[factor]
            contribution = weight * value
            score += contribution
            if value != 0:
                contributions.append(
                    (abs(contribution), _reason_label(factor, value))
                )
        contributions.sort(reverse=True, key=lambda item: item[0])
        reasons = [reason for _, reason in contributions[:3]]
        while len(reasons) < 3:
            reasons.append("Macro backdrop mixed")

        signals.append(
            {
                "ticker": ticker,
                "direction": _direction(score),
                "confidence": _confidence(score),
                "reasons": reasons,
            }
        )

    return signals
