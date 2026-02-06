from __future__ import annotations

import re
from datetime import datetime


def slugify(symbol: str) -> str:
    mapping = {
        "S&P500": "sp500",
        "NAS100": "nas100",
        "NASDAQ mini": "nqmini",
    }
    if symbol in mapping:
        return mapping[symbol]
    return re.sub(r"[^a-zA-Z0-9]+", "", symbol).lower()


def infer_asset_class(symbol: str) -> str:
    if symbol in {"S&P500", "NAS100", "NASDAQ mini"}:
        return "index"
    if symbol == "XAUUSD":
        return "commodity"
    if "/" in symbol:
        return "fx"
    return "other"


def infer_unit(asset_class: str) -> str | None:
    if asset_class == "index":
        return "pts"
    if asset_class == "commodity":
        return "USD"
    if asset_class == "fx":
        return "USD"
    return None


def normalize_price_ticker(
    raw: dict,
    *,
    now: datetime,
    source: str | None,
    status: str,
    error: str | None = None,
    error_reason: str | None = None,
    tried_sources: list[str] | None = None,
) -> dict:
    symbol = raw.get("symbol") or raw.get("name") or raw.get("id") or "Unknown"
    name = raw.get("name") or symbol
    asset_class = raw.get("asset_class") or infer_asset_class(symbol)
    value = raw.get("price")
    if value is None:
        value = raw.get("value")
    change_pct = raw.get("change_pct")
    change = raw.get("change")
    if change_pct is None and change is not None:
        change_pct = change
    if change is None and change_pct is not None:
        change = change_pct
    unit = raw.get("unit") or infer_unit(asset_class)
    history_points = raw.get("history_points") or []
    if isinstance(history_points, int):
        history_points = []
    history_meta = raw.get("history_meta")
    return {
        "id": raw.get("id") or slugify(symbol),
        "symbol": symbol,
        "name": name,
        "asset_class": asset_class,
        "value": value,
        "change": change,
        "change_pct": change_pct,
        "unit": unit,
        "last_updated": raw.get("last_updated") or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": raw.get("as_of") or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "source": source,
        "quality": "high" if status == "live" else "low",
        "error": error,
        "error_reason": error_reason,
        "tried_sources": tried_sources or [],
        "stale": status in {"stale", "cached"},
        "history_points": history_points,
        "history_meta": history_meta,
    }
