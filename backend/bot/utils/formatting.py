from __future__ import annotations

from typing import Iterable


def _fmt_value(value: float | None, unit: str | None = None) -> str:
    if value is None:
        return "—"
    if unit:
        return f"{value:.2f} {unit}"
    return f"{value:.2f}"


def format_prices(prices: Iterable[dict], errors: dict | None = None) -> str:
    lines = ["📈 *Prices*"]
    for ticker in prices:
        change = ticker.get("change")
        change_str = f"{change:+.2f}%" if isinstance(change, (int, float)) else "—"
        lines.append(
            f"*{ticker.get('symbol') or ticker.get('name')}*: {_fmt_value(ticker.get('value'), ticker.get('unit'))} ({change_str})"
        )
    if errors:
        lines.append("")
        lines.append(f"_Partial data missing: {', '.join(sorted(errors.keys()))}_")
    return "\n".join(lines)


def format_signals(signals: Iterable[dict]) -> str:
    lines = ["🧭 *Signals*"]
    for signal in signals:
        bias = (signal.get("bias") or signal.get("signal") or "FLAT").upper()
        confidence = signal.get("confidence")
        conf_str = f"{confidence}%" if confidence is not None else "—"
        drivers = signal.get("debug", {}).get("topPositiveDrivers") or []
        if not drivers:
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
    conf_str = f"{confidence}%" if confidence is not None else "—"
    lines = [f"📌 *{signal.get('ticker')}* — {bias} ({conf_str})"]
    if top_pos:
        lines.append(f"Top +: {', '.join(top_pos)}")
    if top_neg:
        lines.append(f"Top -: {', '.join(top_neg)}")
    for bullet in bullets[:6]:
        lines.append(f"• {bullet}")
    if missing_inputs:
        lines.append("")
        lines.append(f"_Missing inputs: {', '.join(missing_inputs[:6])}_")
    factor_contributions = debug.get("factorContributions") or {}
    if factor_contributions:
        lines.append("")
        lines.append("*Factor contributions:*")
        for factor, entries in factor_contributions.items():
            if not entries:
                continue
            top_entries = entries[:3]
            formatted = ", ".join(
                f"{entry.get('indicator')}: {entry.get('contribution'):+.2f}"
                for entry in top_entries
                if entry.get("indicator") is not None and entry.get("contribution") is not None
            )
            if formatted:
                lines.append(f"- {factor}: {formatted}")
    return "\n".join(lines)


def format_signal_summary(signal: dict) -> str:
    bias = (signal.get("bias") or signal.get("signal") or "FLAT").upper()
    confidence = signal.get("confidence")
    conf_str = f"{confidence}%" if confidence is not None else "—"
    debug = signal.get("debug") or {}
    top_pos = debug.get("topPositiveDrivers") or []
    top_neg = debug.get("topNegativeDrivers") or []
    drivers = (top_pos + top_neg)[:3]
    driver_text = ", ".join(drivers) if drivers else "No dominant drivers"
    return f"📌 *{signal.get('ticker')}* — {bias} ({conf_str})\n_{driver_text}_"


def format_news(events: Iterable[dict], total: int) -> str:
    lines = ["🗞 *News digest*"]
    for event in events:
        time = event.get("datetime_local") or event.get("datetime") or "—"
        title = event.get("title") or "Untitled"
        country = event.get("country") or "—"
        lines.append(f"*{time}* {country} — {title}")
    lines.append(f"_Total events in range: {total}_")
    return "\n".join(lines)


def format_macro_summary(latest: dict) -> str:
    lines = ["🧭 *Macro snapshot*"]
    items = latest.get("latest") or []
    movers = [item for item in items if isinstance(item.get("change"), (int, float))]
    movers.sort(key=lambda item: abs(item.get("change", 0)), reverse=True)
    for item in movers[:5]:
        lines.append(
            f"*{item.get('name')}*: {_fmt_value(item.get('value'), item.get('unit'))} ({item.get('change'):+.2f})"
        )
    missing = latest.get("missing_inputs") or []
    if missing:
        lines.append("")
        lines.append(f"_Macro sources disabled: {', '.join(missing)}_")
    if len(lines) == 1:
        lines.append("_No macro updates available._")
    return "\n".join(lines)


def format_status(health: dict, signals: dict | None = None, macro_latest: dict | None = None) -> str:
    lines = ["✅ *Status*"]
    lines.append(f"API: {health.get('status', 'unknown')} (v{health.get('version', 'n/a')})")
    if signals:
        lines.append(f"Signals updated: {signals.get('updated_at', '—')}")
    if macro_latest:
        missing = macro_latest.get("missing_inputs") or []
        fred_ok = "yes" if "FRED_API_KEY missing" not in missing else "no"
        bea_ok = "yes" if "BEA_API_KEY missing" not in missing else "no"
        lines.append(f"FRED key present: {fred_ok}")
        lines.append(f"BEA key present: {bea_ok}")
    return "\n".join(lines)
