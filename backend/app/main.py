from fastapi import FastAPI

app = FastAPI(title="Gaslighting Macro News API")


@app.get("/api/prices")
async def get_prices():
    return {
        "as_of": "2024-03-01T12:00:00Z",
        "tickers": [
            {"symbol": "SPX", "price": 4988.4, "change_pct": 0.8},
            {"symbol": "NDX", "price": 17640.2, "change_pct": 1.2},
            {"symbol": "DXY", "price": 103.7, "change_pct": -0.3},
            {"symbol": "WTI", "price": 78.1, "change_pct": 0.5},
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
