from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"

    auth_grpc: str = "auth-service:50051"
    check_grpc: str = "check-service:50052"
    search_grpc: str = "search-service:50053"
    document_grpc: str = "document-service:50054"
    data_collector_grpc: str = "data-collector:50056"
    geo_grpc: str = "geo-service:50057"
    cadastral_lookup_timeout: float = 180.0

    allowed_origins: list[str] = ["http://localhost:3000"]
    rate_limit_per_minute: int = 60

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
