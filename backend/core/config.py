"""Application settings, loaded from environment / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "setu"
    environment: Literal["local", "test", "production"] = "local"
    debug: bool = False

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://setu:setu@localhost:5432/setu",
        description="Async SQLAlchemy DSN; must use the asyncpg driver.",
    )
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 5

    # Redpanda advertises 19092 to the host and 9092 inside the compose
    # network; this default is for processes running on the host via uv.
    kafka_bootstrap_servers: str = "localhost:19092"

    outbox_poll_interval_seconds: float = 2.0
    outbox_batch_size: int = 50
    outbox_max_publish_attempts: int = 10

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so settings are parsed once per process."""
    return Settings()
