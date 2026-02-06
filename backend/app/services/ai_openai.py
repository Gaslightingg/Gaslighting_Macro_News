from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .ai_provider import AiPrediction, AiProvider

logger = logging.getLogger(__name__)


class OpenAIPredictor(AiProvider):
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    async def predict(self, context: dict) -> AiPrediction | None:
        prompt = (
            "You are assisting with a trading backtest. "
            "Return a JSON object with keys: signal (buy|sell|hold), confidence (0..1), "
            "notes (string), params (object)."
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(context)},
            ],
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("OpenAI request failed: %s", exc)
            return None
        data: dict[str, Any] = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )
        if not content:
            return None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        signal = str(parsed.get("signal", "")).lower()
        confidence = float(parsed.get("confidence", 0))
        if signal not in {"buy", "sell", "hold"}:
            return None
        return AiPrediction(
            signal=signal,
            confidence=max(0.0, min(1.0, confidence)),
            notes=parsed.get("notes"),
            params=parsed.get("params") if isinstance(parsed.get("params"), dict) else None,
        )
