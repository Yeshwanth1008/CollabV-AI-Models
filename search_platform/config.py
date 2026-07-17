"""
Central settings, loaded from Task 2/.env.
VECTOR_BACKEND toggles the vector repository implementation without touching
call sites: "numpy" works with plain Postgres, "pgvector" switches to native
<=> ANN search once the extension is installed (see VECTOR_STORE.md).
"""
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

TASK2_DIR = Path(__file__).resolve().parent.parent
load_dotenv(TASK2_DIR / ".env")


class Settings(BaseSettings):
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://collabv:collabv_dev_pw@127.0.0.1:5432/collabv_search"
    )
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    reranker_model: str = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    embedding_dim: int = 384

    vector_backend: str = os.getenv("VECTOR_BACKEND", "numpy")  # "numpy" | "pgvector"

    cache_ttl_seconds: int = 300
    search_top_k_candidates: int = 50  # candidates pulled before rerank
    search_default_limit: int = 10

    class Config:
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
