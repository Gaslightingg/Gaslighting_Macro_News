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
const DEFAULT_RANGE = "1y";
const RANGE_OPTIONS = ["1y", "2y", "5y", "max"];

const formatChange = (value) => `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
const formatValue = (value, unit) => {
  if (typeof value !== "number") return "—";
  const formatted = value.toFixed(2);
  return unit ? `${formatted} ${unit}` : formatted;
};
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

const fetchJson = async (url, signal) => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  const combinedSignal = signal ?? controller.signal;
  try {
    console.info(`[api] requesting ${url}`);
    const response = await fetch(url, { signal: combinedSignal });
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

const ChartModal = ({ indicator, onClose, endpoint }) => {
  const [range, setRange] = useState(DEFAULT_RANGE);
  const [seriesData, setSeriesData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!indicator) return;
    const loadSeries = async () => {
      try {
        setLoading(true);
        const data = await fetchJson(`${API_BASE}${endpoint}${indicator.id}?range=${range}`);
        setSeriesData(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load series.");
      } finally {
        setLoading(false);
      }
    };
    loadSeries();
  }, [indicator, range]);

  if (!indicator) return null;

  const tableRows = seriesData?.points?.slice(-50).reverse() ?? [];
  const chartValues = seriesData?.points ?? [];

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h3>{indicator.name}</h3>
            <p className="modal-subtitle">
              {seriesData?.source ?? indicator.source} · {seriesData?.unit ?? indicator.units}
              {" · "}
              {seriesData?.expected_frequency ?? indicator.frequency}
            </p>
          </div>
          <button type="button" className="modal-close" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-controls">
          {RANGE_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              className={`range-btn ${range === option ? "active" : ""}`}
              onClick={() => setRange(option)}
            >
              {option.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="modal-chart">
          {loading && <p>Loading chart...</p>}
          {error && <p className="error-banner">{error}</p>}
          {!loading && !error && chartValues.length > 0 && (
            <svg viewBox="0 0 600 220" width="100%" height="220">
              <path
                d={buildSparklinePath(
                  chartValues.map((point) => point.value),
                  600,
                  200,
                )}
                fill="none"
                stroke="var(--accent)"
                strokeWidth="2"
              />
            </svg>
          )}
          {!loading && !error && chartValues.length === 0 && (
            <p>No historical data available.</p>
          )}
        </div>
        <div className="modal-table">
          <div className="modal-table-header">
            <span>Date</span>
            <span>Value</span>
          </div>
          {tableRows.map((point) => (
            <div key={point.date} className="modal-table-row">
              <span>{point.date}</span>
              <span>{point.value.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

function App() {
  const [prices, setPrices] = useState(null);
  const [macroCategories, setMacroCategories] = useState([]);
  const [macroLatest, setMacroLatest] = useState([]);
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

  const [selectedIndicator, setSelectedIndicator] = useState(null);
  const [selectedTicker, setSelectedTicker] = useState(null);
  const [debugMode, setDebugMode] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("gm_theme", theme);
  }, [theme]);

  useEffect(() => {
    let timeoutId;
    let intervalId;
    const controller = new AbortController();
    const load = async () => {
      try {
        console.info(`[api] base url set to ${API_BASE}`);
        const [pricesRes, signalsRes, categoriesRes, latestRes] =
          await Promise.all([
          fetchJson(`${API_BASE}/api/prices`, controller.signal),
          fetchJson(`${API_BASE}/api/signals`, controller.signal),
          fetchJson(`${API_BASE}/api/macro/categories`, controller.signal),
          fetchJson(`${API_BASE}/api/macro/latest`, controller.signal),
        ]);
        setPrices(pricesRes);
        setSignals(signalsRes);
        setMacroCategories(categoriesRes.categories ?? []);
        setMacroLatest(latestRes.latest ?? []);
        setLastFetch(new Date());
        setError(null);
        setHistory((prev) => {
          const next = {
            prices: { ...prev.prices },
            macro: { ...prev.macro },
          };

          pricesRes?.tickers?.forEach((ticker) => {
            const value = parseNumeric(ticker.value);
            if (value === null) return;
            const historyArr = next.prices[ticker.id]
              ? [...next.prices[ticker.id]]
              : [];
            historyArr.push(value);
            next.prices[ticker.id] = historyArr.slice(-HISTORY_LIMIT);
          });

          localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(next));
          return next;
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load data.");
      }
    };

    const nextQuarterDelayMs = () => {
      const now = new Date();
      const minutes = now.getMinutes();
      const nextQuarter = (Math.floor(minutes / 15) + 1) * 15;
      const next = new Date(now);
      if (nextQuarter >= 60) {
        next.setHours(now.getHours() + 1, 0, 0, 0);
      } else {
        next.setMinutes(nextQuarter, 0, 0);
      }
      return next.getTime() - now.getTime();
    };

    load();
    timeoutId = setTimeout(() => {
      load();
      intervalId = setInterval(load, 15 * 60 * 1000);
    }, nextQuarterDelayMs());
    return () => {
      controller.abort();
      clearTimeout(timeoutId);
      clearInterval(intervalId);
    };
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

  const latestById = useMemo(() => {
    const map = new Map();
    macroLatest.forEach((item) => {
      map.set(item.indicator_id, item);
    });
    return map;
  }, [macroLatest]);

  const minPointsForFrequency = (frequency) => {
    switch (frequency) {
      case "monthly":
        return 12;
      case "weekly":
        return 26;
      case "daily":
        return 60;
      default:
        return 12;
    }
  };

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
            <span className="meta-chip">
              Last refresh: {lastFetch ? formatTime(lastFetch) : "—"}
            </span>
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
            <article key={ticker.id} className="card">
              <div className="card-row">
                <span className="symbol">{ticker.name}</span>
                <span className="price">{formatValue(ticker.value, ticker.unit)}</span>
              </div>
              <div
                className={`change ${
                  typeof ticker.change === "number" && ticker.change < 0
                    ? "negative"
                    : "positive"
                }`}
              >
                {typeof ticker.change === "number" ? formatChange(ticker.change) : "—"}
              </div>
              <span className={`status-badge ${ticker.status}`} title={ticker.error ?? ""}>
                {ticker.status}
              </span>
              <span className={`quality-badge ${ticker.quality}`}>{ticker.quality}</span>
              <button
                type="button"
                className="chart-btn"
                disabled={ticker.status === "unavailable" || ticker.history_points < 12}
                onClick={() => setSelectedTicker(ticker)}
              >
                Chart
              </button>
            </article>
          ))}
        </div>
      </section>

      <section className="section">
        <div className="section-header">
          <h2>Macro data</h2>
          <span className="section-meta">
            Updated: {macroLatest[0]?.last_updated ?? "Loading..."}
          </span>
        </div>
        {macroCategories.map((category) => (
          <div key={category.id} className="category-block">
            <div className="category-header">
              <h3>{category.name}</h3>
            </div>
            <div className="table card">
              <div className="table-header">
                <span>Indicator</span>
                <span>Value</span>
                <span>Change</span>
                <span>Last updated</span>
                <span>Why it matters</span>
                <span>Trend</span>
                <span>Status</span>
                <span>Quality</span>
                <span></span>
              </div>
              {category.indicators.map((indicator) => {
                const latest = latestById.get(indicator.id);
                return (
                  <div key={indicator.id} className="table-row">
                    <span className="table-title">{indicator.name}</span>
                    <span>{formatValue(latest?.value, latest?.unit)}</span>
                    <span
                      className={`table-change ${
                        typeof latest?.change === "number" && latest.change < 0
                          ? "negative"
                          : "positive"
                      }`}
                    >
                      {typeof latest?.change === "number"
                        ? formatChange(latest.change)
                        : "—"}
                    </span>
                    <span className="table-date">{latest?.last_updated ?? "—"}</span>
                    <span className="table-note">{indicator.why_it_matters}</span>
                    {history.macro?.[indicator.id]?.length ? (
                      <Sparkline
                        values={history.macro?.[indicator.id]}
                        className={
                          typeof latest?.change === "number" && latest.change < 0
                            ? "negative"
                            : "positive"
                        }
                      />
                    ) : (
                      <span className="table-note">—</span>
                    )}
                    <span
                      className={`status-badge ${latest?.status ?? "unknown"}`}
                      title={`${latest?.source ?? "unknown"} · ${
                        latest?.last_updated ?? "no date"
                      }${latest?.error ? ` · ${latest.error}` : ""}`}
                    >
                      {latest?.status ?? "unknown"}
                    </span>
                    <span className={`quality-badge ${latest?.quality ?? "low"}`}>
                      {latest?.quality ?? "low"}
                    </span>
                    <button
                      type="button"
                      className="chart-btn"
                      onClick={() => setSelectedIndicator(indicator)}
                      disabled={
                        !latest ||
                        latest.status === "unavailable" ||
                        latest.history_points <
                          minPointsForFrequency(latest.expected_frequency)
                      }
                    >
                      Chart
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        <p className="commentary">Data sourced from FRED/BEA with cache-aware status.</p>
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
      <div className="debug-toggle">
        <label>
          <input
            type="checkbox"
            checked={debugMode}
            onChange={(event) => setDebugMode(event.target.checked)}
          />
          Debug mode
        </label>
      </div>
      {debugMode && (
        <pre className="debug-panel">
          {JSON.stringify(macroLatest.slice(0, 5), null, 2)}
        </pre>
      )}
      {selectedIndicator && (
        <ChartModal
          indicator={selectedIndicator}
          endpoint="/api/macro/series/"
          onClose={() => setSelectedIndicator(null)}
        />
      )}
      {selectedTicker && (
        <ChartModal
          indicator={selectedTicker}
          endpoint="/api/prices/series/"
          onClose={() => setSelectedTicker(null)}
        />
      )}
    </div>
  );
}

export default App;
