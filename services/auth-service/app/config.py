from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_auth_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/auth_db"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 60
    jwt_refresh_ttl_days: int = 30
    grpc_port: int = 50051

    # Сырое значение ADMIN_EMAILS: email-адреса через запятую.
    # Разбирается в множество через свойство admin_emails ниже.
    admin_emails_raw: str = Field(default="", alias="ADMIN_EMAILS")

    model_config = {"env_file": ".env", "extra": "ignore", "populate_by_name": True}

    @cached_property
    def admin_emails(self) -> set[str]:
        """Множество lowercase email-адресов, автоматически повышаемых до роли admin."""
        return {e.strip().lower() for e in self.admin_emails_raw.split(",") if e.strip()}


settings = Settings()
