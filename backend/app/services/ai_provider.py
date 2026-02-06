from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AiPrediction:
    signal: str
    confidence: float
    notes: str | None = None
    params: dict | None = None


class AiProvider(Protocol):
    async def predict(self, context: dict) -> AiPrediction | None:
        ...
