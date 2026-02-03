import { useEffect, useMemo, useState } from "react";
import "./App.css";

const resolveApiBase = () => {
  const envBase = import.meta.env.VITE_API_URL;
  if (envBase) {
    return envBase.replace(/\/$/, "");
  }
  const hostname =
    window.location.hostname === "0.0.0.0"
      ? "localhost"
      : window.location.hostname;
  return `http://${hostname}:8000`;
};

const API_BASE = resolveApiBase();
const REQUEST_TIMEOUT = 8000;
const REFRESH_INTERVAL_MS = 60 * 60 * 1000;
const HISTORY_LIMIT = 48;

const HISTORY_STORAGE_KEY = "gm_history_v1";

const formatChange = (value) => `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
const formatPrice = (value) => (value < 10 ? value.toFixed(4) : value.toFixed(2));
const formatTime = (date) =>
  date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

const parseNumeric = (value) => {
  if (typeof value === "number") {
    return value;
  }
  if (!value) {
    return null;
  }
  const parsed = Number.parseFloat(String(value).replace(",", ""));
  return Number.isFinite(parsed) ? parsed : null;
};

const buildSparklinePath = (values, width, height) => {
  if (!values || values.length < 2) {
    return "";
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (values.length - 1);
  return values
    .map((value, index) => {
      const x = index * step;
      const y = height - ((value - min) / range) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
};

const Sparkline = ({ values, className }) => {
  const width = 90;
  const height = 28;
  const path = buildSparklinePath(values, width, height);
  return (
    <svg
      className={`sparkline ${className ?? ""}`}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
    >
      {path ? <path d={path} fill="none" /> : <line x1="0" y1="14" x2="90" y2="14" />}
    </svg>
  );
};

const fetchJson = async (url) => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  try {
    console.info(`[api] requesting ${url}`);
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      console.warn(`[api] ${url} responded with ${response.status}`);
      throw new Error(`Request failed: ${response.status}`);
    }
    console.info(`[api] ${url} OK`);
    return await response.json();
  } catch (error) {
    console.error(`[api] ${url} failed`, error);
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
};

function App() {
  const [prices, setPrices] = useState(null);
  const [macro, setMacro] = useState(null);
  const [signals, setSignals] = useState(null);
  const [error, setError] = useState(null);
  const [history, setHistory] = useState(() => {
    const stored = localStorage.getItem(HISTORY_STORAGE_KEY);
    return stored ? JSON.parse(stored) : { prices: {}, macro: {} };
  });
  const [now, setNow] = useState(new Date());
  const [lastFetch, setLastFetch] = useState(null);
  const [theme, setTheme] = useState(
    () => localStorage.getItem("gm_theme") ?? "dark",
  );

  const macroNotes = useMemo(
    () => ({
      CPI: "Tracks consumer inflation trends.",
      "Core CPI": "Inflation excluding food/energy noise.",
      PCE: "Fed-preferred inflation gauge.",
      "PMI/ISM": "Business activity and demand momentum.",
      NFP: "Monthly payroll growth signal.",
      "Unemployment Rate": "Labor slack and cycle health.",
      "Jobless Claims": "High-frequency layoffs pulse.",
      "Fed Funds Rate": "Policy stance for risk appetite.",
      US10Y: "Benchmark long-term yield level.",
      US2Y: "Policy expectations & curve shape.",
      US30Y: "Long duration sentiment.",
      VIX: "Risk-off volatility barometer.",
      DXY: "Dollar strength vs major currencies.",
    }),
    [],
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("gm_theme", theme);
  }, [theme]);

  useEffect(() => {
    const load = async () => {
      try {
        console.info(`[api] base url set to ${API_BASE}`);
        const [pricesRes, macroRes, signalsRes] = await Promise.all([
          fetchJson(`${API_BASE}/api/prices`),
          fetchJson(`${API_BASE}/api/macro`),
          fetchJson(`${API_BASE}/api/signals`),
        ]);
        setPrices(pricesRes);
        setMacro(macroRes);
        setSignals(signalsRes);
        setLastFetch(new Date());
        setError(null);
        setHistory((prev) => {
          const next = {
            prices: { ...prev.prices },
            macro: { ...prev.macro },
          };

          pricesRes?.tickers?.forEach((ticker) => {
            const value = parseNumeric(ticker.price);
            if (value === null) return;
            const historyArr = next.prices[ticker.symbol]
              ? [...next.prices[ticker.symbol]]
              : [];
            historyArr.push(value);
            next.prices[ticker.symbol] = historyArr.slice(-HISTORY_LIMIT);
          });

          macroRes?.series?.forEach((item) => {
            const value = parseNumeric(item.value);
            if (value === null) return;
            const historyArr = next.macro[item.name] ? [...next.macro[item.name]] : [];
            historyArr.push(value);
            next.macro[item.name] = historyArr.slice(-HISTORY_LIMIT);
          });

          localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
          return next;
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load data.");
      }
    };

    load();
    const intervalId = setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(intervalId);
  }, []);

  useEffect(() => {
    const clockId = setInterval(() => {
      setNow(new Date());
    }, 1000);
    return () => clearInterval(clockId);
  }, []);

  const nextRefresh = useMemo(() => {
    if (!lastFetch) return "Loading...";
    const next = new Date(lastFetch.getTime() + REFRESH_INTERVAL_MS);
    return formatTime(next);
  }, [lastFetch]);

  return (
    <div className="app">
      <header className="hero">
        <div>
          <p className="eyebrow">Dashboard</p>
          <h1>Gaslighting Macro News</h1>
          <p className="subtitle">
            Cross-asset signals and macro narratives, refreshed every morning.
          </p>
          <div className="meta-row">
            <span className="meta-chip">Local time: {formatTime(now)}</span>
            <span className="meta-chip">Next refresh: {nextRefresh}</span>
          </div>
        </div>
        <div className="right-controls">
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
          <div className="pill">{prices?.as_of ?? "Loading..."}</div>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <section className="section">
        <h2>Tickers</h2>
        <div className="grid tickers">
          {prices?.tickers?.map((ticker) => (
            <article key={ticker.symbol} className="card">
              <div className="card-row">
                <span className="symbol">{ticker.symbol}</span>
                <span className="price">{formatPrice(ticker.price)}</span>
              </div>
              <div
                className={`change ${
                  ticker.change_pct >= 0 ? "positive" : "negative"
                }`}
              >
                {formatChange(ticker.change_pct)}
              </div>
              <Sparkline
                values={history.prices?.[ticker.symbol]}
                className={ticker.change_pct >= 0 ? "positive" : "negative"}
              />
            </article>
          ))}
        </div>
      </section>

      <section className="section">
        <div className="section-header">
          <h2>Macro data</h2>
          <span className="section-meta">
            Updated: {macro?.as_of ?? "Loading..."}
          </span>
        </div>
        <div className="table card">
          <div className="table-header">
            <span>Indicator</span>
            <span>Value</span>
            <span>Change</span>
            <span>Last updated</span>
            <span>Why it matters</span>
            <span>Trend</span>
          </div>
          {macro?.series?.map((item) => (
            <div key={item.name} className="table-row">
              <span className="table-title">{item.name}</span>
              <span>{item.value}</span>
              <span
                className={`table-change ${
                  item.change?.startsWith("-") ? "negative" : "positive"
                }`}
              >
                {item.change}
              </span>
              <span className="table-date">{item.updated}</span>
              <span className="table-note">
                {macroNotes[item.name] ?? "Macro context signal."}
              </span>
              <Sparkline
                values={history.macro?.[item.name]}
                className={item.change?.startsWith("-") ? "negative" : "positive"}
              />
            </div>
          ))}
        </div>
        <p className="commentary">{macro?.commentary ?? "Loading..."}</p>
      </section>

      <section className="section">
        <div className="section-header">
          <h2>Signals</h2>
          <span className="section-meta">
            Updated: {signals?.as_of ?? "Loading..."}
          </span>
        </div>
        <div className="grid signals">
          {signals?.signals?.map((signal) => (
            <article key={signal.ticker} className="card">
              <div className="card-row">
                <p className="label">{signal.ticker}</p>
                <span className={`status ${signal.direction?.toLowerCase()}`}>
                  {signal.direction}
                </span>
              </div>
              <p className="signal-confidence">
                Confidence: {(signal.confidence * 100).toFixed(0)}%
              </p>
              <ul className="signal-reasons">
                {signal.reasons?.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </article>
          ))}
        </div>
        <p className="disclaimer">{signals?.disclaimer ?? "Not financial advice"}</p>
      </section>
    </div>
  );
}

export default App;
