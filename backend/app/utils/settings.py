from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    market_data_provider: str = Field(default="mock", validation_alias="MARKET_DATA_PROVIDER")
    fred_api_key: Optional[str] = None
    bea_api_key: Optional[str] = None
    database_path: str = Field(
        default="backend/data/market_data.sqlite3",
        validation_alias="MARKET_DATA_DB_PATH",
    )
    request_timeout: float = Field(default=10.0, validation_alias="REQUEST_TIMEOUT")
    cors_origins: str = Field(default="*", validation_alias="BACKEND_CORS_ORIGINS")

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
