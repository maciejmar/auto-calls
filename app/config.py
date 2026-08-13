from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://auto_calls:auto_calls@db:5432/auto_calls"

    vapi_api_key: str = ""
    vapi_webhook_secret: str = ""
    vapi_webhook_hmac_secret: str = ""
    vapi_api_base_url: str = "https://api.vapi.ai"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    google_service_account_file: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
