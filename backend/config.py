from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://pipeline_user:pipeline_pass@localhost:5432/article_pipeline"
    sync_database_url: str = "postgresql://pipeline_user:pipeline_pass@localhost:5432/article_pipeline"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    llm_api_key: str = ""
    llm_api_url: str = "https://api.openai.com/v1/chat/completions"
    llm_model: str = "gpt-4o-mini"

    redis_max_memory: str = "256mb"
    redis_cache_ttl: int = 3600
    redis_max_urls: int = 100_000

    scrape_timeout: int = 30
    max_retries: int = 3

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
