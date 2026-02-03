# Gaslighting Macro News

A lightweight dashboard with a FastAPI backend and a Vite + React frontend.

## Project structure

- `backend/` — FastAPI service with modular structure:
  - `app/api` — API routes.
  - `app/models` — Pydantic response models.
  - `app/providers` — Data provider abstractions (mock + real sources).
  - `app/services` — Mock data providers and signal builders.
  - `app/utils` — Logging, settings, and database utilities.
- `frontend/` — Vite + React dashboard UI.

## Environment variables

Copy the sample file and adjust as needed:

```bash
cp .env.example .env
```

Key variables for real data mode:

- `MARKET_DATA_PROVIDER=real` to enable live data fetching.
- `FRED_API_KEY` and `BEA_API_KEY` for macro data.
- `MARKET_DATA_DB_PATH` for the local SQLite cache.
- `BACKEND_CORS_ORIGINS` (comma-separated) to allow frontend origins (default `*`).
- `VITE_API_URL` to override the API base URL in the frontend (defaults to the current host on port 8000).

### Data sources

The real provider pulls market data from:

- **Stooq** (CSV feed) for prices when available.
- **yFinance** as a fallback for prices.
- **FRED** for most macro series (CPI, labor, rates, VIX, DXY).
- **BEA** for PCE (falls back to FRED when BEA is unavailable).

All fetched values are cached in a local SQLite database so the dashboard can still render if a source is unavailable.

## Run the backend (PyCharm)

1. Open the repository in PyCharm.
2. Configure a Python interpreter (recommended: project venv).
3. Install dependencies:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

4. Create a **Run Configuration** in PyCharm:
   - **Module name**: `uvicorn`
   - **Parameters**: `app.main:app --reload --host 0.0.0.0 --port 8000`
   - **Working directory**: `<repo>/backend`

5. Run the configuration and confirm the API responds at `http://localhost:8000/api/health` (use `localhost`, not `0.0.0.0`, in your browser).

## Run the frontend (PyCharm)

1. In PyCharm, open the **Terminal** tab.
2. Install dependencies:

```bash
cd frontend
npm install
```

3. Create a **Run Configuration** (Node.js):
   - **JavaScript file**: `<repo>/frontend/node_modules/vite/bin/vite.js`
   - **Application parameters**: `--host 0.0.0.0 --port 5173`
   - **Working directory**: `<repo>/frontend`

4. Start the configuration and open `http://localhost:5173`.

## Run everything with a single script

If you prefer a single command locally, run the helper script:

```bash
python run_app.py
```

This script starts the FastAPI backend and the Vite dev server (requires `npm` on your PATH).

## API routes

- `GET /api/prices`
- `GET /api/macro`
- `GET /api/signals`
- `GET /api/health`

`/api/prices` returns tickers for S&P500, NAS100, NASDAQ mini, EUR/USD, GBP/USD, GBP/JPY, and XAUUSD.
`/api/macro` returns an `as_of` date plus a `series` list with value, change, and last-updated fields per indicator.
`/api/signals` returns rule-based signals per ticker with direction, confidence, three reasons, and a “Not financial advice” disclaimer.

## Testing limitations

Automated dependency installation and tests may fail in isolated environments (e.g. Codex/CI sandboxes without access to npm or PyPI). The project is intended for local development and validation in PyCharm with full internet access.
