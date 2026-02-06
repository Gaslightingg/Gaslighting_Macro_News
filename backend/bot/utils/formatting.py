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
        bias = signal.get("bias") or signal.get("signal")
        confidence = signal.get("confidence")
        conf_str = f"{confidence}%" if confidence is not None else "—"
        lines.append(f"*{signal.get('ticker')}*: {bias} ({conf_str})")
    return "\n".join(lines)


def format_signal_detail(signal: dict) -> str:
    bias = signal.get("bias") or signal.get("signal")
    confidence = signal.get("confidence")
    bullets = signal.get("bullets") or []
    lines = [f"📌 *{signal.get('ticker')}* — {bias} ({confidence}%)"]
    for bullet in bullets[:5]:
        lines.append(f"• {bullet}")
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
