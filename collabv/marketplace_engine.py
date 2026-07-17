"""
CollabV AI - MarketplaceEngine (retrieve-then-rerank orchestrator).

Pipeline per query:

    retrieve (top-K via embeddings)
        -> deterministic business rules (drop students etc.)
        -> rerank (cold-start blend in Phase 1; LTR in Phase 3)
        -> top-N + LLM explanations on top-5
        -> emit query_hash so downstream events can group back to this list

This module is the parallel to collabv/matching_engine.py.MatchingEngine but
for the marketplace, so the construction pattern mirrors it: try/except per
component with graceful fallback messages, lazy LLM imports, components
exposed on `self.X` for direct access by api.py.

Phase 1 status: stub. The body is structured so api.py can be wired without
the orchestration being complete - the public methods return well-typed
MarketplaceMatchResult stubs that downstream code can consume.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import marketplace_db as mdb
from .marketplace_indexing import (
    MarketplaceIndex,
    patent_listing_text,
    buyer_profile_text,
)
from .marketplace_models import (
    CandidateBuyer, CandidatePatent, MarketplaceMatchResult,
)
from .marketplace_reranker import MarketplaceReranker, FeatureScores
from .marketplace_rules import (
    MarketplaceRulesConfig,
    filter_candidate_buyers,
    filter_candidate_patents,
)

logger = logging.getLogger(__name__)


# ─── Engine ──────────────────────────────────────────────────────────────

class MarketplaceEngine:
    """Top-level orchestrator for patent <-> buyer matching.

    Constructor follows the MatchingEngine pattern: each subcomponent is
    initialised in a try/except block so a missing optional dep (e.g.
    sentence-transformers absent in dev) results in a degraded but functional
    engine.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        rules_config: Optional[MarketplaceRulesConfig] = None,
        load_indices_on_init: bool = True,
    ) -> None:
        self.db_path = db_path or mdb.DEFAULT_DB_PATH
        self.rules_config = rules_config or MarketplaceRulesConfig()

        # Make sure schema exists
        try:
            mdb.init_marketplace_tables(self.db_path)
        except Exception as e:
            logger.warning("init_marketplace_tables failed: %s", e)

        # Two-index manager (graceful fallback if embeddings unavailable)
        try:
            self.index = MarketplaceIndex()
            if load_indices_on_init:
                self.index.load_indices()
            print(f"[marketplace] Index manager ready: {self.index.stats()}")
        except Exception as e:
            print(f"[marketplace] Index manager disabled: {e}")
            self.index = None

        # Cold-start reranker
        try:
            self.reranker = MarketplaceReranker()
            print(f"[marketplace] Reranker: {self.reranker.state()}")
        except Exception as e:
            print(f"[marketplace] Reranker disabled: {e}")
            self.reranker = None

        # Lazy: explainer + LLM proposal generator (Phase 2)
        self._explainer = None
        self._proposal_drafter = None

    # ─── Public API: Mode A (candidate buyers for a patent) ─────────────

    def recommend_buyers_for_patent(
        self,
        listing_id: str,
        top_k: int = 10,
        exclude_students: bool = True,
        include_synthetic: bool = False,
        include_explanations: bool = True,
        explain_top_k: int = 5,
        user_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
        professor_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> MarketplaceMatchResult:
        """Mode A: rank candidate BUYERS for a given patent listing.

        Returns up to top_k CandidateBuyer rows wrapped in a
        MarketplaceMatchResult. Students are excluded by default per spec.
        """
        listing = mdb.get_listing(listing_id, db_path=self.db_path)
        if not listing:
            return MarketplaceMatchResult(
                query_hash="", mode="buyers_for_patent",
                subject_id=listing_id,
                total_candidates_considered=0,
                total_filtered=0,
                notes=["Listing not found"],
            )

        query_hash = self._make_query_hash("buyers_for_patent", listing_id)

        # ── Retrieve ────────────────────────────────────────────────────
        candidate_ids_scores = self._retrieve_buyers(listing, top_k_retrieval=200)
        total_considered = len(candidate_ids_scores)

        # Hydrate buyer rows
        all_buyers = mdb.list_buyers(include_synthetic=True, db_path=self.db_path)
        buyer_by_id = {b["buyer_id"]: b for b in all_buyers}
        candidates_hydrated: List[Dict[str, Any]] = []
        retrieval_by_id: Dict[str, float] = {}
        for bid, retrieval in candidate_ids_scores:
            b = buyer_by_id.get(bid)
            if not b:
                continue
            if not include_synthetic and b.get("is_synthetic"):
                continue
            candidates_hydrated.append(b)
            retrieval_by_id[bid] = retrieval

        # ── Rules ───────────────────────────────────────────────────────
        cfg = self.rules_config
        cfg.exclude_students_from_proposals = exclude_students
        cfg.exclude_synthetic_buyers_from_proposals = not include_synthetic
        candidates_filtered = filter_candidate_buyers(
            candidates_hydrated,
            listing,
            user_lookup or {},
            professor_lookup or {},
            cfg,
        )
        total_after_filter = len(candidates_filtered)

        # ── Rerank ──────────────────────────────────────────────────────
        ranked: List[CandidateBuyer] = []
        for b in candidates_filtered:
            ret = retrieval_by_id.get(b["buyer_id"], 0.0)
            if self.reranker is None:
                continue
            score, features, reasons = self.reranker.score_pair(listing, b, ret)
            ranked.append(CandidateBuyer(
                buyer_id=b["buyer_id"],
                org_name=b.get("org_name", ""),
                org_type=b.get("org_type") or "",
                industry=b.get("industry") or "",
                score=score,
                retrieval_score=round(features.retrieval * 100, 1),
                domain_overlap_score=round(features.domain_overlap * 100, 1),
                industry_match_score=round(features.industry_match * 100, 1),
                maturity_match_score=round(features.maturity_match * 100, 1),
                is_synthetic=bool(b.get("is_synthetic")),
                reasons=reasons,
            ))

        ranked.sort(key=lambda c: -c.score)
        ranked = ranked[:top_k]

        # ── Explain top-K ──────────────────────────────────────────────
        if include_explanations and ranked and explain_top_k > 0:
            self._attach_explanations_mode_a(listing, ranked[:explain_top_k])

        return MarketplaceMatchResult(
            query_hash=query_hash,
            mode="buyers_for_patent",
            subject_id=listing_id,
            total_candidates_considered=total_considered,
            total_filtered=total_after_filter,
            candidates=[c.to_dict() for c in ranked],
            cold_start=(self.reranker is not None and self.reranker.mode == "cold_start"),
        )

    # ─── Public API: Mode B (recommended patents for a buyer) ──────────

    def recommend_patents_for_buyer(
        self,
        buyer_id: str,
        top_k: int = 20,
        include_explanations: bool = True,
        explain_top_k: int = 5,
    ) -> MarketplaceMatchResult:
        """Mode B: rank candidate PATENT LISTINGS for a buyer.

        Symmetric to recommend_buyers_for_patent. Inherits the same reranker
        (including the vocabulary-aligned domain_overlap fix), the same rules
        layer (only ACTIVE listings are visible), and the same retrieve →
        rules → rerank → top-K pipeline.

        Quality caveat (carried from Phase 1): listings are title-only until
        the inventor-paste abstract flow ships, and the buyer side is still
        100% synthetic. This method verifies plumbing + symmetry, not match
        quality.
        """
        buyer = mdb.get_buyer(buyer_id, db_path=self.db_path)
        if not buyer:
            return MarketplaceMatchResult(
                query_hash="", mode="patents_for_buyer",
                subject_id=buyer_id,
                total_candidates_considered=0,
                total_filtered=0,
                notes=["Buyer not found"],
            )

        query_hash = self._make_query_hash("patents_for_buyer", buyer_id)

        # ── Retrieve ────────────────────────────────────────────────────
        candidate_ids_scores = self._retrieve_patents(buyer, top_k_retrieval=200)
        total_considered = len(candidate_ids_scores)

        # Hydrate listing rows. Only ACTIVE listings are eligible - rules layer
        # enforces this too, but limiting the hydration set is faster.
        active_listings = {l["listing_id"]: l
                           for l in mdb.list_active_listings(db_path=self.db_path)}
        candidates_hydrated: List[Dict[str, Any]] = []
        retrieval_by_id: Dict[str, float] = {}
        for lid, retrieval in candidate_ids_scores:
            l = active_listings.get(lid)
            if not l:
                continue
            candidates_hydrated.append(l)
            retrieval_by_id[lid] = retrieval

        # ── Rules ───────────────────────────────────────────────────────
        from .marketplace_rules import filter_candidate_patents
        candidates_filtered = filter_candidate_patents(
            candidates_hydrated, buyer, self.rules_config,
        )
        total_after_filter = len(candidates_filtered)

        # ── Rerank ──────────────────────────────────────────────────────
        ranked: List[CandidatePatent] = []
        if self.reranker is None:
            return MarketplaceMatchResult(
                query_hash=query_hash, mode="patents_for_buyer",
                subject_id=buyer_id,
                total_candidates_considered=total_considered,
                total_filtered=total_after_filter,
                notes=["Reranker not initialized"],
            )
        for l in candidates_filtered:
            ret = retrieval_by_id.get(l["listing_id"], 0.0)
            # Note: reranker.score_pair signature is (listing, buyer, retrieval).
            # The buyer is the SECOND argument; this is correct symmetry.
            score, features, reasons = self.reranker.score_pair(l, buyer, ret)
            # Extract professor metadata for display
            professor_id = l.get("professor_id", "")
            # We don't have a guaranteed professor_lookup here so fall back
            # to whatever's stored on the listing.
            ranked.append(CandidatePatent(
                listing_id=l["listing_id"],
                title=l.get("title", ""),
                professor_id=professor_id,
                professor_name="",          # Filled by api layer if needed
                department="",              # Filled by api layer if needed
                status=l.get("status", ""),
                score=score,
                retrieval_score=round(features.retrieval * 100, 1),
                domain_overlap_score=round(features.domain_overlap * 100, 1),
                recency_score=round(features.recency * 100, 1),
                industry_match_score=round(features.industry_match * 100, 1),
                licensing_terms=l.get("licensing_terms") or {},
                asking_price_inr=l.get("asking_price_inr"),
                reasons=reasons,
            ))

        ranked.sort(key=lambda c: -c.score)
        ranked = ranked[:top_k]

        # ── Explain top-K (Phase 2; stub for now) ──────────────────────
        if include_explanations and ranked and explain_top_k > 0:
            self._attach_explanations_mode_b(buyer, ranked[:explain_top_k])

        return MarketplaceMatchResult(
            query_hash=query_hash,
            mode="patents_for_buyer",
            subject_id=buyer_id,
            total_candidates_considered=total_considered,
            total_filtered=total_after_filter,
            candidates=[c.to_dict() for c in ranked],
            cold_start=(self.reranker is not None
                        and self.reranker.mode == "cold_start"),
        )

    def _retrieve_patents(self, buyer: Dict[str, Any],
                          top_k_retrieval: int = 200) -> List[tuple[str, float]]:
        """Symmetric to _retrieve_buyers. Falls back to 'all active listings'
        if no patent index is loaded."""
        if self.index is None or not self.index.is_ready:
            listings = mdb.list_active_listings(db_path=self.db_path)
            return [(l["listing_id"], 0.0) for l in listings]
        from .marketplace_indexing import buyer_profile_text
        text = buyer_profile_text(buyer)
        return self.index.search_patents(text, top_k=top_k_retrieval)

    def _attach_explanations_mode_b(
        self, buyer: Dict[str, Any], top_candidates: List[CandidatePatent],
    ) -> None:
        """Phase 2: call MarketplaceExplainer for buyer-side explanations."""
        # TODO Phase 2: instantiate MarketplaceExplainer + explain_batch_mode_b.
        return

    # ─── Internals ──────────────────────────────────────────────────────

    def _retrieve_buyers(self, listing: Dict[str, Any],
                         top_k_retrieval: int = 200) -> List[tuple[str, float]]:
        """Embedding-based retrieval. Falls back to "everyone" if no index."""
        if self.index is None or not self.index.is_ready:
            # Fallback: return all buyers with retrieval=0 so reranker still works
            buyers = mdb.list_buyers(db_path=self.db_path)
            return [(b["buyer_id"], 0.0) for b in buyers]
        text = patent_listing_text(listing)
        return self.index.search_buyers(text, top_k=top_k_retrieval)

    def _attach_explanations_mode_a(
        self, listing: Dict[str, Any], top_candidates: List[CandidateBuyer],
    ) -> None:
        """Phase 2: call MarketplaceExplainer to attach .explanation to each."""
        # TODO Phase 2: instantiate MarketplaceExplainer with self.db_path and
        # call explain_batch_mode_a(listing, top_candidates).
        return

    @staticmethod
    def _make_query_hash(mode: str, subject_id: str) -> str:
        """Stable hash that groups all events from one ranking list."""
        h = hashlib.sha1()
        h.update(f"{mode}:{subject_id}:{int(time.time() // 60)}".encode())
        return h.hexdigest()[:16]

    def stats(self) -> Dict[str, Any]:
        return {
            "index": self.index.stats() if self.index else None,
            "reranker": self.reranker.state() if self.reranker else None,
        }


__all__ = ["MarketplaceEngine"]
