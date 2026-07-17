"""
Vector repository abstraction.

Two implementations behind one interface, selected by VECTOR_BACKEND in .env:

- NumpyVectorRepository: loads every embedding into an in-process NumPy
  matrix and does exact cosine search. No extra infra, fine up to roughly
  hundreds of thousands of profiles on a single node.
- PgVectorRepository: delegates to Postgres via the pgvector extension's
  `<=>` cosine-distance operator with an HNSW index, for ANN search at
  larger scale / lower per-query latency.

Callers (hybrid_search.py) only ever see `.search(query_vec, top_k, ids)` —
swapping backends is a one-line config change, not a rewrite.
"""
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .models import UserProfile


class VectorRepository(ABC):
    @abstractmethod
    def refresh(self, db: Session) -> None:
        """Reload the index from the source of truth (Postgres row data)."""

    @abstractmethod
    def search(self, query_vec: np.ndarray, top_k: int, candidate_ids: Optional[set] = None) -> list[tuple[str, float]]:
        """Return [(user_id_str, cosine_similarity)] sorted descending."""


class NumpyVectorRepository(VectorRepository):
    def __init__(self):
        self._ids: list[str] = []
        self._matrix: Optional[np.ndarray] = None

    def refresh(self, db: Session) -> None:
        rows = db.execute(
            select(UserProfile.id, UserProfile.embedding).where(UserProfile.embedding.isnot(None))
        ).all()
        if not rows:
            self._ids, self._matrix = [], None
            return
        self._ids = [str(r[0]) for r in rows]
        self._matrix = np.asarray([r[1] for r in rows], dtype=np.float32)

    def search(self, query_vec: np.ndarray, top_k: int, candidate_ids: Optional[set] = None) -> list[tuple[str, float]]:
        if self._matrix is None or len(self._ids) == 0:
            return []
        sims = self._matrix @ query_vec  # embeddings are pre-normalized -> dot == cosine
        if candidate_ids is not None:
            mask = np.array([uid in candidate_ids for uid in self._ids])
            sims = np.where(mask, sims, -1.0)
        top_k = min(top_k, len(self._ids))
        top_idx = np.argpartition(-sims, top_k - 1)[:top_k]
        top_idx = top_idx[np.argsort(-sims[top_idx])]
        return [(self._ids[i], float(sims[i])) for i in top_idx if sims[i] > -1.0]


class PgVectorRepository(VectorRepository):
    """
    Active once `retrieval`... i.e. once pgvector is installed and the
    `embedding` column has been migrated to `vector(EMBEDDING_DIM)`.
    """

    def refresh(self, db: Session) -> None:
        return  # no in-process cache to refresh — Postgres is the index

    def search(self, query_vec: np.ndarray, top_k: int, candidate_ids: Optional[set] = None) -> list[tuple[str, float]]:
        from .db import SessionLocal

        vec_literal = "[" + ",".join(f"{x:.6f}" for x in query_vec.tolist()) + "]"
        filter_clause = ""
        params = {"vec": vec_literal, "top_k": top_k}
        if candidate_ids:
            filter_clause = "WHERE id = ANY(:ids)"
            params["ids"] = list(candidate_ids)

        sql = text(f"""
            SELECT id, 1 - (embedding <=> :vec) AS cosine_sim
            FROM user_profiles
            {filter_clause}
            ORDER BY embedding <=> :vec
            LIMIT :top_k
        """)
        with SessionLocal() as db:
            rows = db.execute(sql, params).all()
        return [(str(r[0]), float(r[1])) for r in rows]


_repo: Optional[VectorRepository] = None


def get_vector_repository() -> VectorRepository:
    global _repo
    if _repo is None:
        settings = get_settings()
        _repo = PgVectorRepository() if settings.vector_backend == "pgvector" else NumpyVectorRepository()
    return _repo
