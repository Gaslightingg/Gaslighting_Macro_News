# Market data troubleshooting

## Price request path

`GET /api/prices` calls `get_prices_payload()`, which refreshes every configured ticker through the provider chains in `backend/app/services/provider_map.py`.

| Ticker id | Dashboard label | Latest provider chain | Main HTTP requests |
| --- | --- | --- | --- |
| `sp500` | S&P500 | Stooq `^spx` → Yahoo `^GSPC` → Yahoo `SPY` | `https://stooq.com/q/d/l/?s=%5Espx&i=d`, `https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d&range=5d`, `https://query2.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d&range=5d`, then `SPY` equivalents |
| `nas100` | NAS100 | Stooq `^ndx` → Yahoo `^NDX` → Yahoo `QQQ` | `https://stooq.com/q/d/l/?s=%5Endx&i=d`, Yahoo chart URLs for `^NDX` and `QQQ` |
| `nqmini` | NASDAQ mini | Yahoo `MNQ=F` → Yahoo `NQ=F` → Stooq `^ndx` | Yahoo chart URLs for `MNQ=F`/`NQ=F`, then the Stooq CSV URL for `^ndx` |
| `eurusd` | EUR/USD | Frankfurter `EUR/USD` → Stooq `eurusd` → Stooq `eurusd.f` → Yahoo `EURUSD=X` | `https://api.frankfurter.app/latest?from=EUR&to=USD`, previous-date Frankfurter URLs, Stooq CSV URLs, Yahoo chart URL |
| `gbpusd` | GBP/USD | Frankfurter `GBP/USD` → Stooq `gbpusd` → Stooq `gbpusd.f` → Yahoo `GBPUSD=X` | Frankfurter latest/previous URLs, Stooq CSV URLs, Yahoo chart URL |
| `gbpjpy` | GBP/JPY | Frankfurter `GBP/JPY` → Stooq `gbpjpy` → Stooq `gbpjpy.f` → Yahoo `GBPJPY=X` | Frankfurter latest/previous URLs, Stooq CSV URLs, Yahoo chart URL |
| `xauusd` | XAUUSD | Stooq `xauusd` → Stooq `xauusd.f` → Yahoo `XAUUSD=X` → Yahoo `GC=F` | Stooq CSV URLs, Yahoo chart URLs |

## Interpreting common errors

- `stooq_request_failed`: the HTTP request to `https://stooq.com/q/d/l/` did not complete successfully after retries. Check the new logs for the exact URL, response status, and body snippet. Network/DNS/proxy failures are logged as `Price provider connection failed`.
- `rate_limited_cooldown`: Yahoo returned HTTP 429 for one of the chart endpoints. The code temporarily cools down that Yahoo symbol and the global Yahoo provider to avoid hammering the endpoint, then continues to the next configured provider.
- `No reliable data available`: all configured live providers failed and there was no fresh/stale DB cache available for that ticker.

## Required macro environment variables

Macro data is intentionally disabled without API credentials. Add these to the repository `.env` or `backend/.env` file:

```dotenv
MARKET_DATA_PROVIDER=auto
FRED_API_KEY=replace_with_your_fred_api_key
BEA_API_KEY=replace_with_your_bea_api_key
FRED_PMI_SERIES_ID=NAPM
```

`BEA_USER_ID` is also accepted as an alias for `BEA_API_KEY`.
