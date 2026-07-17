"""
Sentence-embedding wrapper around BGE (BAAI general embeddings).
BGE models are asymmetric: queries need an instruction prefix, documents
don't. Getting this wrong silently degrades retrieval quality, so it's
centralized here rather than left to call sites.
"""
import os
import threading

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")

import numpy as np
from sentence_transformers import SentenceTransformer

from .config import get_settings

_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_lock = threading.Lock()
_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                settings = get_settings()
                _model = SentenceTransformer(settings.embedding_model)
    return _model


def encode_documents(texts: list[str]) -> np.ndarray:
    """Embed profile text (no instruction prefix — BGE convention)."""
    model = get_embedding_model()
    return np.asarray(
        model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    )


def encode_query(query: str) -> np.ndarray:
    """Embed a search query (instruction-prefixed — BGE convention)."""
    model = get_embedding_model()
    vec = model.encode(
        _QUERY_INSTRUCTION + query, normalize_embeddings=True, show_progress_bar=False
    )
    return np.asarray(vec)
