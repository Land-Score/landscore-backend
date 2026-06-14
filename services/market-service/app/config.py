from __future__ import annotations

import os

try:
    from pydantic_settings import BaseSettings
except ImportError:  # Allows local parser scripts to run before service deps are installed.
    BaseSettings = None


def _as_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


if BaseSettings is not None:
    class Settings(BaseSettings):
        clickhouse_host: str = "clickhouse"
        # Native protocol port from .env (used by clickhouse-client). clickhouse-connect
        # talks the HTTP interface, so it uses clickhouse_http_port below.
        clickhouse_port: int = 9000
        clickhouse_http_port: int = 8123
        clickhouse_db: str = "market"
        clickhouse_user: str = "default"
        clickhouse_password: str = ""

        clickhouse_connect_retries: int = 30
        clickhouse_connect_backoff: float = 2.0

        market_seed_enabled: bool = True
        market_seed_count: int = 4000

        model_config = {"env_file": ".env", "extra": "ignore"}
else:
    class Settings:
        clickhouse_host: str = os.getenv("CLICKHOUSE_HOST", "clickhouse")
        clickhouse_port: int = int(os.getenv("CLICKHOUSE_PORT", "9000"))
        clickhouse_http_port: int = int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123"))
        clickhouse_db: str = os.getenv("CLICKHOUSE_DB", "market")
        clickhouse_user: str = os.getenv("CLICKHOUSE_USER", "default")
        clickhouse_password: str = os.getenv("CLICKHOUSE_PASSWORD", "")

        clickhouse_connect_retries: int = int(os.getenv("CLICKHOUSE_CONNECT_RETRIES", "30"))
        clickhouse_connect_backoff: float = float(os.getenv("CLICKHOUSE_CONNECT_BACKOFF", "2"))

        market_seed_enabled: bool = _as_bool(os.getenv("MARKET_SEED_ENABLED", "true"))
        market_seed_count: int = int(os.getenv("MARKET_SEED_COUNT", "4000"))


settings = Settings()
