"""In-process BM25 keyword index over `searchable_text`, rebuilt via refresh()."""
from typing import Optional

import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import UserProfile


class BM25Index:
    def __init__(self):
        self._ids: list[str] = []
        self._bm25: Optional[BM25Okapi] = None

    def refresh(self, db: Session) -> None:
        rows = db.execute(select(UserProfile.id, UserProfile.searchable_text)).all()
        self._ids = [str(r[0]) for r in rows]
        corpus = [(r[1] or "").lower().split() for r in rows]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, top_k: int, candidate_ids: Optional[set] = None) -> list[tuple[str, float]]:
        if self._bm25 is None or not self._ids:
            return []
        scores = self._bm25.get_scores(query.lower().split())
        max_score = scores.max() if len(scores) else 0
        if max_score > 0:
            scores = scores / max_score
        if candidate_ids is not None:
            mask = np.array([uid in candidate_ids for uid in self._ids])
            scores = np.where(mask, scores, -1.0)
        top_k = min(top_k, len(self._ids))
        top_idx = np.argpartition(-scores, top_k - 1)[:top_k]
        top_idx = top_idx[np.argsort(-scores[top_idx])]
        return [(self._ids[i], float(scores[i])) for i in top_idx if scores[i] > -1.0]

    def score_for_id(self, query: str, uid: str) -> float:
        """Normalized BM25 score for one specific document — used by the
        on-demand /explain path, which needs a single candidate's score
        without running a full top-k search."""
        if self._bm25 is None or uid not in self._ids:
            return 0.0
        idx = self._ids.index(uid)
        scores = self._bm25.get_scores(query.lower().split())
        max_score = scores.max() if len(scores) else 0
        return float(scores[idx] / max_score) if max_score > 0 else 0.0

    def vocabulary(self) -> set[str]:
        if self._bm25 is None:
            return set()
        return set(self._bm25.idf.keys())


_index: Optional[BM25Index] = None


def get_bm25_index() -> BM25Index:
    global _index
    if _index is None:
        _index = BM25Index()
    return _index
