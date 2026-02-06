import { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";

const resolveApiBase = () => {
  const envBase = import.meta.env.VITE_API_URL;
  if (envBase) return envBase.replace(/\/$/, "");
  const hostname = window.location.hostname === "0.0.0.0" ? "localhost" : window.location.hostname;
  return `http://${hostname}:8000`;
};

const API_BASE = resolveApiBase();
const IS_DEV = Boolean(import.meta.env.DEV);
const REQUEST_TIMEOUT = 15000;
const REFRESH_INTERVAL_MS = 60 * 60 * 1000;
const DEFAULT_RANGE = "1y";
const RANGE_OPTIONS = ["1y", "2y", "5y", "max"];
const PRICE_RANGE_OPTIONS = ["1m", "3m", "6m", "1y", "2y", "5y", "10y", "max"];
const NEWS_REFRESH_MS = 120 * 1000;
const DEFAULT_NEWS_RANGE = "6m_before";
const NEWS_PAGE_SIZE = 120;
const CACHE_PREFIX = "gm_cache_v1:";
const CACHE_TTL_MS = 10 * 60 * 1000;

const formatChange = (value) => `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
const formatValue = (value, unit) => {
  if (typeof value !== "number") return "—";
  return unit ? `${value.toFixed(2)} ${unit}` : value.toFixed(2);
};

const formatPointDate = (timestamp) => {
  if (!Number.isFinite(timestamp)) return "—";
  return new Date(timestamp).toISOString().slice(0, 10);
};

const formatYmd = (date) => date.toISOString().slice(0, 10);

const startOfWeek = (date) => {
  const d = new Date(date);
  const day = d.getDay();
  const diff = (day === 0 ? -6 : 1 - day);
  d.setDate(d.getDate() + diff);
  d.setHours(0, 0, 0, 0);
  return d;
};

const resolveNewsRange = (rangeKey) => {
  const now = new Date();
  const start = new Date(now);
  const end = new Date(now);
  start.setHours(0, 0, 0, 0);
  end.setHours(0, 0, 0, 0);

  switch (rangeKey) {
    case "6m_before":
      start.setMonth(start.getMonth() - 6);
      break;
    case "3m_before":
      start.setMonth(start.getMonth() - 3);
      break;
    case "1m_before":
      start.setMonth(start.getMonth() - 1);
      break;
    case "2w_before":
      start.setDate(start.getDate() - 14);
      break;
    case "1w_before":
      start.setDate(start.getDate() - 7);
      break;
    case "this_week":
      return { start: formatYmd(startOfWeek(now)), end: formatYmd(now) };
    case "this_month":
      start.setDate(1);
      return { start: formatYmd(start), end: formatYmd(now) };
    case "today":
    default:
      break;
  }
  return { start: formatYmd(start), end: formatYmd(now) };
};

const buildNewsUrl = (filters) => {
  const { start, end } = resolveNewsRange(filters.range);
  const params = new URLSearchParams({ start, end });
  if (filters.country !== "ALL") params.set("country", filters.country);
  if (filters.importance !== "ALL") params.set("importance", filters.importance);
  if (filters.status !== "ALL") params.set("status", filters.status);
  if (filters.search.trim()) params.set("search", filters.search.trim());
  return `${API_BASE}/api/news?${params.toString()}`;
};

const MAIN_ENDPOINTS = {
  prices: `${API_BASE}/api/prices`,
  signals: `${API_BASE}/api/signals`,
  macroCategories: `${API_BASE}/api/macro/categories`,
  macroLatest: `${API_BASE}/api/macro/latest`,
};

const isAbortError = (err) => {
  const message = String(err?.message ?? "").toLowerCase();
  return err?.name === "AbortError" || message.includes("aborted") || message.includes("operation was aborted");
};

const formatCacheTime = (timestamp) => {
  if (!timestamp) return "—";
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

const getCacheKey = (url) => `${CACHE_PREFIX}${url}`;

const readCacheEntry = (url) => {
  try {
    const raw = window.localStorage.getItem(getCacheKey(url));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    if (!parsed.fetchedAt) return null;
    return parsed;
  } catch (err) {
    if (IS_DEV) console.debug("[cache] read failed", err);
    return null;
  }
};

const readCachePayload = (url) => {
  const entry = readCacheEntry(url);
  if (!entry) return null;
  return {
    data: entry.data,
    fetchedAt: entry.fetchedAt,
    isStale: Date.now() - entry.fetchedAt > CACHE_TTL_MS,
  };
};

const writeCacheEntry = (url, data, fetchedAt = Date.now()) => {
  try {
    window.localStorage.setItem(getCacheKey(url), JSON.stringify({ data, fetchedAt }));
  } catch (err) {
    if (IS_DEV) console.debug("[cache] write failed", err);
  }
};

const inflightRequests = new Map();

const getDedupedRequest = (key, fetcher) => {
  const existing = inflightRequests.get(key);
  if (existing) {
    existing.count += 1;
    return existing;
  }
  const controller = new AbortController();
  const entry = {
    controller,
    count: 1,
    settled: false,
    promise: null,
  };
  entry.promise = fetcher(controller.signal).finally(() => {
    entry.settled = true;
    inflightRequests.delete(key);
  });
  inflightRequests.set(key, entry);
  return entry;
};

const releaseDedupedRequest = (key) => {
  const entry = inflightRequests.get(key);
  if (!entry) return;
  entry.count = Math.max(0, entry.count - 1);
  if (entry.count === 0 && !entry.settled) {
    window.setTimeout(() => {
      const current = inflightRequests.get(key);
      if (current && current.count === 0 && !current.settled) {
        current.controller.abort("cleanup");
      }
    }, 0);
  }
};

const fetchJson = async (url, signal) => {
  const controller = new AbortController();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  const timeoutId = setTimeout(() => controller.abort("timeout"), REQUEST_TIMEOUT);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timeoutId);
  }
};

const normalizeSignalsPayload = (payload) => {
  if (!payload) return { updated_at: null, signals: [], errors: ["empty payload"], missing_inputs: [] };
  if (Array.isArray(payload)) return { updated_at: null, signals: payload, errors: [], missing_inputs: [] };
  if (!Array.isArray(payload.signals)) {
    console.error("[signals] invalid payload", payload);
    return {
      updated_at: payload.updated_at ?? payload.as_of ?? null,
      signals: [],
      errors: ["invalid signals payload shape"],
      missing_inputs: [],
    };
  }
  return {
    updated_at: payload.updated_at ?? payload.as_of ?? null,
    signals: payload.signals,
    errors: payload.errors ?? [],
    missing_inputs: payload.missing_inputs ?? [],
  };
};

const buildSparklinePath = (values, width, height) => {
  if (!values || values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (values.length - 1);
  return values
    .map((value, i) => {
      const x = i * step;
      const y = height - ((value - min) / range) * height;
      return `${i === 0 ? "M" : "L"}${x} ${y}`;
    })
    .join(" ");
};

const buildSparklineAreaPath = (values, width, height) => {
  const linePath = buildSparklinePath(values, width, height);
  if (!linePath) return "";
  return `${linePath} L ${width} ${height} L 0 ${height} Z`;
};

const parseDateToTimestamp = (value) => {
  if (value == null) return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const normalizeSeriesPoints = (points) => {
  if (!Array.isArray(points)) return [];
  return points
    .map((point, idx) => {
      const value = Number(point?.value);
      const t = parseDateToTimestamp(point?.date ?? point?.t ?? point?.timestamp);
      if (!Number.isFinite(value) || t == null) return null;
      return { idx, t, value, date: point?.date ?? new Date(t).toISOString().slice(0, 10) };
    })
    .filter(Boolean)
    .sort((a, b) => a.t - b.t);
};

const buildTimeSeriesPath = (points, width, height) => {
  if (!points || points.length < 2) return "";
  const minT = points[0].t;
  const maxT = points[points.length - 1].t;
  const minV = Math.min(...points.map((point) => point.value));
  const maxV = Math.max(...points.map((point) => point.value));
  const tRange = maxT - minT || 1;
  const vRange = maxV - minV || 1;
  return points
    .map((point, idx) => {
      const x = ((point.t - minT) / tRange) * width;
      const y = height - ((point.value - minV) / vRange) * height;
      return `${idx === 0 ? "M" : "L"}${x} ${y}`;
    })
    .join(" ");
};

const Sparkline = ({ values, points, tone, variant = "compact", unit }) => {
  const isLarge = variant === "large";
  const width = isLarge ? 1400 : 120;
  const height = isLarge ? 360 : 34;
  const plotPoints = useMemo(() => {
    if (!isLarge || !Array.isArray(points) || points.length < 2) return [];
    const minT = points[0].t;
    const maxT = points[points.length - 1].t;
    const minV = Math.min(...points.map((point) => point.value));
    const maxV = Math.max(...points.map((point) => point.value));
    const tRange = maxT - minT || 1;
    const vRange = maxV - minV || 1;
    return points.map((point) => ({
      ...point,
      x: ((point.t - minT) / tRange) * width,
      y: height - ((point.value - minV) / vRange) * height,
    }));
  }, [height, isLarge, points, width]);

  const path = isLarge ? buildTimeSeriesPath(points ?? [], width, height) : buildSparklinePath(values, width, height);
  const areaPath = isLarge ? `${path} L ${width} ${height} L 0 ${height} Z` : "";
  const [hoverState, setHoverState] = useState(null);
  const [pinned, setPinned] = useState(false);

  const pickNearestPoint = (clientX, bounds) => {
    if (!plotPoints.length || !bounds?.width) return null;
    const relativeX = ((clientX - bounds.left) / bounds.width) * width;
    let nearest = plotPoints[0];
    let best = Math.abs(nearest.x - relativeX);
    for (let i = 1; i < plotPoints.length; i += 1) {
      const dist = Math.abs(plotPoints[i].x - relativeX);
      if (dist < best) {
        best = dist;
        nearest = plotPoints[i];
      }
    }
    return nearest;
  };

  const handlePointerMove = (event) => {
    if (!isLarge || pinned) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    setHoverState(pickNearestPoint(event.clientX, bounds));
  };

  const handlePointerLeave = () => {
    if (!pinned) setHoverState(null);
  };

  const handleTap = (event) => {
    if (!isLarge) return;
    const isTouch = window.matchMedia("(pointer: coarse)").matches;
    if (!isTouch) return;
    if (pinned) {
      setPinned(false);
      setHoverState(null);
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    const next = pickNearestPoint(event.clientX, bounds);
    setHoverState(next);
    setPinned(true);
  };

  const tooltipLeftPx = hoverState ? `${Math.min(Math.max((hoverState.x / width) * 100, 10), 90)}%` : "50%";

  return (
    <div className={`sparkline-wrap ${isLarge ? "large" : "compact"}`}>
      <svg
        className={`sparkline ${tone ?? "flat"} ${isLarge ? "large" : "compact"}`}
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        onMouseMove={handlePointerMove}
        onMouseLeave={handlePointerLeave}
        onClick={handleTap}
      >
        {isLarge ? (
          <>
            <defs>
              <linearGradient id="sparkArea" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="rgba(213, 165, 75, 0.2)" />
                <stop offset="100%" stopColor="rgba(213, 165, 75, 0.02)" />
              </linearGradient>
            </defs>
            <g className="spark-grid">
              <line x1="0" y1={height * 0.2} x2={width} y2={height * 0.2} />
              <line x1="0" y1={height * 0.4} x2={width} y2={height * 0.4} />
              <line x1="0" y1={height * 0.6} x2={width} y2={height * 0.6} />
              <line x1="0" y1={height * 0.8} x2={width} y2={height * 0.8} />
            </g>
          </>
        ) : null}
        {areaPath ? <path d={areaPath} className="spark-area" /> : null}
        {path ? <path d={path} fill="none" /> : <line x1="0" y1="17" x2={width} y2="17" />}
        {isLarge && hoverState ? (
          <g className="spark-hover-layer">
            <line className="spark-crosshair" x1={hoverState.x} y1={0} x2={hoverState.x} y2={height} />
            <circle className="spark-marker" cx={hoverState.x} cy={hoverState.y} r={6} />
          </g>
        ) : null}
      </svg>
      {isLarge && hoverState ? (
        <div className="chart-tooltip" style={{ left: tooltipLeftPx }}>
          <span>Date: {formatPointDate(hoverState.t)}</span>
          <span>Value: {formatValue(hoverState.value, unit)}</span>
        </div>
      ) : null}
    </div>
  );
};

const SkeletonCard = () => <article className="panel card skeleton" />;

const ChartModal = ({ indicator, mode, onClose }) => {
  const [range, setRange] = useState(DEFAULT_RANGE);
  const [seriesData, setSeriesData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const modalRef = useRef(null);
  const chartShellRef = useRef(null);
  const chartCanvasRef = useRef(null);
  const closeEnabledRef = useRef(false);
  const [chartReady, setChartReady] = useState(false);
  const [chartRenderNonce, setChartRenderNonce] = useState(0);
  const [chartDims, setChartDims] = useState({ width: 0, height: 0 });
  const lastGoodPointsRef = useRef([]);
  const renderCountRef = useRef(0);
  const requestSeqRef = useRef(0);
  const activeRequestRef = useRef(null);

  const options = mode === "price" ? PRICE_RANGE_OPTIONS : RANGE_OPTIONS;

  useEffect(() => {
    if (!indicator) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (modalRef.current) modalRef.current.scrollTop = 0;
    closeEnabledRef.current = false;
    setChartReady(false);
    const timer = window.setTimeout(() => {
      closeEnabledRef.current = true;
    }, 180);
    const mountFrame1 = window.requestAnimationFrame(() => {
      const mountFrame2 = window.requestAnimationFrame(() => {
        setChartReady(true);
      });
      return () => window.cancelAnimationFrame(mountFrame2);
    });

    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);

    return () => {
      if (IS_DEV) console.debug("[chart-modal] unmount modal", { id: indicator?.id, mode });
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      window.cancelAnimationFrame(mountFrame1);
    };
  }, [indicator, onClose]);

  useEffect(() => {
    if (!indicator || !chartShellRef.current) return;
    const node = chartShellRef.current;

    let resizeDebounceTimer;

    const updateDims = (source) => {
      const rect = node.getBoundingClientRect();
      if (IS_DEV) {
        console.debug("[chart-modal] container size", {
          source,
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        });
      }
      setChartDims({ width: rect.width, height: rect.height });
    };

    updateDims("mount");
    const observer = new ResizeObserver(() => {
      window.clearTimeout(resizeDebounceTimer);
      resizeDebounceTimer = window.setTimeout(() => {
        const rect = node.getBoundingClientRect();
        if (rect.width < 300 || rect.height < 200) {
          if (IS_DEV) {
            console.debug("[chart-modal] ignored unstable resize", {
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            });
          }
          return;
        }
        updateDims("resize");
      }, 80);
    });
    observer.observe(node);

    return () => {
      observer.disconnect();
      window.clearTimeout(resizeDebounceTimer);
    };
  }, [indicator, range]);

  useEffect(() => {
    if (!indicator) return;
    const load = async () => {
      requestSeqRef.current += 1;
      const requestId = requestSeqRef.current;
      const controller = new AbortController();
      const requestUrl =
        mode === "price"
          ? `${API_BASE}/api/prices/history?symbol=${indicator.id}&range=${range}`
          : `${API_BASE}/api/macro/series/${indicator.id}?range=${range}`;

      if (activeRequestRef.current) {
        if (IS_DEV) {
          console.debug("[chart-modal] abort previous request", {
            requestId: activeRequestRef.current.id,
            reason: "superseded",
            url: activeRequestRef.current.url,
          });
        }
        activeRequestRef.current.controller.abort("superseded");
      }

      activeRequestRef.current = { id: requestId, controller, url: requestUrl };
      if (IS_DEV) console.debug("[chart-modal] request start", { requestId, url: requestUrl });

      setLoading(true);
      setError(null);
      setChartReady(false);
      if (modalRef.current) modalRef.current.scrollTop = 0;
      try {
        const response = await fetch(requestUrl, { signal: controller.signal });
        if (!response.ok) throw new Error(`Request failed: ${response.status}`);
        const data = await response.json();
        if (requestId !== requestSeqRef.current) {
          if (IS_DEV) console.debug("[chart-modal] stale response ignored", { requestId, url: requestUrl });
          return;
        }
        if (IS_DEV && !Array.isArray(data?.points)) {
          console.debug("[chart-modal] fetched series without points array", { requestId, url: requestUrl, payload: data });
        }
        setSeriesData(data);
      } catch (err) {
        if (isAbortError(err)) {
          if (IS_DEV) {
            console.debug("[chart-modal] request aborted", {
              requestId,
              url: requestUrl,
              reason: controller.signal.reason ?? "abort",
            });
          }
          return;
        }
        if (requestId !== requestSeqRef.current) {
          if (IS_DEV) console.debug("[chart-modal] stale request error ignored", { requestId, url: requestUrl });
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load chart.");
      } finally {
        if (requestId === requestSeqRef.current) {
          setLoading(false);
        }
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            setChartReady(true);
          });
        });
      }
    };
    load();

    return () => {
      if (activeRequestRef.current?.id === requestSeqRef.current) {
        if (IS_DEV) {
          console.debug("[chart-modal] cleanup abort", {
            requestId: activeRequestRef.current.id,
            reason: "effect cleanup",
            url: activeRequestRef.current.url,
          });
        }
        activeRequestRef.current.controller.abort("effect cleanup");
      }
    };
  }, [indicator, mode, range]);

  if (!indicator) return null;

  const normalizedPoints = useMemo(() => normalizeSeriesPoints(seriesData?.points), [seriesData]);
  useEffect(() => {
    if (normalizedPoints.length > 1) {
      lastGoodPointsRef.current = normalizedPoints;
    } else if (IS_DEV && seriesData?.points && normalizedPoints.length === 0) {
      console.debug("[chart-modal] normalized points became empty", {
        id: indicator.id,
        mode,
        range,
        rawPoints: seriesData.points.length,
      });
    }
  }, [indicator.id, mode, range, normalizedPoints, seriesData]);

  const visiblePoints = normalizedPoints.length > 1 ? normalizedPoints : lastGoodPointsRef.current;
  const values = visiblePoints.map((pt) => pt.value);
  const rows = visiblePoints.slice(-30).reverse();
  const minChartWidth = window.innerWidth >= 900 ? 400 : 220;
  const minChartHeight = window.innerWidth >= 900 ? 280 : 220;
  const hasInitialData = visiblePoints.length > 1;
  const hasStableDims = chartDims.width >= minChartWidth && chartDims.height >= minChartHeight;
  const canShowChart = hasInitialData && hasStableDims;

  renderCountRef.current += 1;
  if (IS_DEV) {
    console.debug("[chart-modal] render", {
      renderCount: renderCountRef.current,
      timeframe: range,
      pointsLength: visiblePoints.length,
      containerW: Math.round(chartDims.width),
      containerH: Math.round(chartDims.height),
      loading,
      chartReady,
    });
  }

  useEffect(() => {
    if (!canShowChart || !chartCanvasRef.current || !chartShellRef.current) return;
    const chartRect = chartCanvasRef.current.getBoundingClientRect();
    const shellRect = chartShellRef.current.getBoundingClientRect();
    if (IS_DEV) {
      console.debug("[chart-modal] rendered chart width check", {
        chartWidth: Math.round(chartRect.width),
        containerWidth: Math.round(shellRect.width),
      });
    }
    if (chartRect.width < shellRect.width * 0.6) {
      if (IS_DEV) {
        console.warn("[chart-modal] detected narrow chart render; forcing recovery", {
          chartWidth: Math.round(chartRect.width),
          containerWidth: Math.round(shellRect.width),
        });
      }
      window.requestAnimationFrame(() => {
        setChartRenderNonce((prev) => prev + 1);
        window.requestAnimationFrame(() => {
          if (chartShellRef.current) {
            const nextRect = chartShellRef.current.getBoundingClientRect();
            setChartDims({ width: nextRect.width, height: nextRect.height });
          }
        });
      });
    }
  }, [canShowChart, chartDims.width, chartDims.height, visiblePoints.length, chartRenderNonce]);

  useEffect(() => {
    if (!IS_DEV) return;
    console.debug("[chart-modal] mount chart layer", { id: indicator.id, mode });
    return () => {
      console.debug("[chart-modal] unmount chart layer", { id: indicator.id, mode });
    };
  }, [indicator.id, mode]);

  return (
    <div
      className="modal-backdrop modal-enter"
      onClick={() => {
        if (closeEnabledRef.current) onClose();
      }}
    >
      <div ref={modalRef} className="modal panel modal-enter" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-wrap">
            <h3>{indicator.symbol ?? indicator.name ?? indicator.id}</h3>
            <p className="muted">
              {seriesData?.source ?? indicator.source ?? "Data"} · {range.toUpperCase()}
            </p>
          </div>
          <div className="modal-controls" role="tablist" aria-label="Chart range">
            {options.map((option) => (
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
          <span className="chart-hint">Hover to inspect</span>
          <button className="icon-btn modal-close-fixed" type="button" onClick={onClose}>
            ✕
          </button>
        </div>

        <div ref={chartShellRef} className="chart-shell panel chart-fixed-height" title="Chart">
          {!hasInitialData ? (
            <div className="chart-skeleton" />
          ) : error ? (
            <div className="terminal-state error">{error}</div>
          ) : (
            <div ref={chartCanvasRef} className={`chart-fade-in ${chartReady && canShowChart ? "ready" : "pending"}`} data-render-nonce={chartRenderNonce}>
              <Sparkline values={values} points={visiblePoints} tone="flat" variant="large" unit={seriesData?.unit} />
              {loading ? <div className="chart-inline-loading">Updating…</div> : null}
            </div>
          )}
        </div>

        <div className="modal-table">
          <div className="modal-table-header">
            <span>Date</span>
            <span>Value</span>
          </div>
          {rows.map((row) => (
            <div key={`${row.t}-${row.idx}`} className="modal-table-row">
              <span>{row.date}</span>
              <span>{formatValue(row.value, seriesData?.unit)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};


function App() {
  const initialView = (() => {
    const q = new URLSearchParams(window.location.search).get("view");
    if (q === "news") return "news";
    return "main";
  })();
  const initialMainCache = useMemo(
    () => ({
      prices: readCachePayload(MAIN_ENDPOINTS.prices),
      signals: readCachePayload(MAIN_ENDPOINTS.signals),
      macroCategories: readCachePayload(MAIN_ENDPOINTS.macroCategories),
      macroLatest: readCachePayload(MAIN_ENDPOINTS.macroLatest),
    }),
    []
  );
  const initialMainFetchedAt = useMemo(() => {
    const times = [
      initialMainCache.prices?.fetchedAt,
      initialMainCache.signals?.fetchedAt,
      initialMainCache.macroCategories?.fetchedAt,
      initialMainCache.macroLatest?.fetchedAt,
    ].filter((value) => Number.isFinite(value));
    if (!times.length) return null;
    return Math.max(...times);
  }, [initialMainCache]);
  const initialMainStale = useMemo(
    () => [initialMainCache.prices, initialMainCache.signals, initialMainCache.macroCategories, initialMainCache.macroLatest].some((entry) => entry?.isStale),
    [initialMainCache]
  );
  const [prices, setPrices] = useState(() => initialMainCache.prices?.data ?? null);
  const [signals, setSignals] = useState(() => (initialMainCache.signals?.data ? normalizeSignalsPayload(initialMainCache.signals.data) : null));
  const [macroCategories, setMacroCategories] = useState(() => initialMainCache.macroCategories?.data?.categories ?? []);
  const [macroLatest, setMacroLatest] = useState(() => initialMainCache.macroLatest?.data?.latest ?? []);
  const [error, setError] = useState(null);
  const [lastFetch, setLastFetch] = useState(() => (initialMainFetchedAt ? new Date(initialMainFetchedAt) : null));
  const [mainRefreshing, setMainRefreshing] = useState(false);
  const [mainRefreshError, setMainRefreshError] = useState(null);
  const [mainFromCache, setMainFromCache] = useState(Boolean(initialMainFetchedAt));
  const [mainCacheInfo, setMainCacheInfo] = useState(() =>
    initialMainFetchedAt ? { fetchedAt: initialMainFetchedAt, isStale: initialMainStale } : null
  );
  const [selectedIndicator, setSelectedIndicator] = useState(null);
  const [selectedTicker, setSelectedTicker] = useState(null);
  const [debugMode, setDebugMode] = useState(false);
  const [now, setNow] = useState(new Date());
  const [activeView, setActiveView] = useState(initialView);
  const touchStartXRef = useRef(null);
  const [newsPayload, setNewsPayload] = useState({ updated_at: null, provider_status: "ok", events: [] });
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsError, setNewsError] = useState(null);
  const [newsRefreshError, setNewsRefreshError] = useState(null);
  const [newsFromCache, setNewsFromCache] = useState(false);
  const [newsCacheInfo, setNewsCacheInfo] = useState(null);
  const initialRange = (() => {
    const stored = window.localStorage.getItem("news_range");
    const query = new URLSearchParams(window.location.search).get("range");
    return query || stored || DEFAULT_NEWS_RANGE;
  })();
  const [newsFilters, setNewsFilters] = useState({
    range: initialRange,
    country: "ALL",
    importance: "ALL",
    status: "ALL",
    search: "",
  });
  const [newsPage, setNewsPage] = useState(1);

  useEffect(() => {
    let timeoutId;
    let intervalId;
    let isActive = true;
    const activeRequests = new Set();

    const load = async () => {
      try {
        setMainRefreshing(true);
        setMainRefreshError(null);
        const requests = [
          { key: MAIN_ENDPOINTS.prices, fetcher: (signal) => fetchJson(MAIN_ENDPOINTS.prices, signal) },
          { key: MAIN_ENDPOINTS.signals, fetcher: (signal) => fetchJson(MAIN_ENDPOINTS.signals, signal) },
          { key: MAIN_ENDPOINTS.macroCategories, fetcher: (signal) => fetchJson(MAIN_ENDPOINTS.macroCategories, signal) },
          { key: MAIN_ENDPOINTS.macroLatest, fetcher: (signal) => fetchJson(MAIN_ENDPOINTS.macroLatest, signal) },
        ];
        const entries = requests.map(({ key, fetcher }) => {
          activeRequests.add(key);
          return { key, entry: getDedupedRequest(key, fetcher) };
        });
        const [pricesRes, signalsRes, categoriesRes, latestRes] = await Promise.all(entries.map(({ entry }) => entry.promise));
        if (!isActive) return;
        const fetchedAt = Date.now();
        setPrices(pricesRes);
        setSignals(normalizeSignalsPayload(signalsRes));
        setMacroCategories(categoriesRes.categories ?? []);
        setMacroLatest(latestRes.latest ?? []);
        setError(null);
        setLastFetch(new Date(fetchedAt));
        setMainFromCache(false);
        setMainCacheInfo({ fetchedAt, isStale: false });
        writeCacheEntry(MAIN_ENDPOINTS.prices, pricesRes, fetchedAt);
        writeCacheEntry(MAIN_ENDPOINTS.signals, signalsRes, fetchedAt);
        writeCacheEntry(MAIN_ENDPOINTS.macroCategories, categoriesRes, fetchedAt);
        writeCacheEntry(MAIN_ENDPOINTS.macroLatest, latestRes, fetchedAt);
      } catch (err) {
        if (!isActive) return;
        if (isAbortError(err)) {
          if (IS_DEV) console.debug("[ui] main fetch aborted", err);
          return;
        }
        const hasCachedMain = Boolean(prices || macroCategories.length || signals?.signals?.length || macroLatest.length);
        console.error("[ui] fetch failed", err);
        if (hasCachedMain) {
          setMainRefreshError("Offline / refresh failed");
        } else {
          setError(err instanceof Error ? err.message : "Failed to load dashboard.");
        }
      } finally {
        if (isActive) setMainRefreshing(false);
        Array.from(activeRequests).forEach((key) => {
          releaseDedupedRequest(key);
          activeRequests.delete(key);
        });
      }
    };

    const nextQuarterDelayMs = () => {
      const current = new Date();
      const next = new Date(current);
      const nextQuarter = (Math.floor(current.getMinutes() / 15) + 1) * 15;
      if (nextQuarter >= 60) next.setHours(current.getHours() + 1, 0, 0, 0);
      else next.setMinutes(nextQuarter, 0, 0);
      return next.getTime() - current.getTime();
    };

    load();
    timeoutId = setTimeout(() => {
      load();
      intervalId = setInterval(load, 15 * 60 * 1000);
    }, nextQuarterDelayMs());

    return () => {
      isActive = false;
      Array.from(activeRequests).forEach((key) => releaseDedupedRequest(key));
      clearTimeout(timeoutId);
      clearInterval(intervalId);
    };
  }, []);

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (activeView !== "news") return;
    let isActive = true;
    const activeRequests = new Set();

    const loadNews = async () => {
      setNewsLoading(true);
      setNewsRefreshError(null);
      const requestUrl = buildNewsUrl(newsFilters);
      const cachedNews = readCachePayload(requestUrl);
      if (cachedNews?.data) {
        setNewsPayload(cachedNews.data ?? { updated_at: null, provider_status: "ok", events: [] });
        setNewsFromCache(true);
        setNewsCacheInfo({ fetchedAt: cachedNews.fetchedAt, isStale: cachedNews.isStale });
        setNewsError(null);
      }
      try {
        const entry = getDedupedRequest(requestUrl, (signal) => fetchJson(requestUrl, signal));
        activeRequests.add(requestUrl);
        const data = await entry.promise;
        if (!isActive) return;
        const fetchedAt = Date.now();
        setNewsPayload(data ?? { updated_at: null, provider_status: "ok", events: [] });
        setNewsError(null);
        setNewsFromCache(false);
        setNewsCacheInfo({ fetchedAt, isStale: false });
        writeCacheEntry(requestUrl, data, fetchedAt);
      } catch (err) {
        if (!isActive) return;
        if (isAbortError(err)) {
          if (IS_DEV) console.debug("[ui] news fetch aborted", err);
          return;
        }
        if (cachedNews?.data) {
          setNewsRefreshError("Offline / refresh failed");
        } else {
          setNewsError(err instanceof Error ? err.message : "Failed to load news");
        }
      } finally {
        if (isActive) setNewsLoading(false);
        Array.from(activeRequests).forEach((key) => {
          releaseDedupedRequest(key);
          activeRequests.delete(key);
        });
      }
    };

    loadNews();
    const id = setInterval(loadNews, NEWS_REFRESH_MS);
    return () => {
      isActive = false;
      Array.from(activeRequests).forEach((key) => releaseDedupedRequest(key));
      clearInterval(id);
    };
  }, [activeView, newsFilters]);

  useEffect(() => {
    setNewsPage(1);
  }, [newsFilters]);

  useEffect(() => {
    window.localStorage.setItem("news_range", newsFilters.range);
    const url = new URL(window.location.href);
    url.searchParams.set("range", newsFilters.range);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, [newsFilters.range]);

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("view", activeView);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, [activeView]);


  const hasMainData = useMemo(
    () => Boolean(prices || macroCategories.length || signals?.signals?.length || macroLatest.length),
    [macroCategories.length, macroLatest.length, prices, signals]
  );

  const latestById = useMemo(() => {
    const map = new Map();
    macroLatest.forEach((item) => map.set(item.indicator_id, item));
    return map;
  }, [macroLatest]);

  const riskCards = useMemo(() => {
    const defs = [
      { id: "vix", title: "Volatility Regime", unit: "idx" },
      { id: "us10y", title: "10Y Yield", unit: "%" },
      { id: "dxy", title: "Dollar Strength", unit: "idx" },
      { id: "unemployment", title: "Labor Slack", unit: "%" },
    ];
    return defs.map((def) => ({
      ...def,
      data: latestById.get(def.id),
    }));
  }, [latestById]);

  const nextRefresh = useMemo(() => {
    if (!lastFetch) return "Loading...";
    return new Date(lastFetch.getTime() + REFRESH_INTERVAL_MS).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }, [lastFetch]);

  const handleTouchStart = (event) => {
    touchStartXRef.current = event.touches?.[0]?.clientX ?? null;
  };

  const handleTouchEnd = (event) => {
    if (touchStartXRef.current == null) return;
    const endX = event.changedTouches?.[0]?.clientX;
    if (typeof endX !== "number") return;
    const delta = endX - touchStartXRef.current;
    if (delta < -60) setActiveView("news");
    else if (delta > 60) setActiveView("main");
    touchStartXRef.current = null;
  };

  return (
    <div className="app">
      <header className="hero panel fade-up">
        <div>
          <p className="eyebrow">Institutional Macro Desk</p>
          <h1>Gaslighting Macro News</h1>
          <p className="subtitle">Cross-asset intelligence layer for discretionary and systematic macro decisions.</p>
          <nav className="nav-mini" aria-label="Primary view switcher">
            <button
              type="button"
              className={`view-switch-btn ${activeView === "main" ? "active" : ""}`}
              onClick={() => setActiveView("main")}
            >
              Main
            </button>
            <button
              type="button"
              className={`view-switch-btn ${activeView === "news" ? "active" : ""}`}
              onClick={() => setActiveView("news")}
            >
              News
            </button>
          </nav>
        </div>
        <div className="meta-stack">
          <div className="meta-chip">Local {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
          <div className="meta-chip">Refresh {nextRefresh}</div>
          <div className="meta-chip">As of {prices?.as_of ?? "—"}</div>
          {mainCacheInfo ? (
            <div className={`meta-chip cache-chip ${mainCacheInfo.isStale ? "stale" : "cached"}`}>
              {mainFromCache ? "Cached" : "Updated"} {formatCacheTime(mainCacheInfo.fetchedAt)}
            </div>
          ) : null}
          {mainRefreshing && hasMainData ? <div className="meta-chip refreshing">Refreshing…</div> : null}
          {mainRefreshError ? <div className="meta-chip warning">{mainRefreshError}</div> : null}
        </div>
      </header>

      <div
        className={`view-slider-viewport ${activeView === "news" ? "news-active" : "main-active"}`}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
      >
      <div className="view-slider-track">
      <main className="view-page page-main">
      {error && <div className="terminal-state error fade-up">{error}</div>}

      <section className="section" id="prices">
        <div className="section-header"><h2>Prices</h2><span className="section-meta">Spot + history</span></div>
        <div className="grid prices-grid">
          {prices?.tickers?.length
            ? prices.tickers.map((ticker) => {
                const tone = typeof ticker.change === "number" && ticker.change < 0 ? "negative" : "positive";
                return (
                  <article key={ticker.id} className="panel card fade-up">
                    <div className="card-row">
                      <span className="symbol">{ticker.symbol ?? ticker.name}</span>
                      <span className="price">{formatValue(ticker.value, ticker.unit)}</span>
                    </div>
                    <p className={`change ${tone}`}>{typeof ticker.change === "number" ? formatChange(ticker.change) : "—"}</p>
                    <Sparkline values={ticker.history_points?.map((p) => p.value) ?? []} tone={tone} />
                    <span className="ticker-meta">
                      {ticker.history_meta?.data_start ? `Data since ${ticker.history_meta.data_start}` : "Data availability pending"}
                    </span>
                    <div className="card-row">
                      <span className={`status-badge ${ticker.status}`}>{ticker.status}</span>
                      <button type="button" className="chart-btn" onClick={() => setSelectedTicker(ticker)} disabled={ticker.status === "unavailable"}>
                        Chart
                      </button>
                    </div>
                  </article>
                );
              })
            : Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      </section>


      <section className="section" id="macro">
        <div className="section-header">
          <h2>Macro</h2>
          <span className="section-meta">Updated {macroLatest[0]?.last_updated ?? "Loading..."}</span>
        </div>

        {!macroCategories.length ? (
          <div className="grid macro-grid">{Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}</div>
        ) : (
          macroCategories.map((category) => (
            <div key={category.id} className="category-block panel fade-up">
              <div className="category-header"><h3>{category.name}</h3></div>
              <div className="table">
                <div className="table-header">
                  <span>Indicator</span><span>Value</span><span>Change</span><span>Updated</span><span>Status</span><span>Quality</span><span></span>
                </div>
                {category.indicators.map((indicator) => {
                  const latest = latestById.get(indicator.id);
                  const tone = typeof latest?.change === "number" && latest.change < 0 ? "negative" : "positive";
                  return (
                    <div key={indicator.id} className="table-row">
                      <span className="table-title">{indicator.name}</span>
                      <span>{formatValue(latest?.value, latest?.unit)}</span>
                      <span className={`table-change ${tone}`}>{typeof latest?.change === "number" ? formatChange(latest.change) : "—"}</span>
                      <span className="table-date">{latest?.last_updated ?? "—"}</span>
                      <span className={`status-badge ${latest?.status ?? "unknown"}`}>{latest?.status ?? "unknown"}</span>
                      <span className={`quality-badge ${latest?.quality ?? "low"}`}>{latest?.quality ?? "low"}</span>
                      <button
                        type="button"
                        className="chart-btn"
                        onClick={() => setSelectedIndicator(indicator)}
                        disabled={!latest || latest.status === "unavailable"}
                      >
                        Chart
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </section>

      <section className="section" id="risk">
        <div className="section-header"><h2>Risk</h2><span className="section-meta">Desk pulse</span></div>
        <div className="grid risk-grid">
          {riskCards.map((card) => {
            const change = card.data?.change;
            const tone = typeof change === "number" && change < 0 ? "negative" : "positive";
            return (
              <article key={card.id} className="panel card fade-up">
                <p className="label">{card.title}</p>
                <p className="risk-value">{formatValue(card.data?.value, card.data?.unit ?? card.unit)}</p>
                <p className={`change ${tone}`}>{typeof change === "number" ? formatChange(change) : "No data"}</p>
                <span className="ticker-meta">{card.data?.status ?? "unavailable"}</span>
              </article>
            );
          })}
        </div>
      </section>

      <section className="section" id="signals">
        <div className="section-header">
          <h2>Signals</h2>
          <span className="section-meta">Updated {signals?.updated_at ?? "Loading..."}</span>
        </div>
        <div className="grid signals-grid">
          {signals?.signals?.length
            ? signals.signals.map((signal) => {
                const confidence = typeof signal.confidence === "number" ? Math.round(signal.confidence > 1 ? signal.confidence : signal.confidence * 100) : 0;
                const longPct = typeof signal.long_pct === "number" ? signal.long_pct : 50;
                const shortPct = typeof signal.short_pct === "number" ? signal.short_pct : 50;
                const directionLabel = signal.direction_label ?? `${longPct}% long / ${shortPct}% short`;
                const bias = signal.bias ?? (longPct > 55 ? "LONG" : shortPct > 55 ? "SHORT" : "FLAT");
                const bullets = signal.bullets ?? ["Insufficient data"]; 
                return (
                  <article key={signal.ticker} className="panel card signal-card fade-up">
                    <div className="card-row">
                      <p className="label">{signal.ticker}</p>
                      <span className={`status ${bias.toLowerCase()}`}>{bias}</span>
                    </div>
                    <p className="direction-label">{directionLabel}</p>
                    <div className="confidence-track" role="presentation">
                      <div className="confidence-fill" style={{ width: `${confidence}%` }} />
                    </div>
                    <p className="signal-confidence">Confidence: {confidence}%</p>
                    <ul className="signal-reasons">
                      {bullets.slice(0, 5).map((reason) => <li key={reason}>{reason}</li>)}
                    </ul>
                  </article>
                );
              })
            : Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
        <p className="disclaimer">
          {signals?.missing_inputs?.length
            ? `Some macro sources disabled: ${signals.missing_inputs.join("; ")}`
            : signals?.errors?.length
            ? `Signals warnings: ${signals.errors.join("; ")}`
            : "Not financial advice"}
        </p>
      </section>

      

      <section className="section debug-wrap">
        <button type="button" className="chart-btn" onClick={() => setDebugMode((v) => !v)}>
          {debugMode ? "Hide debug" : "Show debug"}
        </button>
        <div className={`debug-accordion ${debugMode ? "open" : ""}`}>
          <pre className="debug-panel">{JSON.stringify({ signals, macroLatest: macroLatest.slice(0, 5) }, null, 2)}</pre>
        </div>
      </section>
      </main>

      <section className="view-page page-news panel fade-up" aria-label="News view placeholder">
        <div className="news-header-row">
          <h2>Economic / News Calendar</h2>
          <span className="section-meta">Updated {newsPayload.updated_at ?? "—"}</span>
        </div>
        {newsCacheInfo ? (
          <div className="news-cache-row">
            <span className={`cache-badge ${newsCacheInfo.isStale ? "stale" : "cached"}`}>
              {newsFromCache ? "Cached" : "Updated"} {formatCacheTime(newsCacheInfo.fetchedAt)}
            </span>
            {newsLoading && (newsPayload.events ?? []).length ? <span className="muted">Refreshing…</span> : null}
            {newsRefreshError ? <span className="muted warning">{newsRefreshError}</span> : null}
          </div>
        ) : null}
        <p className="news-range-label">
          {resolveNewsRange(newsFilters.range).start} → {resolveNewsRange(newsFilters.range).end}
        </p>
        <div className="news-filters">
          <select value={newsFilters.range} onChange={(e) => setNewsFilters((p) => ({ ...p, range: e.target.value }))}>
            <option value="6m_before">6M before</option>
            <option value="3m_before">3M before</option>
            <option value="1m_before">1M before</option>
            <option value="2w_before">2W before</option>
            <option value="1w_before">1W before</option>
            <option value="today">Today</option>
            <option value="this_week">This week</option>
            <option value="this_month">This month</option>
          </select>
          <select value={newsFilters.country} onChange={(e) => setNewsFilters((p) => ({ ...p, country: e.target.value }))}>
            <option value="ALL">All countries</option>
            <option value="US">US</option>
            <option value="EU">EU</option>
            <option value="UK">UK</option>
            <option value="JP">JP</option>
          </select>
          <select value={newsFilters.importance} onChange={(e) => setNewsFilters((p) => ({ ...p, importance: e.target.value }))}>
            <option value="ALL">All importance</option>
            <option value="HIGH">High</option>
            <option value="MED">Medium</option>
            <option value="LOW">Low</option>
          </select>
          <select value={newsFilters.status} onChange={(e) => setNewsFilters((p) => ({ ...p, status: e.target.value }))}>
            <option value="ALL">All status</option>
            <option value="UPCOMING">Upcoming</option>
            <option value="RELEASED">Released</option>
          </select>
          <input
            value={newsFilters.search}
            onChange={(e) => setNewsFilters((p) => ({ ...p, search: e.target.value }))}
            placeholder="Search event"
          />
        </div>
        {newsError ? <div className="terminal-state error">{newsError}</div> : null}
        <div className="news-table-wrap">
          <div className="news-table-head">
            <span>Date/Time</span><span>Event</span><span>Country</span><span>Importance</span><span>Previous</span><span>Forecast</span><span>Actual</span><span>Status</span><span>Impact</span>
          </div>
          {(newsPayload.events ?? []).slice(0, newsPage * NEWS_PAGE_SIZE).map((event) => (
            <div className="news-row" key={event.id}>
              <span>{event.datetime_local}</span>
              <span>{event.title}</span>
              <span>{event.country}</span>
              <span>{event.importance}</span>
              <span>{event.previous ?? "—"}</span>
              <span>{event.forecast ?? "—"}</span>
              <span>{event.actual ?? "—"}</span>
              <span className={`status ${event.status === "RELEASED" ? "long" : "flat"}`}>{event.status}</span>
              <span className="impact-badges">
                {event.status === "RELEASED" ? Object.entries(event.impacts ?? {}).map(([ticker, windows]) => {
                  const w = windows["15m"] ?? windows["1h"] ?? windows["1d"];
                  if (!w) return null;
                  return <em key={ticker} className={`impact-chip ${w.direction.toLowerCase()}`}>{ticker}: {typeof w.move === "number" ? `${w.move.toFixed(2)}%` : "N/A"}</em>;
                }) : "—"}
              </span>
            </div>
          ))}
          {newsPayload.events && newsPayload.events.length > newsPage * NEWS_PAGE_SIZE ? (
            <button type="button" className="chart-btn" onClick={() => setNewsPage((p) => p + 1)}>
              Load more
            </button>
          ) : null}
          {newsLoading ? <div className="muted">Refreshing news…</div> : null}
          {!newsLoading && !(newsPayload.events ?? []).length ? <div className="muted">No events in selected range.</div> : null}
        </div>
      </section>

      </div>
      </div>

      {selectedIndicator && <ChartModal indicator={selectedIndicator} mode="macro" onClose={() => setSelectedIndicator(null)} />}
      {selectedTicker && <ChartModal indicator={selectedTicker} mode="price" onClose={() => setSelectedTicker(null)} />}
    </div>
  );
}

export default App;
