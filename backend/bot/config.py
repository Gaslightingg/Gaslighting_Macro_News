from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class BotConfig:
    token: str
    allowed_user_ids: set[int]
    allowed_chat_id: int | None
    api_base_url: str
    enabled: bool


def _parse_int_set(raw: str) -> set[int]:
    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError:
            continue
    return values


def _parse_enabled(raw: str) -> bool:
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def load_config_optional() -> BotConfig | None:
    load_dotenv()
    enabled = _parse_enabled(os.getenv("TELEGRAM_ENABLED", "true"))
    if not enabled:
        return None
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_ids_raw = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
    allowed_chat_raw = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    api_base_url = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    allowed_ids = _parse_int_set(allowed_ids_raw) if allowed_ids_raw else set()
    allowed_chat_id = int(allowed_chat_raw) if allowed_chat_raw.isdigit() else None
    if not token or not allowed_ids:
        return None
    return BotConfig(
        token=token,
        allowed_user_ids=allowed_ids,
        allowed_chat_id=allowed_chat_id,
        api_base_url=api_base_url,
        enabled=enabled,
    )


def load_config() -> BotConfig:
    config = load_config_optional()
    if config is None or not config.token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")
    if not config.allowed_user_ids:
        raise ValueError("TELEGRAM_ALLOWED_USER_ID is required")
    return config
