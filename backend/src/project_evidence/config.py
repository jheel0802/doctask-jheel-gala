from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+psycopg://project_evidence:project_evidence@localhost:54329/"
        "project_evidence"
    )
    checkpoint_database_url: str = (
        "postgresql://project_evidence:project_evidence@localhost:54329/"
        "project_evidence?sslmode=disable"
    )
    max_retries_per_stage: int = 1
    max_llm_calls_per_run: int = 16
    max_run_cost_usd: float = 0.25
    embedding_provider: str = "local"
    embedding_dimensions: int = 64
    openai_embedding_model: str = "text-embedding-3-small"
    semantic_candidate_distance: float = 0.38
    llm_provider: str = "fake"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini-2024-07-18"
    openai_base_url: str | None = None
    openai_max_retries: int = 2
    openai_timeout_seconds: float = 60.0
    openai_input_cost_per_million: float = 0.15
    openai_output_cost_per_million: float = 0.60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
