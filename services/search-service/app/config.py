from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_main_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/main_db"
    redis_url: str = "redis://localhost:6379/0"
    ai_orchestrator_grpc: str = "ai-orchestrator:50055"
    grpc_port: int = 50053

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
