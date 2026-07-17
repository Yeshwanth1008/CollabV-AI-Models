"""
CollabV AI - Matching Engine 7: Buyer (Student/Employee) -> Patents & Professors
=================================================================================
Given one registered Student or Employee profile, rank professor-owned,
actively licensable/sellable patent listings the buyer is a good fit for,
plus a grouped-by-professor view of the same results ("Recommended
Professors").

Powers both the Student Dashboard and the Employee Dashboard's AI sections
via a `buyer_type` parameter:
  - Recommended Patents:    match_patents_for_buyer(buyer_type, ...)
  - Recommended Professors: match_professors_for_buyer(buyer_type, ...)

Formerly two structurally identical modules (Engine 7 for Student, Engine 8
for Employee) that differed only in which `_REQUEST_ADAPTERS` key they
passed to Engine 5's shared core - merged into one engine rather than
maintained as parallel copies.

Reuses the unified Matching Engine 5's shared core rather than duplicating
scoring logic - same cross-engine reuse pattern as Matching Engine 4
(Company Dashboard), which imports its shared helpers from Engine 5:

    _score_pool           - the hybrid keyword+semantic scoring loop
    _REQUEST_ADAPTERS      - includes "student" and "employee" adapters
    _listing_as_patent     - reshapes a patent_listings row for scoring
    _patent_text            - builds embeddable text from a patent-shaped dict
    patent_id / _build_reasons - stable id + human-readable reasons
    _patent_readiness / _commercialization_score - maturity heuristics

Candidate pool is `patent_listings` (curated, active, PRICED listings) -
not the raw uncurated patent pool - since only listings carry
asking_price_inr/licensing_terms, matching both dashboards' "buy or
license" framing exactly.

Recommended Professors is derived from the SAME scoring call as Recommended
Patents (no second embedding pass): every active listing is scored against
the buyer profile WITHOUT truncation, THEN grouped by professor_id, THEN
truncated - truncating before grouping would undercount or drop professors
whose several decent-but-not-top-K listings each fall outside a small
top_k cutoff. A professor's overall score is the MAX of their listings'
scores (one great matching patent is enough to recommend them), with
average score and available-patent count exposed as secondary context.

Ranking is ephemeral (not persisted) - reuses the unified /match/interactions
log for view/save/contact/buy/license-request events
(source_kind="student"|"employee").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from .matching_engine_5 import (
    _score_pool, _REQUEST_ADAPTERS, _listing_as_patent, _patent_text,
    patent_id, _build_reasons, _patent_readiness, _commercialization_score,
)

BuyerType = Literal["student", "employee"]

_ScoredTuple = Tuple[str, str, str, float, float, float, str, List[str], List[str], List[str]]


def _business_potential_label(score: float) -> str:
    if score >= 70:
        return "High - strong technical fit in a commercialization-ready domain"
    if score >= 45:
        return "Medium - viable with additional development or partnership"
    return "Exploratory - early-stage fit, worth monitoring"


def _skill_alignment(buyer_profile: Dict[str, Any], matching_keywords: List[str]) -> List[str]:
    buyer_skills = {str(s).lower() for s in (buyer_profile.get("skills") or [])}
    return [kw for kw in matching_keywords if kw.lower() in buyer_skills]


@dataclass
class BuyerPatentMatch:
    listing_id: str
    patent_title: str
    professor_id: str
    professor_name: str
    department: str
    technology_domain: str
    match_score: float
    semantic_score: float
    keyword_score: float
    confidence: str
    asking_price_inr: Optional[float]
    licensing_terms: Dict[str, Any]
    status: str
    commercialization_stage: str
    industry_applications: List[str]
    reasons: List[str]
    skill_alignment: List[str]
    business_potential: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "listing_id": self.listing_id,
            "patent_title": self.patent_title,
            "professor_id": self.professor_id,
            "professor_name": self.professor_name,
            "department": self.department,
            "technology_domain": self.technology_domain,
            "match_score": self.match_score,
            "semantic_score": self.semantic_score,
            "keyword_score": self.keyword_score,
            "confidence": self.confidence,
            "asking_price_inr": self.asking_price_inr,
            "licensing_terms": self.licensing_terms,
            "status": self.status,
            "commercialization_stage": self.commercialization_stage,
            "industry_applications": self.industry_applications,
            "reasons": self.reasons,
            "skill_alignment": self.skill_alignment,
            "business_potential": self.business_potential,
        }


@dataclass
class BuyerProfessorMatch:
    professor_id: str
    professor_name: str
    department: str
    research_areas: List[str]
    available_patent_count: int
    max_score: float
    average_score: float
    featured_patents: List[BuyerPatentMatch] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "professor_id": self.professor_id,
            "professor_name": self.professor_name,
            "department": self.department,
            "research_areas": self.research_areas,
            "available_patent_count": self.available_patent_count,
            "max_score": self.max_score,
            "average_score": self.average_score,
            "featured_patents": [p.to_dict() for p in self.featured_patents],
        }


def _score_listings_for_buyer(
    buyer_type: BuyerType, buyer_profile: Dict[str, Any], listings: List[Dict[str, Any]],
) -> Tuple[List[_ScoredTuple], Dict[str, Dict[str, Any]]]:
    """Score every listing against the buyer profile, UNTRUNCATED (top_k=None) -
    callers that need a flat top-K list truncate afterward; the grouped
    professor view truncates only after grouping (see module docstring)."""
    request = _REQUEST_ADAPTERS[buyer_type](buyer_profile)

    ids: List[str] = []
    names: List[str] = []
    tags: List[str] = []
    patent_shapes: List[Dict[str, Any]] = []
    texts: List[str] = []
    by_id: Dict[str, Dict[str, Any]] = {}

    for listing in listings:
        lid = listing.get("listing_id", "")
        if not lid:
            continue
        ids.append(lid)
        names.append(listing.get("title", ""))
        tags.append(listing.get("status", ""))
        shaped = _listing_as_patent(listing)
        patent_shapes.append(shaped)
        texts.append(_patent_text(shaped))
        by_id[lid] = listing

    scored = _score_pool(
        query_patent=None, query_request=request,
        candidate_ids=ids, candidate_names=names, candidate_tags=tags,
        candidate_patent_shapes=patent_shapes, candidate_requests=None, candidate_texts=texts,
        namespace="listings", semantic_weight=0.65, keyword_weight=0.35, top_k=None,
    )
    return scored, by_id


def _build_patent_match(
    scored_tuple: _ScoredTuple,
    listing: Dict[str, Any],
    professor_lookup: Dict[str, Dict[str, Any]],
    buyer_profile: Dict[str, Any],
) -> BuyerPatentMatch:
    cid, name, tag, score, sem, kw, conf, reasons, domains, keywords = scored_tuple
    professor_id_ = str(listing.get("professor_id", ""))
    prof = professor_lookup.get(professor_id_, {})

    patent_like = {"status": listing.get("status", ""), "filing_date": listing.get("granted_date")}
    commercialization_stage = _patent_readiness(patent_like)

    return BuyerPatentMatch(
        listing_id=cid,
        patent_title=name,
        professor_id=professor_id_,
        professor_name=prof.get("name", ""),
        department=prof.get("department", ""),
        technology_domain=(listing.get("domain_tags") or [None])[0] or "General R&D",
        match_score=score,
        semantic_score=sem,
        keyword_score=kw,
        confidence=conf,
        asking_price_inr=listing.get("asking_price_inr"),
        licensing_terms=listing.get("licensing_terms") or {},
        status=listing.get("status", ""),
        commercialization_stage=commercialization_stage,
        industry_applications=listing.get("industry_tags") or [],
        reasons=reasons,
        skill_alignment=_skill_alignment(buyer_profile, keywords),
        business_potential=_business_potential_label(score),
    )


def match_patents_for_buyer(
    buyer_type: BuyerType,
    buyer_profile: Dict[str, Any],
    listings: List[Dict[str, Any]],
    professor_lookup: Dict[str, Dict[str, Any]],
    top_k: int = 20,
) -> List[BuyerPatentMatch]:
    scored, by_id = _score_listings_for_buyer(buyer_type, buyer_profile, listings)
    truncated = scored[:top_k] if top_k else scored
    return [
        _build_patent_match(t, by_id[t[0]], professor_lookup, buyer_profile)
        for t in truncated
    ]


def match_professors_for_buyer(
    buyer_type: BuyerType,
    buyer_profile: Dict[str, Any],
    listings: List[Dict[str, Any]],
    professor_lookup: Dict[str, Dict[str, Any]],
    top_k: int = 10,
    patents_per_professor: int = 3,
) -> List[BuyerProfessorMatch]:
    scored, by_id = _score_listings_for_buyer(buyer_type, buyer_profile, listings)

    groups: Dict[str, List[_ScoredTuple]] = {}
    for t in scored:
        listing = by_id[t[0]]
        pid = str(listing.get("professor_id", ""))
        if not pid:
            continue
        groups.setdefault(pid, []).append(t)

    professor_matches: List[BuyerProfessorMatch] = []
    for pid, tuples in groups.items():
        tuples.sort(key=lambda t: -t[3])
        prof = professor_lookup.get(pid, {})
        max_score = tuples[0][3]
        average_score = round(sum(t[3] for t in tuples) / len(tuples), 1)
        featured = [
            _build_patent_match(t, by_id[t[0]], professor_lookup, buyer_profile)
            for t in tuples[:patents_per_professor]
        ]
        professor_matches.append(BuyerProfessorMatch(
            professor_id=pid,
            professor_name=prof.get("name", ""),
            department=prof.get("department", ""),
            research_areas=(prof.get("research_areas") or [])[:8],
            available_patent_count=len(tuples),
            max_score=round(max_score, 1),
            average_score=average_score,
            featured_patents=featured,
        ))

    professor_matches.sort(key=lambda m: -m.max_score)
    return professor_matches[:top_k] if top_k else professor_matches


__all__ = [
    "BuyerType", "BuyerPatentMatch", "BuyerProfessorMatch",
    "match_patents_for_buyer", "match_professors_for_buyer",
]
