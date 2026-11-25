"""Application settings loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_nested_delimiter="__",
    )

    app_name: str = "api-gateway"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"

    mongodb_uri: str = Field(
        default="mongodb://gateway:change-me-in-production@127.0.0.1:27017/api_gateway?authSource=admin"
    )
    mongodb_database: str = "api_gateway"
    mongodb_cache_collection: str = "response_cache"
    mongo_server_selection_timeout_ms: int = Field(default=5000, ge=500)
    mongo_startup_attempts: int = Field(default=10, ge=1)
    mongo_startup_retry_delay_seconds: float = Field(default=2.0, ge=0.1)

    default_cache_ttl_seconds: int = Field(default=300, ge=1)
    # TTL index deletes documents when purge_at passes; this grace period keeps
    # expired responses available for the stale_cache fallback.
    stale_cache_retention_seconds: int = Field(default=86_400, ge=0)
    stale_cache_fallback_enabled: bool = True
    exchange_rates_ttl_seconds: int = Field(default=3600, ge=1)
    slow_repository_ttl_seconds: int = Field(default=60, ge=1)

    http_timeout_seconds: float = Field(default=5.0, gt=0)
    http_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    http_max_connections: int = Field(default=100, ge=1)
    http_max_keepalive_connections: int = Field(default=20, ge=1)
    exchange_rates_base_url: str = "https://open.er-api.com/v6"
    slow_connector_delay_seconds: float = Field(default=2.0, ge=0)
    enable_failure_simulation: bool = True

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in _LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}")
        return level

    @field_validator("mongodb_uri")
    @classmethod
    def validate_mongodb_uri(cls, value: str) -> str:
        if not value.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("MONGODB_URI must start with mongodb:// or mongodb+srv://")
        return value

    @field_validator("exchange_rates_base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("EXCHANGE_RATES_BASE_URL must be an absolute http(s) URL")
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings snapshot."""
    return Settings()
