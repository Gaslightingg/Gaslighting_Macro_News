from __future__ import annotations

from typing import Iterable
import html


def _fmt_value(value: float | None, unit: str | None = None) -> str:
    if value is None:
        return "—"
    if unit:
        return f"{value:.2f} {html.escape(unit)}"
    return f"{value:.2f}"


def format_prices(prices: Iterable[dict], errors: dict | None = None) -> str:
    lines = ["📈 <b>Prices</b>"]
    for ticker in prices:
        change = ticker.get("change")
        change_str = f"{change:+.2f}%" if isinstance(change, (int, float)) else "—"
        symbol = html.escape(str(ticker.get("symbol") or ticker.get("name") or "Unknown"))
        lines.append(f"<b>{symbol}</b>: {_fmt_value(ticker.get('value'), ticker.get('unit'))} ({change_str})")
    if errors:
        lines.append("")
        missing = ", ".join(html.escape(item) for item in sorted(errors.keys()))
        lines.append(f"<i>Partial data missing: {missing}</i>")
    return "\n".join(lines)


def format_signals(signals: Iterable[dict]) -> str:
    lines = ["🧭 <b>Signals</b>"]
    for signal in signals:
        bias = (signal.get("bias") or signal.get("signal") or "FLAT").upper()
        confidence = signal.get("confidence")
        conf_str = f"{confidence}%" if confidence is not None else "—"
        drivers = signal.get("debug", {}).get("topPositiveDrivers") or []
        if not drivers:
            drivers = signal.get("bullets") or []
        driver_text = "; ".join(drivers[:3]) if drivers else "No dominant drivers."
        ticker = html.escape(str(signal.get("ticker") or "Unknown"))
        lines.append(f"<b>{ticker}</b>: {html.escape(bias)} ({html.escape(conf_str)})")
        lines.append(f"<i>{html.escape(driver_text)}</i>")
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
    ticker = html.escape(str(signal.get("ticker") or "Unknown"))
    lines = [f"📌 <b>{ticker}</b> — {html.escape(bias)} ({html.escape(conf_str)})"]
    if top_pos:
        lines.append(f"Top +: {html.escape(', '.join(top_pos))}")
    if top_neg:
        lines.append(f"Top -: {html.escape(', '.join(top_neg))}")
    for bullet in bullets[:6]:
        lines.append(f"• {html.escape(str(bullet))}")
    if missing_inputs:
        lines.append("")
        lines.append(f"<i>Missing inputs: {html.escape(', '.join(missing_inputs[:6]))}</i>")
    factor_contributions = debug.get("factorContributions") or {}
    if factor_contributions:
        lines.append("")
        lines.append("<b>Factor contributions:</b>")
        for factor, entries in factor_contributions.items():
            if not entries:
                continue
            top_entries = entries[:3]
            formatted = ", ".join(
                f"{html.escape(str(entry.get('indicator')))}: {entry.get('contribution'):+.2f}"
                for entry in top_entries
                if entry.get("indicator") is not None and entry.get("contribution") is not None
            )
            if formatted:
                lines.append(f"- {html.escape(str(factor))}: {formatted}")
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
    ticker = html.escape(str(signal.get("ticker") or "Unknown"))
    return f"📌 <b>{ticker}</b> — {html.escape(bias)} ({html.escape(conf_str)})\n<i>{html.escape(driver_text)}</i>"


def format_news(events: Iterable[dict], total: int) -> str:
    lines = ["🗞 <b>News digest</b>"]
    for event in events:
        time = event.get("datetime_local") or event.get("datetime") or "—"
        title = event.get("title") or "Untitled"
        country = event.get("country") or "—"
        lines.append(f"<b>{html.escape(str(time))}</b> {html.escape(str(country))} — {html.escape(str(title))}")
    lines.append(f"<i>Total events in range: {total}</i>")
    return "\n".join(lines)


def format_macro_summary(latest: dict) -> str:
    lines = ["🧭 <b>Macro snapshot</b>"]
    items = latest.get("latest") or []
    movers = [item for item in items if isinstance(item.get("change"), (int, float))]
    movers.sort(key=lambda item: abs(item.get("change", 0)), reverse=True)
    for item in movers[:5]:
        lines.append(
            f"<b>{html.escape(str(item.get('name')))}</b>: {_fmt_value(item.get('value'), item.get('unit'))} ({item.get('change'):+.2f})"
        )
    missing = latest.get("missing_inputs") or []
    if missing:
        lines.append("")
        lines.append(f"<i>Macro sources disabled: {html.escape(', '.join(missing))}</i>")
    if len(lines) == 1:
        lines.append("<i>No macro updates available.</i>")
    return "\n".join(lines)


def format_status(health: dict, signals: dict | None = None, macro_latest: dict | None = None) -> str:
    lines = ["✅ <b>Status</b>"]
    lines.append(f"API: {html.escape(str(health.get('status', 'unknown')))} (v{html.escape(str(health.get('version', 'n/a')))})")
    if signals:
        lines.append(f"Signals updated: {html.escape(str(signals.get('updated_at', '—')))}")
    if macro_latest:
        missing = macro_latest.get("missing_inputs") or []
        fred_ok = "yes" if "FRED_API_KEY missing" not in missing else "no"
        bea_ok = "yes" if "BEA_API_KEY missing" not in missing else "no"
        lines.append(f"FRED key present: {fred_ok}")
        lines.append(f"BEA key present: {bea_ok}")
    return "\n".join(lines)
