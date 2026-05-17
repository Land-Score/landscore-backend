from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    yandex_ai_api_key: str = ""
    yandex_ai_folder_id: str = ""
    yandex_ai_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    yandex_gpt_model: str = "yandexgpt/latest"

    check_grpc: str = "check-service:50052"
    search_grpc: str = "search-service:50053"
    data_collector_grpc: str = "data-collector:50056"
    geo_grpc: str = "geo-service:50057"
    market_grpc: str = "market-service:50058"
    document_grpc: str = "document-service:50054"

    grpc_port: int = 50055

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
