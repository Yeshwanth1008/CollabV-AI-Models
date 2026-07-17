"""
CollabV AI - Dense Embeddings + Vector Index
=============================================
Replaces (or supplements) the TF-IDF Tier 2 of the matching engine with dense
embeddings produced by a sentence-transformers model and stored in a FAISS
flat IP index.

If sentence-transformers / faiss are unavailable, the engine sets `is_ready`
to False and falls back to TF-IDF cleanly. Nothing breaks.

Public API:
    EmbeddingEngine(model_name).build_professor_index(professors)
    EmbeddingEngine.encode(text) -> np.ndarray
    EmbeddingEngine.search(query_emb, top_k) -> List[(prof_id, score)]
    EmbeddingEngine.save_index(path) / load_index(path)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ─── Optional heavy dependencies ────────────────────────────────────────────
# We import lazily so the module loads even without sentence-transformers/faiss.
try:
    import numpy as np  # numpy is already a dependency
except ImportError:  # pragma: no cover
    np = None  # type: ignore

_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_VECTOR_DIM = 384  # MiniLM-L6 outputs 384-dim vectors


class EmbeddingEngine:
    """Encodes texts to vectors and serves nearest-neighbour search over them.

    The engine is designed to degrade gracefully:
      - If sentence-transformers or faiss isn't installed, `is_ready` stays False
        and callers can fall back to TF-IDF without exceptions.
      - If only sentence-transformers is installed but faiss isn't, search uses
        a numpy fallback that's slower but correct for our scale (<10K profs).
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        cache_dir: Optional[str] = None,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.model = None
        self.index = None
        self.use_faiss = False
        self.prof_ids: List[str] = []
        self._matrix = None  # numpy fallback storage
        # load_error is the *reason* is_ready is False, surfaced to callers
        # (health endpoint, smoke tests, structured logs). Stays None on success.
        # Distinguishes "no model loaded, dense retrieval is dark" from
        # "model loaded, index just hasn't been built yet" — those are very
        # different failure modes for an operator to see.
        self.load_error: Optional[str] = None

        self._try_load_model()

    @property
    def is_ready(self) -> bool:
        """True if the model loaded successfully."""
        return self.model is not None

    @property
    def has_index(self) -> bool:
        """True if a professor index has been built or loaded."""
        return (self.index is not None) or (self._matrix is not None)

    # ─── Model loading ──────────────────────────────────────────────────────

    def _try_load_model(self) -> None:
        # Both failure paths set self.load_error and log LOUDLY (error level).
        # Reasoning: a missing sentence-transformers install causes Mode A and
        # Mode B to silently return zero candidates — visually indistinguishable
        # from "no buyers/listings exist yet." That ambiguity has bitten this
        # project before; the error log is the breadcrumb operators search for.
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:
            msg = (
                f"sentence-transformers not importable ({e}). Dense retrieval is "
                f"DISABLED — Mode A and Mode B will return engine_unavailable. "
                f"Install with `pip install sentence-transformers` or ensure "
                f"PYTHONPATH includes the wheel cache (see BUILD_SUMMARY)."
            )
            self.load_error = msg
            logger.error("[EMBEDDINGS DEGRADED] %s", msg)
            return
        try:
            self.model = SentenceTransformer(self.model_name, cache_folder=self.cache_dir)
            logger.info("Loaded embedding model: %s", self.model_name)
        except Exception as e:
            msg = (
                f"sentence-transformers IS installed but loading model "
                f"{self.model_name!r} failed ({type(e).__name__}: {e}). Dense "
                f"retrieval is DISABLED. Common causes: corrupt HF cache, "
                f"offline mode without a pre-warmed cache, or a torch ABI "
                f"mismatch."
            )
            self.load_error = msg
            logger.error("[EMBEDDINGS DEGRADED] %s", msg)
            self.model = None
            return

        try:
            import faiss  # type: ignore  # noqa: F401
            self.use_faiss = True
        except ImportError:
            logger.info("faiss not installed; using numpy fallback for vector search")
            self.use_faiss = False

    # ─── Encoding ───────────────────────────────────────────────────────────

    def encode(self, text: str) -> Any:
        if not self.is_ready or np is None:
            raise RuntimeError("EmbeddingEngine not ready")
        vec = self.model.encode([text or ""], normalize_embeddings=True)
        return np.asarray(vec, dtype="float32")

    def encode_batch(self, texts: Sequence[str], batch_size: int = 32, show_progress: bool = False) -> Any:
        if not self.is_ready or np is None:
            raise RuntimeError("EmbeddingEngine not ready")
        vecs = self.model.encode(
            list(texts),
            batch_size=batch_size,
            show_progress_bar=show_progress,
            normalize_embeddings=True,
        )
        return np.asarray(vecs, dtype="float32")

    # ─── Index build / search ───────────────────────────────────────────────

    def build_professor_index(self, professors: List[Dict[str, Any]], show_progress: bool = False) -> None:
        if not self.is_ready:
            logger.warning("Cannot build index - embedding model not loaded")
            return

        texts = [self._professor_text(p) for p in professors]
        self.prof_ids = [str(p.get("professor_id") or f"PROF-{i}") for i, p in enumerate(professors)]
        embeddings = self.encode_batch(texts, show_progress=show_progress)

        if self.use_faiss:
            import faiss  # type: ignore
            self.index = faiss.IndexFlatIP(embeddings.shape[1])
            self.index.add(embeddings)
            logger.info("Built FAISS index with %d professors", len(self.prof_ids))
        else:
            self._matrix = embeddings
            logger.info("Built numpy embedding matrix with %d professors", len(self.prof_ids))

    def search(self, query_embedding: Any, top_k: int = 20) -> List[Tuple[str, float]]:
        if not self.has_index or np is None:
            return []
        # Make query 2D
        if query_embedding.ndim == 1:
            query_embedding = query_embedding.reshape(1, -1)

        if self.use_faiss and self.index is not None:
            import faiss  # type: ignore  # noqa: F401
            scores, idxs = self.index.search(query_embedding, top_k)
            results = []
            for idx, score in zip(idxs[0], scores[0]):
                if 0 <= idx < len(self.prof_ids):
                    results.append((self.prof_ids[idx], float(score)))
            return results

        # Numpy fallback - dot product since vectors are normalized
        sims = (self._matrix @ query_embedding.T).flatten()
        top_idx = sims.argsort()[::-1][:top_k]
        return [(self.prof_ids[i], float(sims[i])) for i in top_idx]

    def score_all(self, query_embedding: Any) -> Any:
        """Return similarity scores for every professor in the index (aligned to prof_ids)."""
        if not self.has_index or np is None:
            return np.zeros(len(self.prof_ids), dtype="float32") if np else []
        if query_embedding.ndim == 1:
            query_embedding = query_embedding.reshape(1, -1)
        if self.use_faiss and self.index is not None:
            # FAISS doesn't directly expose all-scores; do a top_k = N search
            n = len(self.prof_ids)
            scores, idxs = self.index.search(query_embedding, n)
            arr = np.zeros(n, dtype="float32")
            for idx, sc in zip(idxs[0], scores[0]):
                if 0 <= idx < n:
                    arr[idx] = float(sc)
            return arr
        return (self._matrix @ query_embedding.T).flatten()

    # ─── Persistence ────────────────────────────────────────────────────────

    def save_index(self, path: str) -> None:
        if not self.has_index:
            raise RuntimeError("No index to save")
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "model_name": self.model_name,
            "use_faiss": self.use_faiss,
            "prof_ids": self.prof_ids,
        }
        with open(path_obj.with_suffix(".meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        if self.use_faiss and self.index is not None:
            import faiss  # type: ignore
            faiss.write_index(self.index, str(path_obj))
        else:
            np.save(str(path_obj) + ".npy", self._matrix)

    def load_index(self, path: str) -> bool:
        path_obj = Path(path)
        meta_path = path_obj.with_suffix(".meta.json")
        if not meta_path.exists():
            return False
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        self.prof_ids = meta["prof_ids"]

        if self.use_faiss and path_obj.exists():
            import faiss  # type: ignore
            self.index = faiss.read_index(str(path_obj))
            return True
        np_path = Path(str(path_obj) + ".npy")
        if np_path.exists() and np is not None:
            self._matrix = np.load(np_path)
            return True
        return False

    # ─── Text preparation ───────────────────────────────────────────────────

    @staticmethod
    def _professor_text(prof: Dict[str, Any]) -> str:
        parts: List[str] = []

        bio = prof.get("biography") or ""
        if bio:
            parts.append(bio)

        research = prof.get("research_areas") or []
        if isinstance(research, list):
            parts.append("Research areas: " + ", ".join(str(r) for r in research))

        expertise = prof.get("technical_expertise") or []
        if isinstance(expertise, list):
            parts.append("Expertise: " + ", ".join(str(e) for e in expertise))

        # Top 5 publication titles
        pubs = prof.get("publications") or []
        if isinstance(pubs, list):
            for pub in pubs[:5]:
                parts.append(str(pub))

        # Patent titles
        patents = prof.get("patents") or []
        if isinstance(patents, list):
            for p in patents[:5]:
                if isinstance(p, dict):
                    parts.append(str(p.get("title", "")))
                else:
                    parts.append(str(p))

        dept = prof.get("department") or ""
        if dept:
            parts.append(dept)

        nlp_tags = prof.get("nlp_tags") or []
        if isinstance(nlp_tags, list):
            parts.append("Tags: " + ", ".join(str(t) for t in nlp_tags))

        return " ".join(parts)

    @staticmethod
    def request_text(request: Any) -> str:
        """Combine a CompanyRequest into a single string for encoding."""
        if request is None:
            return ""
        getter = lambda k: getattr(request, k, None) if not isinstance(request, dict) else request.get(k)
        parts = []
        for key in ("project_description", "challenges", "industry"):
            v = getter(key)
            if v:
                parts.append(str(v))
        for key in ("technical_area", "required_expertise", "tech_stack"):
            v = getter(key)
            if isinstance(v, list):
                parts.append(", ".join(str(x) for x in v))
            elif v:
                parts.append(str(v))
        return " ".join(parts)


__all__ = ["EmbeddingEngine"]
