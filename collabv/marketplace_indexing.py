"""
CollabV AI - Marketplace embedding indexing.

Two-index manager: one over public patent listings, one over buyer profiles.
Wraps two EmbeddingEngine instances (same MiniLM-L6-v2 model) and provides
text formatters for each entity type.

This module deliberately does NOT touch the professor embedding index used by
MatchingEngine - patents and buyers get their own indices because they have
different field shapes and update cadences.

Design notes:
  - Only ACTIVE listings get embedded. Drafts / pending stay out of the index
    so guest browse and Mode B never expose them.
  - Buyers including is_synthetic=True go in the index but are filtered out at
    response time by marketplace_rules (unless include_synthetic=True).
  - Incremental updates: encode one entity, slot into the matrix.
  - Full rebuild: triggered via /marketplace/embeddings/rebuild admin endpoint.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import marketplace_db as mdb
from .embeddings import EmbeddingEngine

logger = logging.getLogger(__name__)


# Index files live alongside the existing collabv_embeddings.index
PATENT_INDEX_FILE = Path(__file__).parent.parent / "marketplace_patents.index"
BUYER_INDEX_FILE  = Path(__file__).parent.parent / "marketplace_buyers.index"


# ─── Text formatters ─────────────────────────────────────────────────────

def patent_listing_text(listing: Dict[str, Any]) -> str:
    """Build the text blob that gets embedded for a patent listing.

    Same philosophy as EmbeddingEngine._professor_text - concatenate the
    fields that carry semantic signal, biased toward title + abstract since
    those are the highest-signal fields for retrieval.
    """
    parts: List[str] = []

    title = listing.get("title") or ""
    if title:
        # Title weighted 3x by repetition (mirrors what _build_profiles does
        # for research_areas in matching_engine.py)
        parts.append(title)
        parts.append(title)
        parts.append(title)

    abstract = listing.get("abstract") or ""
    if abstract:
        parts.append(abstract)

    claims = listing.get("claims_text") or ""
    if claims:
        # Claims can be very long; cap the contribution
        parts.append(claims[:4000])

    domain_tags = listing.get("domain_tags") or []
    if isinstance(domain_tags, list) and domain_tags:
        parts.append("Domains: " + ", ".join(str(t) for t in domain_tags))

    industry_tags = listing.get("industry_tags") or []
    if isinstance(industry_tags, list) and industry_tags:
        parts.append("Industries: " + ", ".join(str(t) for t in industry_tags))

    return " ".join(p for p in parts if p)


def buyer_profile_text(buyer: Dict[str, Any]) -> str:
    """Build the text blob that gets embedded for a buyer profile."""
    parts: List[str] = []

    org = buyer.get("org_name") or ""
    if org:
        parts.append(org)

    industry = buyer.get("industry") or ""
    if industry:
        parts.append(f"Industry: {industry}")

    interests = buyer.get("industries_of_interest") or []
    if isinstance(interests, list) and interests:
        parts.append("Industries of interest: " + ", ".join(str(i) for i in interests))

    tech_areas = buyer.get("technical_areas") or []
    if isinstance(tech_areas, list) and tech_areas:
        # Tech areas weighted 2x because they're the strongest matching signal
        text = ", ".join(str(t) for t in tech_areas)
        parts.append("Technical areas: " + text)
        parts.append(text)

    use_cases = buyer.get("use_cases") or ""
    if use_cases:
        parts.append(use_cases)

    maturity = buyer.get("tech_maturity_preference") or ""
    if maturity:
        parts.append(f"Maturity preference: {maturity}")

    return " ".join(p for p in parts if p)


# ─── Two-index manager ───────────────────────────────────────────────────

class MarketplaceIndex:
    """Holds the patent and buyer EmbeddingEngine instances.

    Same graceful-degradation pattern as MatchingEngine._init_embeddings:
    if sentence-transformers isn't installed, is_ready stays False and the
    engine falls back to keyword-only retrieval at the engine layer.
    """

    def __init__(self) -> None:
        self.patent_engine = EmbeddingEngine()
        self.buyer_engine = EmbeddingEngine()

    @property
    def is_ready(self) -> bool:
        return self.patent_engine.is_ready and self.buyer_engine.is_ready

    # ─── Build / load / save ─────────────────────────────────────────────

    def build_patent_index(self, db_path: Optional[str] = None) -> int:
        """Encode every ACTIVE listing and rebuild the FAISS index."""
        if not self.patent_engine.is_ready:
            logger.warning("patent_engine not ready; skipping build")
            return 0
        listings = mdb.list_active_listings(db_path=db_path)
        if not listings:
            logger.info("No active listings to index")
            self.patent_engine.prof_ids = []
            self.patent_engine._matrix = None
            self.patent_engine.index = None
            return 0
        # We re-purpose `prof_ids` to hold listing_ids; that field is just a
        # generic key list inside EmbeddingEngine.
        texts = [patent_listing_text(l) for l in listings]
        ids = [l["listing_id"] for l in listings]
        embeddings = self.patent_engine.encode_batch(texts)
        if self.patent_engine.use_faiss:
            import faiss  # type: ignore
            self.patent_engine.index = faiss.IndexFlatIP(embeddings.shape[1])
            self.patent_engine.index.add(embeddings)
        else:
            self.patent_engine._matrix = embeddings
        self.patent_engine.prof_ids = ids
        # Persist each embedding to the listing row too (handy for warm restarts
        # and for pgvector parity in production).
        for listing_id, vec in zip(ids, embeddings):
            mdb.update_listing_embedding(listing_id, vec.tolist(), db_path=db_path)
        return len(ids)

    def build_buyer_index(self, db_path: Optional[str] = None,
                          include_synthetic: bool = True) -> int:
        if not self.buyer_engine.is_ready:
            logger.warning("buyer_engine not ready; skipping build")
            return 0
        buyers = mdb.list_buyers(include_synthetic=include_synthetic, db_path=db_path)
        if not buyers:
            self.buyer_engine.prof_ids = []
            self.buyer_engine._matrix = None
            self.buyer_engine.index = None
            return 0
        texts = [buyer_profile_text(b) for b in buyers]
        ids = [b["buyer_id"] for b in buyers]
        embeddings = self.buyer_engine.encode_batch(texts)
        if self.buyer_engine.use_faiss:
            import faiss  # type: ignore
            self.buyer_engine.index = faiss.IndexFlatIP(embeddings.shape[1])
            self.buyer_engine.index.add(embeddings)
        else:
            self.buyer_engine._matrix = embeddings
        self.buyer_engine.prof_ids = ids
        for buyer_id, vec in zip(ids, embeddings):
            mdb.update_buyer_embedding(buyer_id, vec.tolist(), db_path=db_path)
        return len(ids)

    def save_indices(self) -> None:
        if self.patent_engine.has_index:
            self.patent_engine.save_index(str(PATENT_INDEX_FILE))
        if self.buyer_engine.has_index:
            self.buyer_engine.save_index(str(BUYER_INDEX_FILE))

    def load_indices(self) -> Tuple[bool, bool]:
        return (
            self.patent_engine.load_index(str(PATENT_INDEX_FILE)),
            self.buyer_engine.load_index(str(BUYER_INDEX_FILE)),
        )

    # ─── Incremental updates ─────────────────────────────────────────────

    def upsert_listing(self, listing: Dict[str, Any], db_path: Optional[str] = None) -> None:
        """Encode a single listing and slot it into the index. Skips if not active.

        Phase 1 implementation rebuilds the whole index whenever a listing
        is added or removed - that's fine for hundreds of listings. Phase 2
        switches to true incremental FAISS add() once we cross ~5k.
        """
        if listing.get("status") != mdb.LISTING_ACTIVE:
            # Drafts stay unembedded
            return
        # TODO Phase 2: true incremental add. For now, rebuild.
        self.build_patent_index(db_path=db_path)

    def upsert_buyer(self, buyer: Dict[str, Any], db_path: Optional[str] = None) -> None:
        # TODO Phase 2: true incremental add. For now, rebuild.
        self.build_buyer_index(db_path=db_path)

    # ─── Search ──────────────────────────────────────────────────────────

    def search_buyers(self, listing_text: str, top_k: int = 200) -> List[Tuple[str, float]]:
        """Retrieve top-K buyer candidates for a patent's text."""
        if not self.buyer_engine.is_ready or not self.buyer_engine.has_index:
            return []
        q = self.buyer_engine.encode(listing_text)
        return self.buyer_engine.search(q, top_k=top_k)

    def search_patents(self, buyer_text: str, top_k: int = 200) -> List[Tuple[str, float]]:
        """Retrieve top-K patent listing candidates for a buyer's text."""
        if not self.patent_engine.is_ready or not self.patent_engine.has_index:
            return []
        q = self.patent_engine.encode(buyer_text)
        return self.patent_engine.search(q, top_k=top_k)

    # ─── Diagnostics ─────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        # Surfaces is_ready AND any load_error for each engine. The error string
        # is the canonical signal CI/smoke tests should assert against — if it's
        # non-null, dense retrieval is dark and Mode A/B return engine_unavailable
        # instead of empty results.
        return {
            "patent_index": {
                "ready": self.patent_engine.is_ready,
                "indexed": len(self.patent_engine.prof_ids),
                "use_faiss": self.patent_engine.use_faiss,
                "load_error": self.patent_engine.load_error,
            },
            "buyer_index": {
                "ready": self.buyer_engine.is_ready,
                "indexed": len(self.buyer_engine.prof_ids),
                "use_faiss": self.buyer_engine.use_faiss,
                "load_error": self.buyer_engine.load_error,
            },
        }

    @property
    def embeddings_degraded(self) -> bool:
        """Single boolean for the health endpoint: True if EITHER engine
        failed to load. Either failure breaks Mode A or Mode B respectively.
        """
        return (not self.patent_engine.is_ready) or (not self.buyer_engine.is_ready)


__all__ = [
    "MarketplaceIndex",
    "patent_listing_text",
    "buyer_profile_text",
    "PATENT_INDEX_FILE",
    "BUYER_INDEX_FILE",
]
