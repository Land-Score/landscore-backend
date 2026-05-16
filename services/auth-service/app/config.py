from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_auth_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/auth_db"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_minutes: int = 60
    jwt_refresh_ttl_days: int = 30
    grpc_port: int = 50051

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
