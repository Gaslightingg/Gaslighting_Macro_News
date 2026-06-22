from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_ROOT_DIR / ".env"), str(_ROOT_DIR / "backend" / ".env")),
        env_file_encoding="utf-8",
        extra="forbid",
    )

    backend_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("BACKEND_HOST", "backend_host"),
    )
    backend_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("BACKEND_PORT", "backend_port"),
    )
    vite_api_url: str = Field(
        default="http://localhost:8000",
        validation_alias=AliasChoices("VITE_API_URL", "vite_api_url"),
    )
    market_data_provider: str = Field(default="auto", validation_alias="MARKET_DATA_PROVIDER")
    fred_api_key: Optional[str] = Field(default=None, validation_alias="FRED_API_KEY")
    bea_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("BEA_API_KEY", "BEA_USER_ID"),
    )
    fred_pmi_series_id: str = Field(default="NAPM", validation_alias="FRED_PMI_SERIES_ID")
    database_path: str = Field(
        default="backend/data/market_data.sqlite3",
        validation_alias="MARKET_DATA_DB_PATH",
    )
    request_timeout: float = Field(default=10.0, validation_alias="REQUEST_TIMEOUT")
    cors_origins: str = Field(default="*", validation_alias="BACKEND_CORS_ORIGINS")
    cache_db_url: str = Field(default="sqlite:///./cache.db", validation_alias="CACHE_DB_URL")
    cache_ttl_latest: int = Field(default=1800, validation_alias="CACHE_TTL_LATEST")
    cache_ttl_series_1y: int = Field(default=21600, validation_alias="CACHE_TTL_SERIES_1Y")
    cache_ttl_series_5y: int = Field(default=43200, validation_alias="CACHE_TTL_SERIES_5Y")
    cache_ttl_prices: int = Field(default=180, validation_alias=AliasChoices("CACHE_TTL_PRICES", "CACHE_TTL_PRICES_SECONDS"))
    cache_ttl_signals: int = Field(default=600, validation_alias="CACHE_TTL_SIGNALS")
    cache_ttl_macro_seconds: int = Field(default=1800, validation_alias="CACHE_TTL_MACRO_SECONDS")
    price_fetch_timeout_seconds: float = Field(default=8.0, validation_alias="PRICE_FETCH_TIMEOUT_SECONDS")
    price_fetch_concurrency: int = Field(default=8, validation_alias="PRICE_FETCH_CONCURRENCY")
    allow_seed_prices: bool = Field(default=False, validation_alias="ALLOW_SEED_PRICES")
    debug_price_fetch: bool = Field(default=False, validation_alias="DEBUG_PRICE_FETCH")
    news_provider: str = Field(default="auto", validation_alias="NEWS_PROVIDER")
    tradingeconomics_api_key: Optional[str] = Field(default=None, validation_alias="TRADINGECONOMICS_API_KEY")
    news_timezone: str = Field(default="Europe/Moscow", validation_alias="NEWS_TIMEZONE")
    telegram_enabled: bool = Field(default=False, validation_alias="TELEGRAM_ENABLED")
    telegram_bot_token: Optional[str] = Field(default=None, validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_user_id: Optional[int] = Field(default=None, validation_alias="TELEGRAM_ALLOWED_USER_ID")
    telegram_allowed_chat_id: Optional[int] = Field(default=None, validation_alias="TELEGRAM_ALLOWED_CHAT_ID")
    api_base_url: str = Field(default="http://127.0.0.1:8000", validation_alias="API_BASE_URL")

    @field_validator("telegram_allowed_user_id", "telegram_allowed_chat_id", mode="before")
    @classmethod
    def _empty_optional_int_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def resolved_database_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        return path

    def parsed_cors_origins(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
