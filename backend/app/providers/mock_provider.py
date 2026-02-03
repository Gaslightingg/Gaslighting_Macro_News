from __future__ import annotations

from datetime import datetime, timezone
from math import sin

from ..models.schemas import MacroResponse, PricesResponse
from .base import MarketDataProvider


def _hour_seed() -> int:
    now = datetime.now(timezone.utc)
    return now.year * 10_000 + now.month * 100 + now.day * 10 + now.hour


def _jitter(value: float, seed: int, magnitude: float) -> float:
    return value + sin(seed) * magnitude


def _format_pct(value: float) -> str:
    return f"{value:.2f}%"


def _format_pp(value: float) -> str:
    return f"{value:+.2f}pp"


def _format_k(value: float) -> str:
    return f"{value:+.0f}k"


class MockMarketDataProvider(MarketDataProvider):
    """Mock provider used for local development.

    Replace with a real provider that fetches data from services like FRED,
    market data APIs, or internal data warehouses.
    """

    async def get_prices(self) -> PricesResponse:
        seed = _hour_seed()
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return PricesResponse(
            as_of=now_iso,
            tickers=[
                {
                    "symbol": "S&P500",
                    "price": round(_jitter(5041.3, seed + 1, 18.0), 2),
                    "change_pct": round(_jitter(0.62, seed + 2, 0.25), 2),
                },
                {
                    "symbol": "NAS100",
                    "price": round(_jitter(17812.9, seed + 3, 45.0), 2),
                    "change_pct": round(_jitter(1.08, seed + 4, 0.35), 2),
                },
                {
                    "symbol": "NASDAQ mini",
                    "price": round(_jitter(17925.5, seed + 5, 52.0), 2),
                    "change_pct": round(_jitter(0.94, seed + 6, 0.3), 2),
                },
                {
                    "symbol": "EUR/USD",
                    "price": round(_jitter(1.0824, seed + 7, 0.004), 4),
                    "change_pct": round(_jitter(-0.18, seed + 8, 0.2), 2),
                },
                {
                    "symbol": "GBP/USD",
                    "price": round(_jitter(1.2689, seed + 9, 0.0045), 4),
                    "change_pct": round(_jitter(0.24, seed + 10, 0.2), 2),
                },
                {
                    "symbol": "GBP/JPY",
                    "price": round(_jitter(191.42, seed + 11, 0.6), 2),
                    "change_pct": round(_jitter(-0.41, seed + 12, 0.25), 2),
                },
                {
                    "symbol": "XAUUSD",
                    "price": round(_jitter(2034.7, seed + 13, 6.0), 2),
                    "change_pct": round(_jitter(0.73, seed + 14, 0.3), 2),
                },
            ],
        )

    async def get_macro(self) -> MacroResponse:
        seed = _hour_seed()
        today = datetime.now(timezone.utc).date().isoformat()
        return MacroResponse(
            as_of=today,
            series=[
                {
                    "name": "CPI",
                    "value": _format_pct(_jitter(3.1, seed + 20, 0.08)),
                    "change": _format_pp(_jitter(-0.1, seed + 21, 0.05)),
                    "updated": today,
                },
                {
                    "name": "Core CPI",
                    "value": _format_pct(_jitter(3.3, seed + 22, 0.08)),
                    "change": _format_pp(_jitter(0.0, seed + 23, 0.05)),
                    "updated": today,
                },
                {
                    "name": "PCE",
                    "value": _format_pct(_jitter(2.8, seed + 24, 0.06)),
                    "change": _format_pp(_jitter(-0.1, seed + 25, 0.05)),
                    "updated": today,
                },
                {
                    "name": "PMI/ISM",
                    "value": f"{_jitter(52.4, seed + 26, 0.4):.1f}",
                    "change": f"{_jitter(0.6, seed + 27, 0.2):+.1f}",
                    "updated": today,
                },
                {
                    "name": "NFP",
                    "value": _format_k(_jitter(198, seed + 28, 12.0)),
                    "change": _format_k(_jitter(-31, seed + 29, 8.0)),
                    "updated": today,
                },
                {
                    "name": "Unemployment Rate",
                    "value": _format_pct(_jitter(3.9, seed + 30, 0.05)),
                    "change": _format_pp(_jitter(0.1, seed + 31, 0.04)),
                    "updated": today,
                },
                {
                    "name": "Jobless Claims",
                    "value": _format_k(_jitter(212, seed + 32, 10.0)),
                    "change": _format_k(_jitter(-8, seed + 33, 6.0)),
                    "updated": today,
                },
                {
                    "name": "Fed Funds Rate",
                    "value": "5.25-5.50%",
                    "change": _format_pp(_jitter(0.0, seed + 34, 0.02)),
                    "updated": today,
                },
                {
                    "name": "US10Y",
                    "value": _format_pct(_jitter(4.18, seed + 35, 0.08)),
                    "change": _format_pp(_jitter(-0.03, seed + 36, 0.05)),
                    "updated": today,
                },
                {
                    "name": "US2Y",
                    "value": _format_pct(_jitter(4.59, seed + 37, 0.08)),
                    "change": _format_pp(_jitter(-0.02, seed + 38, 0.05)),
                    "updated": today,
                },
                {
                    "name": "US30Y",
                    "value": _format_pct(_jitter(4.33, seed + 39, 0.08)),
                    "change": _format_pp(_jitter(-0.01, seed + 40, 0.05)),
                    "updated": today,
                },
                {
                    "name": "VIX",
                    "value": f"{_jitter(13.4, seed + 41, 0.8):.1f}",
                    "change": f"{_jitter(-0.8, seed + 42, 0.4):+.1f}",
                    "updated": today,
                },
                {
                    "name": "DXY",
                    "value": f"{_jitter(103.7, seed + 43, 0.6):.1f}",
                    "change": f"{_jitter(-0.3, seed + 44, 0.3):+.1f}",
                    "updated": today,
                },
            ],
            commentary="Inflation continues to cool while growth remains steady.",
        )
