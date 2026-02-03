# Gaslighting Macro News

A lightweight dashboard with a FastAPI backend and a Vite + React frontend.

## Project structure

- `backend/` — FastAPI service with modular structure:
  - `app/api` — API routes.
  - `app/models` — Pydantic response models.
  - `app/services` — Mock data providers and signal builders.
  - `app/utils` — Logging utilities.
- `frontend/` — Vite + React dashboard UI.

## Environment variables

Copy the sample file and adjust as needed:

```bash
cp .env.example .env
```

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

5. Run the configuration and confirm the API responds at `http://localhost:8000/api/health`.

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

## API routes

- `GET /api/prices`
- `GET /api/macro`
- `GET /api/signals`
- `GET /api/health`

`/api/prices` returns mock tickers for S&P500, NAS100, EUR/USD, GBP/USD, GBP/JPY, and XAUUSD.
`/api/macro` returns an `as_of` date plus a `series` list with value, change, and last-updated fields per indicator.
`/api/signals` returns rule-based signals per ticker with direction, confidence, three reasons, and a “Not financial advice” disclaimer.
