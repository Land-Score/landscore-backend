from __future__ import annotations

import os

try:
    from pydantic_settings import BaseSettings
except ImportError:  # Allows local parser scripts to run before service deps are installed.
    BaseSettings = None


if BaseSettings is not None:
    class Settings(BaseSettings):
        rosreestr_mode: str = "mock"
        rosreestr_api_url: str = "https://nspd.gov.ru/api"
        rosreestr_timeout: float = 30.0
        rosreestr_verify_ssl: bool = False
        nspd_insecure_tls: bool = True
        nspd_resolve_ip: str = ""
        nspd_force_ipv4: bool = True
        rosreestr_user_agent: str = "LandScoreAI/0.1 (+https://landscore.local)"
        source_timeout: float = 12.0
        market_search_enabled: bool = False
        nspd_map_layers_enabled: bool = True
        nspd_map_layers_timeout: float = 30.0
        nspd_child_lookup_concurrency: int = 64
        nspd_child_lookup_limit: int = 160
        nspd_child_lookup_timeout: float = 100.0
        nspd_child_lookup_total_timeout: float = 100.0

        model_config = {"env_file": ".env", "extra": "ignore"}
else:
    class Settings:
        rosreestr_mode: str = os.getenv("ROSREESTR_MODE", "mock")
        rosreestr_api_url: str = os.getenv("ROSREESTR_API_URL", "https://nspd.gov.ru/api")
        rosreestr_timeout: float = float(os.getenv("ROSREESTR_TIMEOUT", "30"))
        nspd_insecure_tls: bool = os.getenv("NSPD_INSECURE_TLS", "true").lower() in {"1", "true", "yes"}
        nspd_resolve_ip: str = os.getenv("NSPD_RESOLVE_IP", os.getenv("ROSREESTR_RESOLVE_IP", ""))
        nspd_force_ipv4: bool = os.getenv("NSPD_FORCE_IPV4", "true").lower() in {"1", "true", "yes"}
        rosreestr_verify_ssl: bool = os.getenv(
            "ROSREESTR_VERIFY_SSL",
            "false" if nspd_insecure_tls else "true",
        ).lower() not in {"0", "false", "no"}
        rosreestr_user_agent: str = os.getenv("ROSREESTR_USER_AGENT", "LandScoreAI/0.1 (+https://landscore.local)")
        source_timeout: float = float(os.getenv("SOURCE_TIMEOUT", "12"))
        market_search_enabled: bool = os.getenv("MARKET_SEARCH_ENABLED", "false").lower() in {"1", "true", "yes"}
        nspd_map_layers_enabled: bool = os.getenv("NSPD_MAP_LAYERS_ENABLED", "true").lower() in {"1", "true", "yes"}
        nspd_map_layers_timeout: float = float(os.getenv("NSPD_MAP_LAYERS_TIMEOUT", "30"))
        nspd_child_lookup_concurrency: int = int(os.getenv("NSPD_CHILD_LOOKUP_CONCURRENCY", "64"))
        nspd_child_lookup_limit: int = int(os.getenv("NSPD_CHILD_LOOKUP_LIMIT", "160"))
        nspd_child_lookup_timeout: float = float(os.getenv("NSPD_CHILD_LOOKUP_TIMEOUT", "100"))
        nspd_child_lookup_total_timeout: float = float(os.getenv("NSPD_CHILD_LOOKUP_TOTAL_TIMEOUT", "100"))


settings = Settings()
