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
    outbox_publish_timeout_seconds: float = 10.0

    worker_retry_base_delay_seconds: float = 2.0
    worker_retry_max_delay_seconds: float = 30.0

    # How often the API refreshes the job-lifecycle Prometheus gauges
    # (setu_jobs_pending/processing/completed/failed) from Postgres.
    metrics_poll_interval_seconds: float = 5.0

    # Jaeger's OTLP/grpc receiver. Like kafka_bootstrap_servers above, this
    # is the host-side address -- the API and workers run on the host via
    # uv, not in compose.
    otel_exporter_otlp_endpoint: str = "localhost:4317"
    # Off in environments with no Jaeger/OTLP collector reachable (e.g. a
    # cloud deploy) -- tracing wouldn't crash without one (the exporter
    # batches in a background thread), but it would spam connection-refused
    # errors into the logs forever.
    tracing_enabled: bool = True

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so settings are parsed once per process."""
    return Settings()
