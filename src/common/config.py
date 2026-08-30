"""Application configuration, loaded from environment variables / .env.

A single Settings object is shared across services (ingestion, consumer, ai,
dashboard). Each service only reads the fields it needs.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- OpenSky ---
    opensky_client_id: str | None = None
    opensky_client_secret: str | None = None
    opensky_base_url: str = "https://opensky-network.org/api"
    opensky_token_url: str = (
        "https://auth.opensky-network.org/auth/realms/opensky-network"
        "/protocol/openid-connect/token"
    )
    opensky_lamin: float = 29.45
    opensky_lamax: float = 33.35
    opensky_lomin: float = 34.25
    opensky_lomax: float = 35.90
    poll_interval_seconds: float = 12.0

    # --- RabbitMQ ---
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"
    rabbitmq_vhost: str = "/"
    rabbitmq_exchange: str = "flights_exchange"
    rabbitmq_routing_key: str = "flights.raw"
    rabbitmq_queue: str = "flights.raw"

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "flighthub"
    postgres_user: str = "flighthub"
    postgres_password: str = "flighthub"

    # --- Open-Meteo (weather enrichment) ---
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    # Flights are bucketed onto a lat/lon grid of this size (degrees) so
    # nearby aircraft share a single cached weather lookup. 1.0deg (~111km)
    # keeps a country-sized polling bbox (thousands of aircraft) down to a
    # few hundred grid cells, so Open-Meteo's own rate limit isn't the
    # bottleneck; still plenty granular for a demo-scale weather overlay.
    weather_grid_size_deg: float = 1.0
    weather_cache_ttl_seconds: float = 600.0

    # --- Consumer ---
    consumer_prefetch_count: int = 40
    # Weather lookups are the bottleneck (one blocking HTTP call per
    # uncached grid cell); a thread pool lets several run concurrently
    # instead of serially, so throughput scales with a large polling bbox.
    consumer_worker_threads: int = 8

    # --- AI layer ---
    llm_provider: Literal["gemini", "groq"] = "gemini"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"

    # --- Dashboard ---
    dashboard_refresh_seconds: float = 15.0
    # If set, the dashboard requires this password before rendering anything.
    # Leave unset for local development; set it before public deployment.
    dashboard_password: str | None = None

    # --- Logging ---
    log_level: str = "INFO"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def get_settings() -> Settings:
    """Build a fresh Settings instance from the current environment."""
    return Settings()
