import { useEffect, useState } from "react";
import "./App.css";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const formatChange = (value) => `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
const formatPrice = (value) => (value < 10 ? value.toFixed(4) : value.toFixed(2));

function App() {
  const [prices, setPrices] = useState(null);
  const [macro, setMacro] = useState(null);
  const [signals, setSignals] = useState(null);

  useEffect(() => {
    const load = async () => {
      const [pricesRes, macroRes, signalsRes] = await Promise.all([
        fetch(`${API_BASE}/api/prices`),
        fetch(`${API_BASE}/api/macro`),
        fetch(`${API_BASE}/api/signals`),
      ]);
      setPrices(await pricesRes.json());
      setMacro(await macroRes.json());
      setSignals(await signalsRes.json());
    };

    load();
  }, []);

  return (
    <div className="app">
      <header className="hero">
        <div>
          <p className="eyebrow">Dashboard</p>
          <h1>Gaslighting Macro News</h1>
          <p className="subtitle">
            Cross-asset signals and macro narratives, refreshed every morning.
          </p>
        </div>
        <div className="pill">{prices?.as_of ?? "Loading..."}</div>
      </header>

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
            </article>
          ))}
        </div>
      </section>

      <section className="section">
        <h2>Macro data</h2>
        <div className="grid macro">
          {macro?.highlights?.map((item) => (
            <article key={item.label} className="card">
              <p className="label">{item.label}</p>
              <p className="value">{item.value}</p>
            </article>
          ))}
        </div>
        <p className="commentary">{macro?.commentary ?? "Loading..."}</p>
      </section>

      <section className="section">
        <h2>Signals</h2>
        <div className="grid signals">
          {signals?.signals?.map((signal) => (
            <article key={signal.name} className="card">
              <div className="card-row">
                <p className="label">{signal.name}</p>
                <span className="status">{signal.status}</span>
              </div>
              <p className="details">{signal.details}</p>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

export default App;
