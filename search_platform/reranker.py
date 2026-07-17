"""Cross-encoder re-ranking: scores (query, candidate_text) pairs jointly,
which is far more precise than the bi-encoder cosine similarity used for
first-pass retrieval — at the cost of being too slow to run over the whole
index, hence "retrieve broad, rerank narrow"."""
import threading

import numpy as np
from sentence_transformers import CrossEncoder

from .config import get_settings

_lock = threading.Lock()
_model: CrossEncoder | None = None


def get_reranker() -> CrossEncoder:
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                settings = get_settings()
                _model = CrossEncoder(settings.reranker_model)
    return _model


def rerank(query: str, candidates: list[tuple[str, str]]) -> dict[str, float]:
    """
    candidates: [(id, text)]. Returns {id: rerank_score in [0,1]}.
    """
    if not candidates:
        return {}
    model = get_reranker()
    pairs = [(query, text) for _, text in candidates]
    raw_scores = model.predict(pairs)
    # ms-marco cross-encoders output unbounded logits — squash to [0,1].
    scores = 1 / (1 + np.exp(-np.asarray(raw_scores, dtype=np.float64)))
    return {cid: float(s) for (cid, _), s in zip(candidates, scores)}
