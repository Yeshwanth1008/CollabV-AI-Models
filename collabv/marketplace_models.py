"""
CollabV AI - Marketplace request/response models.

Pydantic Input models for the API layer (mirror the naming style of
collabv/api.py's CompanyRequestInput, ProfessorProfileInput, etc.) plus
dataclasses for engine internals and API responses.

No persistence logic here - pure schemas.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ─── Pydantic Input models ────────────────────────────────────────────────

class PatentListingInput(BaseModel):
    """Inventor-facing payload to create or update a patent listing.

    Status is NOT settable via this model - lifecycle transitions go through
    dedicated endpoints (POST /marketplace/listings/{id}/activate, etc.) so
    we can enforce the draft -> pending_approval -> active flow.
    """
    listing_id: Optional[str] = Field(None, max_length=64)
    professor_id: str = Field(..., max_length=64)
    patent_number: Optional[str] = Field(None, max_length=64)
    title: str = Field(..., max_length=500)
    abstract: Optional[str] = Field(None, max_length=10000)
    claims_text: Optional[str] = Field(None, max_length=200000)
    inventor_names: List[str] = Field(default_factory=list, max_length=20)
    granted_date: Optional[str] = Field(None, max_length=32)
    licensing_terms: Dict[str, Any] = Field(default_factory=dict)
    asking_price_inr: Optional[float] = Field(None, ge=0)
    domain_tags: List[str] = Field(default_factory=list, max_length=20)
    industry_tags: List[str] = Field(default_factory=list, max_length=20)


class BuyerProfileInput(BaseModel):
    """Buyer-facing payload. Minimum bar (per agreed default): industry +
    >=1 technical_area + 100-char use_cases."""
    buyer_id: Optional[str] = Field(None, max_length=64)
    org_name: str = Field(..., max_length=200)
    org_type: str = Field("enterprise", max_length=32)
    industry: str = Field(..., max_length=200)
    industries_of_interest: List[str] = Field(default_factory=list, max_length=20)
    technical_areas: List[str] = Field(..., min_length=1, max_length=30)
    use_cases: str = Field(..., min_length=100, max_length=5000)
    tech_maturity_preference: str = Field("mid_stage", max_length=32)
    budget_band: str = Field("medium", max_length=32)
    geographic_scope: List[str] = Field(default_factory=list, max_length=30)
    seller_preferences: Dict[str, Any] = Field(default_factory=dict)


class InquiryInput(BaseModel):
    """Buyer- or student-initiated buy-interest on a listing."""
    listing_id: str = Field(..., max_length=64)
    message: str = Field("", max_length=2000)


class ProposalInput(BaseModel):
    """Inventor-initiated outreach to a candidate buyer."""
    listing_id: str = Field(..., max_length=64)
    buyer_id: str = Field(..., max_length=64)
    message: Optional[str] = Field(None, max_length=5000)


class ProposalRespondInput(BaseModel):
    action: str = Field(..., max_length=32)        # accept | decline
    reply: Optional[str] = Field(None, max_length=2000)


class EventInput(BaseModel):
    """Client-recorded signal that feeds reranker retraining."""
    event_type: str = Field(..., max_length=32)
    subject_listing_id: Optional[str] = Field(None, max_length=64)
    subject_buyer_id: Optional[str] = Field(None, max_length=64)
    match_score_at_event: Optional[float] = None
    position_in_ranking: Optional[int] = Field(None, ge=0, le=10000)
    query_hash: Optional[str] = Field(None, max_length=64)
    payload: Dict[str, Any] = Field(default_factory=dict)


class CandidateBuyersInput(BaseModel):
    """Per-listing Mode A query parameters."""
    top_k: int = Field(10, ge=1, le=50)
    exclude_students: bool = True
    include_synthetic: bool = False   # synthetic = seed buyers (eval/demo only)
    include_explanations: bool = True
    explain_top_k: int = Field(5, ge=0, le=20)


class RecommendedPatentsInput(BaseModel):
    """Per-buyer Mode B query parameters."""
    top_k: int = Field(20, ge=1, le=100)
    include_explanations: bool = True
    explain_top_k: int = Field(5, ge=0, le=20)
    min_status: str = Field("active", max_length=32)   # only active listings


# ─── Engine dataclasses (output shapes) ──────────────────────────────────

@dataclass
class CandidateBuyer:
    """One ranked candidate buyer for a patent listing (Mode A result row)."""
    buyer_id: str
    org_name: str
    org_type: str
    industry: str
    score: float                                  # final composite (0-100)
    retrieval_score: float                        # cosine similarity (0-100 rescaled)
    domain_overlap_score: float
    industry_match_score: float
    maturity_match_score: float
    is_synthetic: bool = False
    reasons: List[str] = field(default_factory=list)
    explanation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class CandidatePatent:
    """One ranked patent listing for a buyer (Mode B result row)."""
    listing_id: str
    title: str
    professor_id: str
    professor_name: str
    department: str
    status: str
    score: float
    retrieval_score: float
    domain_overlap_score: float
    recency_score: float
    industry_match_score: float
    licensing_terms: Dict[str, Any] = field(default_factory=dict)
    asking_price_inr: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    explanation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MarketplaceMatchResult:
    """Wrapper returned from MarketplaceEngine for either mode."""
    query_hash: str             # used to group events from this ranking list
    mode: str                   # "buyers_for_patent" | "patents_for_buyer"
    subject_id: str             # listing_id or buyer_id
    total_candidates_considered: int
    total_filtered: int
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    cold_start: bool = True     # True until LTR model takes over
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


__all__ = [
    # Pydantic
    "PatentListingInput", "BuyerProfileInput",
    "InquiryInput", "ProposalInput", "ProposalRespondInput", "EventInput",
    "CandidateBuyersInput", "RecommendedPatentsInput",
    # Dataclasses
    "CandidateBuyer", "CandidatePatent", "MarketplaceMatchResult",
]
