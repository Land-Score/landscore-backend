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

    # Redis broker shared with ai-orchestrator (db=1 for tasks)
    celery_broker_url: str = "redis://redis:6379/1"

    allowed_origins: list[str] = ["http://localhost:3000"]
    rate_limit_per_minute: int = 60
    # Stricter limit for expensive write endpoints (создание проверок/поисков)
    rate_limit_write_per_minute: int = 10

    # Alice webhook: shared secret expected in the X-Alice-Secret header.
    # Если пусто — webhook отключён (возвращает 503), чтобы публичная ручка
    # не оставалась без какой-либо аутентификации.
    alice_webhook_secret: str = ""
    # Существующий служебный пользователь, от имени которого создаются проверки
    # из навыка Алисы. Должен присутствовать в auth_db. Если пусто — создание
    # проверок из Алисы отключено (возвращается голосовая подсказка).
    alice_service_user_id: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
