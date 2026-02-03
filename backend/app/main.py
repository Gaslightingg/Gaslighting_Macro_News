from fastapi import FastAPI

app = FastAPI(title="Gaslighting Macro News API")


@app.get("/api/prices")
async def get_prices():
    return {
        "as_of": "2024-03-01T12:00:00Z",
        "tickers": [
            {"symbol": "S&P500", "price": 5041.3, "change_pct": 0.62},
            {"symbol": "NAS100", "price": 17812.9, "change_pct": 1.08},
            {"symbol": "EUR/USD", "price": 1.0824, "change_pct": -0.18},
            {"symbol": "GBP/USD", "price": 1.2689, "change_pct": 0.24},
            {"symbol": "GBP/JPY", "price": 191.42, "change_pct": -0.41},
            {"symbol": "XAUUSD", "price": 2034.7, "change_pct": 0.73},
        ],
    }


@app.get("/api/macro")
async def get_macro():
    return {
        "highlights": [
            {"label": "CPI YoY", "value": "3.1%"},
            {"label": "Core PCE", "value": "2.8%"},
            {"label": "Fed Funds", "value": "5.25-5.50%"},
            {"label": "Payrolls", "value": "+198k"},
        ],
        "commentary": "Inflation continues to cool while growth remains steady.",
    }


@app.get("/api/signals")
async def get_signals():
    return {
        "signals": [
            {
                "name": "Risk Appetite",
                "status": "Bullish",
                "details": "Credit spreads tightened and cyclicals led the tape.",
            },
            {
                "name": "Liquidity Pulse",
                "status": "Neutral",
                "details": "Treasury issuance absorbed without material pressure.",
            },
            {
                "name": "Macro Surprise",
                "status": "Constructive",
                "details": "Economic data outperformed consensus for a third week.",
            },
        ]
    }
