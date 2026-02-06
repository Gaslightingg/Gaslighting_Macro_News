from __future__ import annotations

from typing import Iterable


def _fmt_value(value: float | None, unit: str | None = None) -> str:
    if value is None:
        return "—"
    if unit:
        return f"{value:.2f} {unit}"
    return f"{value:.2f}"


def format_prices(prices: Iterable[dict]) -> str:
    lines = ["📈 *Main overview*"]
    for ticker in prices:
        change = ticker.get("change")
        change_str = f"{change:+.2f}%" if isinstance(change, (int, float)) else "—"
        lines.append(
            f"*{ticker.get('symbol') or ticker.get('name')}*: {_fmt_value(ticker.get('value'), ticker.get('unit'))} ({change_str})"
        )
    return "\n".join(lines)


def format_signals(signals: Iterable[dict]) -> str:
    lines = ["🧭 *Signals*"]
    for signal in signals:
        bias = (signal.get("bias") or signal.get("signal") or "FLAT").upper()
        confidence = signal.get("confidence")
        conf_str = f"{confidence}%" if confidence is not None else "—"
        drivers = signal.get("bullets") or []
        driver_text = "; ".join(drivers[:3]) if drivers else "No dominant drivers."
        lines.append(f"*{signal.get('ticker')}*: {bias} ({conf_str})")
        lines.append(f"_{driver_text}_")
    return "\n".join(lines)


def format_signal_detail(signal: dict) -> str:
    bias = (signal.get("bias") or signal.get("signal") or "FLAT").upper()
    confidence = signal.get("confidence")
    bullets = signal.get("bullets") or []
    debug = signal.get("debug") or {}
    top_pos = debug.get("topPositiveDrivers") or []
    top_neg = debug.get("topNegativeDrivers") or []
    missing_inputs = debug.get("missingInputs") or []
    lines = [f"📌 *{signal.get('ticker')}* — {bias} ({confidence}%)"]
    if top_pos:
        lines.append(f"Top +: {', '.join(top_pos)}")
    if top_neg:
        lines.append(f"Top -: {', '.join(top_neg)}")
    for bullet in bullets[:6]:
        lines.append(f"• {bullet}")
    if missing_inputs:
        lines.append("")
        lines.append(f"_Missing inputs: {', '.join(missing_inputs[:6])}_")
    return "\n".join(lines)


def format_news(events: Iterable[dict], total: int) -> str:
    lines = ["🗞 *News digest*"]
    for event in events:
        time = event.get("datetime_local") or event.get("datetime") or "—"
        title = event.get("title") or "Untitled"
        country = event.get("country") or "—"
        lines.append(f"*{time}* {country} — {title}")
    lines.append(f"_Total events in range: {total}_")
    return "\n".join(lines)


def format_status(health: dict, refresh: dict | None = None) -> str:
    lines = ["✅ *Status*"]
    lines.append(f"API: {health.get('status', 'unknown')} (v{health.get('version', 'n/a')})")
    if refresh is not None:
        lines.append(f"Macro refresh: {refresh.get('status', 'unknown')}")
    return "\n".join(lines)
