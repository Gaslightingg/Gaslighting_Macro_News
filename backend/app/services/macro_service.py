from __future__ import annotations

from ..models.schemas import MacroResponse


def get_macro_payload() -> MacroResponse:
    return MacroResponse(
        as_of="2024-03-01",
        series=[
            {
                "name": "CPI",
                "value": "3.1%",
                "change": "-0.1pp",
                "updated": "2024-02-13",
            },
            {
                "name": "Core CPI",
                "value": "3.3%",
                "change": "0.0pp",
                "updated": "2024-02-13",
            },
            {
                "name": "PCE",
                "value": "2.8%",
                "change": "-0.1pp",
                "updated": "2024-02-29",
            },
            {
                "name": "PMI/ISM",
                "value": "52.4",
                "change": "+0.6",
                "updated": "2024-03-01",
            },
            {
                "name": "NFP",
                "value": "+198k",
                "change": "-31k",
                "updated": "2024-03-08",
            },
            {
                "name": "Unemployment Rate",
                "value": "3.9%",
                "change": "+0.1pp",
                "updated": "2024-03-08",
            },
            {
                "name": "Jobless Claims",
                "value": "212k",
                "change": "-8k",
                "updated": "2024-02-29",
            },
            {
                "name": "Fed Funds Rate",
                "value": "5.25-5.50%",
                "change": "0.0pp",
                "updated": "2024-01-31",
            },
            {
                "name": "US10Y",
                "value": "4.18%",
                "change": "-0.03pp",
                "updated": "2024-03-01",
            },
            {
                "name": "US2Y",
                "value": "4.59%",
                "change": "-0.02pp",
                "updated": "2024-03-01",
            },
            {
                "name": "US30Y",
                "value": "4.33%",
                "change": "-0.01pp",
                "updated": "2024-03-01",
            },
            {
                "name": "VIX",
                "value": "13.4",
                "change": "-0.8",
                "updated": "2024-03-01",
            },
            {
                "name": "DXY",
                "value": "103.7",
                "change": "-0.3",
                "updated": "2024-03-01",
            },
        ],
        commentary="Inflation continues to cool while growth remains steady.",
    )
