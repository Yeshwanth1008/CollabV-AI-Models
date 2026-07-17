"""
Run once, after `CREATE EXTENSION vector;` has succeeded (see
INSTALL_PGVECTOR.md). Converts the fallback float[] embedding column to a
native `vector(EMBEDDING_DIM)` column, backfills it, adds an HNSW cosine
index, and flips VECTOR_BACKEND=pgvector in .env so PgVectorRepository
takes over from NumpyVectorRepository — no application code changes.
"""
from pathlib import Path

from sqlalchemy import text

from .config import get_settings
from .db import engine

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def main():
    settings = get_settings()
    dim = settings.embedding_dim

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.execute(text(
            f"ALTER TABLE user_profiles "
            f"ALTER COLUMN embedding TYPE vector({dim}) USING embedding::vector({dim});"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_user_profiles_embedding_hnsw "
            "ON user_profiles USING hnsw (embedding vector_cosine_ops);"
        ))
    print(f"Migrated embedding column to vector({dim}) with HNSW cosine index.")

    env_text = ENV_PATH.read_text(encoding="utf-8")
    if "VECTOR_BACKEND=" in env_text:
        env_text = "\n".join(
            "VECTOR_BACKEND=pgvector" if line.startswith("VECTOR_BACKEND=") else line
            for line in env_text.splitlines()
        ) + "\n"
    else:
        env_text += "\nVECTOR_BACKEND=pgvector\n"
    ENV_PATH.write_text(env_text, encoding="utf-8")
    print("Set VECTOR_BACKEND=pgvector in .env — restart the server to take effect.")


if __name__ == "__main__":
    main()
