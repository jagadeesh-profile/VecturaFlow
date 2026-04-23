"""
VecturaFlow — Centralised configuration.
All settings are loaded from environment variables (or .env file).
Never access os.environ directly anywhere else in the codebase — import settings.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str
    embedding_model: str = "text-embedding-3-small"
    generation_model: str = "gpt-4o-mini"

    # ── Pinecone ──────────────────────────────────────────────────────────────
    pinecone_api_key: str
    pinecone_index: str = "vecturaflow"
    pinecone_region: str = "us-east-1"

    # ── AWS ───────────────────────────────────────────────────────────────────
    aws_default_region: str = "us-east-1"
    ingestion_bucket: str
    ingestion_queue_url: str
    embedding_queue_url: str
    registry_table: str = "vecturaflow-registry"
    keys_table: str = "vecturaflow-keys"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379

    # ── API ───────────────────────────────────────────────────────────────────
    api_env: str = "development"
    api_debug: bool = False
    api_dev_bypass: bool = False
    api_title: str = "VecturaFlow API"
    api_version: str = "1.0.0"

    # ── RAG ───────────────────────────────────────────────────────────────────
    retrieval_top_k: int = 5
    retrieval_score_threshold: float = 0.70
    chunk_size: int = 512
    chunk_overlap: int = 50

    # ── Rate limiting ─────────────────────────────────────────────────────────
    rate_limit_per_minute: int = 60

    @property
    def is_production(self) -> bool:
        return self.api_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance — only parsed once per process."""
    return Settings()


# Module-level singleton for direct imports
settings = get_settings()
