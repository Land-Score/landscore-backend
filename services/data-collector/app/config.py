from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    rosreestr_mode: str = "mock"
    rosreestr_api_url: str = "https://nspd.gov.ru/api"
    rosreestr_timeout: float = 30.0
    rosreestr_verify_ssl: bool = True
    rosreestr_user_agent: str = "LandScoreAI/0.1 (+https://landscore.local)"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
