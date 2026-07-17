"""
CollabV AI - Unified Matching Engine (merges former Engines 3, 5, 6)
========================================================================
One engine, one API surface, for every "rank patents/candidates against a
query" direction the platform needs:

  A. Patent -> Audience        match_patent_to_audience() / match_patent_to_all_audiences()
     Given one patent, rank candidates of any of 5 registered audience
     types (company, professor, student, employee, institute). Powers the
     Professor Dashboard's "market this patent" flow.

  B. Buyer -> Patent Listings  match_buyer_to_listings()
     Given one buyer of any of the 5 audience types, rank active/priced
     marketplace listings.

  C. Buyer -> Raw Patent Pool  discover_patents_for_buyer()
     Given one professor-or-institute buyer profile, rank every patent on
     the platform (not just curated listings) - across every professor's
     affiliated institute, not just the buyer's own. Reverse of direction A.
     group_patents_by_professor() re-groups an untruncated result by
     professor (each carrying their affiliated institute) for buyers that
     want "recommended professors + their patents" instead of a flat list -
     powers the Professor Dashboard's Institute section.

  D. Technology Request -> Patent Listings  match_technology_request_to_listings()
     Given a posted "I need X" technology request, rank active listings.

All four directions share one hybrid scoring core: a semantic-similarity
signal (EmbeddingEngine, sentence-transformers MiniLM cosine similarity,
weight configurable per direction - 65/35 for A/B/D, 70/30 for C, spec'd
per engine) plus a keyword/domain-overlap signal (PatentScorer.score_relevance,
also the source of the explainable "reasons"). Falls back to keyword-only
scoring automatically if the embedding model is unavailable.

Formerly three separate modules (matching_engine_3.py, matching_engine_5.py,
matching_engine_6.py) that had already started depending on each other -
Engine 6 imported Engine 5's adapters/embedder, and Engine 3 had already
been reduced to shared helpers absorbed into Engine 5's "company" category.
This module completes that consolidation: one candidate-adapter registry,
one embedding cache, one confidence heuristic, one canonical domain
classifier (imported from patent_scorer.py instead of a second drifted
copy), and one MatchResult shape.

Ranking is ephemeral (not persisted) in every direction - a live "who/what
should I look at" search. Interactions (view/save/offer/bookmark/licensing-
request/...) are logged separately, see patent_marketplace_db.log_match_interaction.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .patent_scorer import PatentScorer, _classify_domain
from .embeddings import EmbeddingEngine
from . import patent_problem_db as ppdb

TARGET_TYPES = ("company", "student", "employee", "professor", "institute")
BUYER_TYPES = ("professor", "institute")  # direction C (raw patent pool) only supports these two
_INSTITUTION_NAME = "IIT Madras"

_NEXT_ACTION = {
    "company": "License Patent",
    "employee": "Collaborate",
    "student": "Connect",
    "professor": "Co-Research",
    "institute": "Research Partnership",
}

_CATEGORY_LABELS = {
    "company": "Best Matching Companies",
    "employee": "Best Matching Employees",
    "student": "Best Matching Students",
    "professor": "Best Matching Professors",
    "institute": "Best Matching Academic Institutes",
}


# ─── Shared helpers (formerly Matching Engine 3) ───────────────────────────

@dataclass
class ProblemStatement:
    id: str
    sector: str
    title: str
    description: str
    problem_statement: str
    expected_outcomes: List[str] = field(default_factory=list)

    def as_request(self) -> Dict[str, Any]:
        """Shape this problem statement as a company_request-like dict so it
        can be scored by PatentScorer.score_relevance()."""
        return {
            "project_description": self.description,
            "challenges": self.problem_statement,
            "industry": self.sector,
            "technical_area": [self.title],
            "required_expertise": self.expected_outcomes,
            "tech_stack": [],
        }


def load_problem_statements(db_path: Optional[str] = None) -> List[ProblemStatement]:
    ppdb.init_patent_problem_tables(db_path)
    rows = ppdb.get_problem_statements(db_path)
    return [ProblemStatement(**r) for r in rows]


def patent_id(patent: Dict[str, Any], professor_id: str) -> str:
    """Stable id for a scraped patent record, which has no id of its own.
    Prefers patent_number (IITM IDF code); falls back to a hash of the title
    when the number is missing (synthetic/Google Patents records)."""
    num = str(patent.get("patent_number") or "").strip()
    if num:
        return f"{professor_id}:{num}"
    h = hashlib.sha1(f"{professor_id}:{patent.get('title', '')}".encode()).hexdigest()[:12]
    return f"{professor_id}:{h}"


def _build_reasons(matching_domains: List[str], matching_keywords: List[str]) -> List[str]:
    reasons: List[str] = []
    if matching_domains:
        reasons.append(f"Shared technology domain: {', '.join(matching_domains[:3])}")
    if matching_keywords:
        reasons.append(f"Keyword overlap: {', '.join(matching_keywords[:5])}")
    if not reasons:
        reasons.append("Weak textual overlap only")
    return reasons


# ─── Request-shape adapters (any entity -> project_description/challenges/ ─
# ─── industry/technical_area/required_expertise/tech_stack) ───────────────
# One adapter per entity kind, used symmetrically whether the entity is the
# QUERY (direction B/C's buyer) or a CANDIDATE (direction A's audience
# member). Professor/institute adapters are the merged superset of the
# former Engine 5 (candidate-side) and Engine 6 (query-side) field lists.

def _as_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.strip():
        return [v]
    return []


def _is_problem_statement(candidate: Dict[str, Any]) -> bool:
    """Problem statements (the 50-item research compendium) are folded into
    the 'company' candidate pool - they represent company needs, not a
    separate audience type. Distinguished from a real buyer_profiles row by
    field shape (no buyer_id, has problem_statement/sector)."""
    return "buyer_id" not in candidate and "problem_statement" in candidate and "sector" in candidate


def _company_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    if _is_problem_statement(candidate):
        return {
            "project_description": candidate.get("description", ""),
            "challenges": candidate.get("problem_statement", ""),
            "industry": candidate.get("sector", ""),
            "technical_area": [candidate.get("title", "")],
            "required_expertise": candidate.get("expected_outcomes") or [],
            "tech_stack": [],
        }
    return {
        "project_description": candidate.get("use_cases", ""),
        "challenges": "",
        "industry": candidate.get("industry", ""),
        "technical_area": _as_list(candidate.get("technical_areas")),
        "required_expertise": _as_list(candidate.get("industries_of_interest")),
        "tech_stack": [],
    }


def _professor_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project_description": candidate.get("biography", ""),
        "challenges": candidate.get("expertise_summary", ""),
        "industry": "",
        "technical_area": _as_list(candidate.get("research_areas")) + _as_list(candidate.get("research_projects")),
        "required_expertise": _as_list(candidate.get("technical_expertise")) + _as_list(candidate.get("innovation_objectives")),
        "tech_stack": _as_list(candidate.get("nlp_tags")) + _as_list(candidate.get("emerging_technologies")),
    }


def _student_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project_description": candidate.get("bio", ""),
        "challenges": "",
        "industry": candidate.get("field_of_study", ""),
        "technical_area": _as_list(candidate.get("research_areas")),
        "required_expertise": _as_list(candidate.get("skills")) + _as_list(candidate.get("interests")),
        "tech_stack": [],
    }


def _employee_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project_description": candidate.get("bio", ""),
        "challenges": candidate.get("job_title", ""),
        "industry": candidate.get("industry", ""),
        "technical_area": _as_list(candidate.get("skills")) + _as_list(candidate.get("industry_expertise")),
        "required_expertise": _as_list(candidate.get("interests")) + _as_list(candidate.get("innovation_interests")),
        "tech_stack": _as_list(candidate.get("preferred_domains")),
    }


def _institute_adapter(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project_description": candidate.get("bio", ""),
        "challenges": ", ".join(_as_list(candidate.get("collaboration_types"))),
        "industry": ", ".join(_as_list(candidate.get("industrial_sectors"))),
        "technical_area": _as_list(candidate.get("focus_areas")),
        "required_expertise": _as_list(candidate.get("departments")),
        "tech_stack": _as_list(candidate.get("emerging_technologies")),
    }


_REQUEST_ADAPTERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "company": _company_adapter,
    "professor": _professor_adapter,
    "student": _student_adapter,
    "employee": _employee_adapter,
    "institute": _institute_adapter,
}


def _identity(kind: str, candidate: Dict[str, Any]) -> Tuple[str, str, str]:
    """Return (id, display_name, tag) for a candidate, by entity kind."""
    if kind == "company":
        if _is_problem_statement(candidate):
            return (candidate.get("id", ""), candidate.get("title", ""), candidate.get("sector", ""))
        return (candidate.get("buyer_id", ""), candidate.get("org_name", ""), candidate.get("industry", ""))
    if kind == "professor":
        return (candidate.get("professor_id", ""), candidate.get("name", ""), candidate.get("department", ""))
    if kind == "student":
        return (candidate.get("user_id", ""), candidate.get("name", ""), candidate.get("institute", ""))
    if kind == "employee":
        return (candidate.get("user_id", ""), candidate.get("name", ""), candidate.get("company_name", ""))
    if kind == "institute":
        return (candidate.get("user_id", ""), candidate.get("institute_name", ""), "")
    raise ValueError(f"Unknown entity kind: {kind}")


def _buyer_identity(buyer_type: str, profile: Dict[str, Any]) -> str:
    if buyer_type == "professor":
        return str(profile.get("professor_id", ""))
    return str(profile.get("user_id", ""))


# ─── Patent-shape text builders ─────────────────────────────────────────────

def _patent_text(patent: Dict[str, Any], extra_context: str = "") -> str:
    return " ".join(filter(None, [
        str(patent.get("title", "")),
        str(patent.get("abstract", "")),
        str(patent.get("claims_text", "") or patent.get("description", "")),
        extra_context,
    ]))


def _listing_as_patent(listing: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": listing.get("title", ""),
        "abstract": listing.get("abstract", ""),
        "description": listing.get("claims_text", ""),
    }


def _patent_readiness(patent: Dict[str, Any]) -> str:
    status = str(patent.get("status", "")).lower()
    if "grant" in status:
        return "High - Granted, ready for licensing"
    if "publish" in status:
        return "Medium - Published, nearing grant"
    return "Early Stage - Filed"


def _extract_year(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value)
    for token in text.replace("-", " ").replace("/", " ").split():
        if token.isdigit() and len(token) == 4 and 1900 <= int(token) <= 2100:
            return int(token)
    return None


def _commercialization_score(patent: Dict[str, Any]) -> float:
    status = str(patent.get("status", "")).lower()
    status_factor = 1.0 if "grant" in status else 0.7 if "publish" in status else 0.5

    year = _extract_year(patent.get("filing_date") or patent.get("year"))
    if year:
        age = datetime.now().year - year
        if age <= 3:
            recency_factor = 1.0
        elif age <= 5:
            recency_factor = 0.75
        elif age <= 10:
            recency_factor = 0.5
        else:
            recency_factor = 0.3
    else:
        recency_factor = 0.6

    return round(min(100.0, (0.6 * status_factor + 0.4 * recency_factor) * 100), 1)


def _suggested_action_and_mode(buyer_type: str, relevance: float, commercialization: float) -> Tuple[str, str]:
    if buyer_type == "institute":
        if commercialization >= 75 and relevance >= 70:
            return "Acquire Patent", "Exclusive License"
        if commercialization >= 60:
            return "Negotiate Institutional License", "Non-Exclusive License"
        if relevance >= 70:
            return "Build Joint Research Program", "Sponsored Research"
        if relevance >= 50:
            return "Start Technology Transfer", "Technology Transfer"
        return "Connect with Patent Owner", "Industry-Academia Partnership"

    # professor (or default framing for company/other buyer types on listings)
    if commercialization >= 75 and relevance >= 70:
        return "Buy Patent", "Patent Purchase"
    if commercialization >= 60:
        return "Request Licensing", "Patent Licensing"
    if relevance >= 70:
        return "Initiate Research Collaboration", "Research Collaboration"
    if relevance >= 50:
        return "Schedule Technical Discussion", "Joint Development"
    return "Contact Inventor", "Co-Innovation Partnership"


def _collaboration_opportunity(target_type: str, shared: List[str]) -> str:
    domain_phrase = f" around {', '.join(shared[:2])}" if shared else ""
    return {
        "company": f"License or co-develop the technology{domain_phrase} for a commercial product.",
        "employee": f"Bring this technology into your organization's roadmap{domain_phrase}.",
        "student": f"Use this as a foundation for a research project, thesis, or startup{domain_phrase}.",
        "professor": f"Co-research, co-patent, or interdisciplinary collaboration{domain_phrase}.",
        "institute": f"Joint research partnership or technology-transfer agreement{domain_phrase}.",
    }.get(target_type, "Explore a collaboration.")


def _confidence(semantic_score: float, keyword_score: float, has_signal: bool) -> str:
    """High: both signals agree and are strong. Medium: one strong signal.
    Low: everything else (including keyword-only fallback with weak overlap)."""
    if not has_signal:
        return "low"
    if semantic_score >= 55 and keyword_score >= 40:
        return "high"
    if semantic_score >= 65 or keyword_score >= 60:
        return "high"
    if semantic_score >= 35 or keyword_score >= 30:
        return "medium"
    return "low"


# ─── Unified result shape ───────────────────────────────────────────────────

@dataclass
class MatchResult:
    target_kind: str   # "company"|"professor"|"student"|"employee"|"institute"|"listing"|"patent"
    target_id: str
    target_name: str
    tag: str = ""
    score: float = 0.0
    semantic_score: float = 0.0
    keyword_score: float = 0.0
    confidence: str = "low"
    reasons: List[str] = field(default_factory=list)
    matching_domains: List[str] = field(default_factory=list)
    matching_keywords: List[str] = field(default_factory=list)
    # direction A (patent -> audience) enrichment
    next_action: str = ""
    shared_expertise: List[str] = field(default_factory=list)
    collaboration_opportunity: str = ""
    # direction B/D (-> listing) enrichment
    professor_id: str = ""
    professor_name: str = ""
    department: str = ""
    status: str = ""
    asking_price_inr: Optional[float] = None
    licensing_terms: Dict[str, Any] = field(default_factory=dict)
    domain_tags: List[str] = field(default_factory=list)
    industry_tags: List[str] = field(default_factory=list)
    # direction C (buyer -> raw patent pool) enrichment
    technology_domain: str = ""
    commercialization_score: float = 0.0
    patent_readiness: str = ""
    suggested_action: str = ""
    collaboration_mode: str = ""
    institute: str = ""  # the owning professor's affiliated institute

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "tag": self.tag,
            "score": self.score,
            "semantic_score": self.semantic_score,
            "keyword_score": self.keyword_score,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "matching_domains": self.matching_domains,
            "matching_keywords": self.matching_keywords,
            "next_action": self.next_action,
            "shared_expertise": self.shared_expertise,
            "collaboration_opportunity": self.collaboration_opportunity,
            "professor_id": self.professor_id,
            "professor_name": self.professor_name,
            "department": self.department,
            "status": self.status,
            "asking_price_inr": self.asking_price_inr,
            "licensing_terms": self.licensing_terms,
            "domain_tags": self.domain_tags,
            "industry_tags": self.industry_tags,
            "technology_domain": self.technology_domain,
            "commercialization_score": self.commercialization_score,
            "patent_readiness": self.patent_readiness,
            "suggested_action": self.suggested_action,
            "collaboration_mode": self.collaboration_mode,
            "institute": self.institute,
        }


# ─── Shared embedder singleton + namespaced embedding cache ────────────────
# Loading the sentence-transformers model takes ~15-40s (disk + model init).
# Every direction's scorer is created fresh per request, so this MUST be a
# process-wide singleton - otherwise every API call reloads the model from
# scratch. One cache dict-of-dicts, keyed by a namespace string (e.g.
# "audience:professor", "listings", "patent_pool"), replaces what used to be
# two separate module-level caches - a namespace is just "which pool of
# candidate ids/vectors this is", so any pool can share the same structure.

_SHARED_EMBEDDER: Optional[EmbeddingEngine] = None
_EMBEDDING_CACHE: Dict[str, Dict[str, Any]] = {}


def _get_shared_embedder() -> EmbeddingEngine:
    global _SHARED_EMBEDDER
    if _SHARED_EMBEDDER is None:
        _SHARED_EMBEDDER = EmbeddingEngine()
    return _SHARED_EMBEDDER


def _cached_embeddings(namespace: str, ids: List[str], texts: List[str]) -> Any:
    """Return a matrix of vectors aligned to `ids`, reusing a process-wide
    per-id cache (keyed by namespace) and only encoding whatever ids aren't
    in it yet."""
    embedder = _get_shared_embedder()
    cache = _EMBEDDING_CACHE.setdefault(namespace, {})
    missing = [i for i, cid in enumerate(ids) if cid not in cache]
    if missing:
        new_vecs = embedder.encode_batch([texts[i] for i in missing])
        for j, i in enumerate(missing):
            cache[ids[i]] = new_vecs[j]
    return np.stack([cache[cid] for cid in ids])


def prewarm_candidates(target_type: str, candidates: List[Dict[str, Any]]) -> None:
    """Populate the embedding cache for one audience type ahead of the first
    real request (direction A). Candidate embeddings don't depend on which
    patent is being matched, so this can run once at server startup."""
    embedder = _get_shared_embedder()
    if not embedder.is_ready or target_type not in _REQUEST_ADAPTERS:
        return
    adapter = _REQUEST_ADAPTERS[target_type]
    ids: List[str] = []
    texts: List[str] = []
    for candidate in candidates:
        cid, _, _ = _identity(target_type, candidate)
        if not cid:
            continue
        text = embedder.request_text(adapter(candidate))
        if text.strip():
            ids.append(str(cid))
            texts.append(text)
    if texts:
        vecs = embedder.encode_batch(texts)
        cache = _EMBEDDING_CACHE.setdefault(f"audience:{target_type}", {})
        for cid, vec in zip(ids, vecs):
            cache[cid] = vec


def prewarm_patent_pool(professors: List[Dict[str, Any]]) -> None:
    """Populate the embedding cache for the raw platform-wide patent pool
    ahead of the first real request (direction C)."""
    embedder = _get_shared_embedder()
    if not embedder.is_ready:
        return
    ids: List[str] = []
    texts: List[str] = []
    for prof in professors:
        professor_id = str(prof.get("professor_id", ""))
        department = str(prof.get("department", ""))
        for patent in prof.get("patents") or []:
            pid = patent_id(patent, professor_id)
            if pid in _EMBEDDING_CACHE.get("patent_pool", {}):
                continue
            text = _patent_text(patent, department)
            if text.strip():
                ids.append(pid)
                texts.append(text)
    if texts:
        vecs = embedder.encode_batch(texts)
        cache = _EMBEDDING_CACHE.setdefault("patent_pool", {})
        for pid, vec in zip(ids, vecs):
            cache[pid] = vec


# ─── Core scoring loop ──────────────────────────────────────────────────────
# Every direction reduces to: one FIXED side (the query - patent-shaped in
# direction A, request-shaped in directions B/C/D) scored against MANY
# candidates (request-shaped in direction A, patent-shaped in B/C/D), via
# PatentScorer.score_relevance({"patents": [patent_shaped]}, request_shaped)
# for the keyword/domain pass, plus a semantic cosine-similarity pass shared
# across all four directions.

_scorer = PatentScorer()


def _score_pool(
    *,
    query_patent: Optional[Dict[str, Any]],
    query_request: Optional[Dict[str, Any]],
    candidate_ids: List[str],
    candidate_names: List[str],
    candidate_tags: List[str],
    candidate_patent_shapes: Optional[List[Dict[str, Any]]],
    candidate_requests: Optional[List[Dict[str, Any]]],
    candidate_texts: List[str],
    namespace: str,
    semantic_weight: float,
    keyword_weight: float,
    top_k: Optional[int],
) -> List[Tuple[str, str, str, float, float, float, str, List[str], List[str], List[str]]]:
    """Returns list of (id, name, tag, score, semantic_score, keyword_score,
    confidence, reasons, matching_domains, matching_keywords), sorted desc,
    truncated to top_k. Exactly one of (query_patent, query_request) and
    exactly one of (candidate_patent_shapes, candidate_requests) must be set."""
    embedder = _get_shared_embedder()
    n = len(candidate_ids)

    # Pass 1: keyword/domain scoring (cheap, no ML).
    keyword_scores: List[float] = []
    matching_domains_list: List[List[str]] = []
    matching_keywords_list: List[List[str]] = []
    for i in range(n):
        if query_patent is not None:
            rel = _scorer.score_relevance({"patents": [query_patent]}, candidate_requests[i])
        else:
            rel = _scorer.score_relevance({"patents": [candidate_patent_shapes[i]]}, query_request)
        keyword_scores.append(float(rel.relevance_score))
        matching_domains_list.append(rel.matching_domains)
        matching_keywords_list.append(rel.matching_keywords)

    # Pass 2: semantic scoring, batched (one encode() for the fixed side,
    # one encode_batch() for every candidate - never one encode() per item).
    semantic_scores = [0.0] * n
    embed_ready = embedder.is_ready
    if embed_ready and n:
        try:
            if query_patent is not None:
                fixed_vec = embedder.encode(_patent_text(query_patent))
            else:
                fixed_text = embedder.request_text(query_request)
                fixed_vec = embedder.encode(fixed_text) if fixed_text.strip() else None

            if fixed_vec is not None:
                non_empty_idx = [i for i, t in enumerate(candidate_texts) if t.strip()]
                if non_empty_idx:
                    ids = [candidate_ids[i] for i in non_empty_idx]
                    vecs = _cached_embeddings(namespace, ids, [candidate_texts[i] for i in non_empty_idx])
                    sims = (vecs @ fixed_vec.T).flatten()
                    for j, i in enumerate(non_empty_idx):
                        semantic_scores[i] = max(0.0, min(100.0, float(sims[j]) * 100))
            else:
                embed_ready = False
        except Exception:
            embed_ready = False  # falls back to keyword-only for this batch

    results = []
    for i in range(n):
        keyword_score = keyword_scores[i]
        semantic_score = semantic_scores[i]
        hybrid = (semantic_weight * semantic_score + keyword_weight * keyword_score) if embed_ready else keyword_score
        has_signal = semantic_score > 0 or keyword_score > 0
        if hybrid <= 0:
            continue
        reasons = _build_reasons(matching_domains_list[i], matching_keywords_list[i])
        if embed_ready and semantic_score >= 50:
            reasons.append(f"Strong semantic similarity ({round(semantic_score)}%) between the two profiles")
        results.append((
            candidate_ids[i], candidate_names[i], candidate_tags[i],
            round(hybrid, 1), round(semantic_score, 1), round(keyword_score, 1),
            _confidence(semantic_score, keyword_score, has_signal),
            reasons, matching_domains_list[i], matching_keywords_list[i],
        ))

    results.sort(key=lambda r: -r[3])
    return results[:top_k] if top_k else results


# ─── Direction A: Patent -> Audience ────────────────────────────────────────

def match_patent_to_audience(
    patent: Dict[str, Any],
    target_type: str,
    candidates: List[Dict[str, Any]],
    exclude_id: Optional[str] = None,
    top_k: Optional[int] = 10,
) -> List[MatchResult]:
    if target_type not in _REQUEST_ADAPTERS:
        raise ValueError(f"Unknown target_type: {target_type}")
    adapter = _REQUEST_ADAPTERS[target_type]

    ids, names, tags, requests, texts = [], [], [], [], []
    embedder = _get_shared_embedder()
    for candidate in candidates:
        cid, name, tag = _identity(target_type, candidate)
        if not cid or (exclude_id and cid == exclude_id):
            continue
        request = adapter(candidate)
        ids.append(str(cid))
        names.append(name)
        tags.append(tag)
        requests.append(request)
        texts.append(embedder.request_text(request))

    scored = _score_pool(
        query_patent=patent, query_request=None,
        candidate_ids=ids, candidate_names=names, candidate_tags=tags,
        candidate_patent_shapes=None, candidate_requests=requests, candidate_texts=texts,
        namespace=f"audience:{target_type}", semantic_weight=0.65, keyword_weight=0.35, top_k=top_k,
    )

    out: List[MatchResult] = []
    for cid, name, tag, score, sem, kw, conf, reasons, domains, keywords in scored:
        shared = list(dict.fromkeys(domains + keywords[:3]))
        out.append(MatchResult(
            target_kind=target_type, target_id=cid, target_name=name, tag=tag,
            score=score, semantic_score=sem, keyword_score=kw, confidence=conf,
            reasons=reasons, matching_domains=domains, matching_keywords=keywords,
            next_action=_NEXT_ACTION.get(target_type, "Connect"),
            shared_expertise=shared[:5],
            collaboration_opportunity=_collaboration_opportunity(target_type, shared),
        ))
    return out


def match_patent_to_all_audiences(
    patent: Dict[str, Any],
    candidates_by_type: Dict[str, List[Dict[str, Any]]],
    exclude_id: Optional[str] = None,
    top_k: int = 5,
) -> Dict[str, List[MatchResult]]:
    """Categorized output: one ranked list per audience type, computed in a
    single call - powers the Professor Dashboard's 'Best Matching ...' view."""
    out: Dict[str, List[MatchResult]] = {}
    for target_type in TARGET_TYPES:
        candidates = candidates_by_type.get(target_type, [])
        out[target_type] = match_patent_to_audience(
            patent, target_type, candidates,
            exclude_id=exclude_id if target_type == "professor" else None,
            top_k=top_k,
        )
    return out


# ─── Direction B: Buyer -> Patent Listings ──────────────────────────────────

def match_buyer_to_listings(
    buyer_type: str,
    buyer_profile: Dict[str, Any],
    listings: List[Dict[str, Any]],
    top_k: Optional[int] = 20,
    domain: Optional[str] = None,
    industry: Optional[str] = None,
    max_price: Optional[float] = None,
    professor_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[MatchResult]:
    if buyer_type not in _REQUEST_ADAPTERS:
        raise ValueError(f"Unknown buyer_type: {buyer_type}")
    request = _REQUEST_ADAPTERS[buyer_type](buyer_profile)
    professor_lookup = professor_lookup or {}

    ids, names, tags, patent_shapes, texts, raw_listings = [], [], [], [], [], []
    for listing in listings:
        if domain and domain not in (listing.get("domain_tags") or []):
            continue
        if industry and industry not in (listing.get("industry_tags") or []):
            continue
        price = listing.get("asking_price_inr")
        if max_price is not None and price is not None and price > max_price:
            continue
        lid = listing.get("listing_id", "")
        if not lid:
            continue
        ids.append(lid)
        names.append(listing.get("title", ""))
        tags.append(listing.get("status", ""))
        shaped = _listing_as_patent(listing)
        patent_shapes.append(shaped)
        texts.append(_patent_text(shaped))
        raw_listings.append(listing)

    scored = _score_pool(
        query_patent=None, query_request=request,
        candidate_ids=ids, candidate_names=names, candidate_tags=tags,
        candidate_patent_shapes=patent_shapes, candidate_requests=None, candidate_texts=texts,
        namespace="listings", semantic_weight=0.65, keyword_weight=0.35, top_k=top_k,
    )

    by_id = {l["listing_id"]: l for l in raw_listings}
    out: List[MatchResult] = []
    for cid, name, tag, score, sem, kw, conf, reasons, domains, keywords in scored:
        listing = by_id[cid]
        prof = professor_lookup.get(str(listing.get("professor_id", "")), {})
        out.append(MatchResult(
            target_kind="listing", target_id=cid, target_name=name, tag=tag,
            score=score, semantic_score=sem, keyword_score=kw, confidence=conf,
            reasons=reasons, matching_domains=domains, matching_keywords=keywords,
            professor_id=listing.get("professor_id", ""),
            professor_name=prof.get("name", ""),
            department=prof.get("department", ""),
            status=listing.get("status", ""),
            asking_price_inr=listing.get("asking_price_inr"),
            licensing_terms=listing.get("licensing_terms") or {},
            domain_tags=listing.get("domain_tags") or [],
            industry_tags=listing.get("industry_tags") or [],
        ))
    return out


# ─── Direction D: Technology Request -> Patent Listings ────────────────────

def match_technology_request_to_listings(
    title: str,
    description: str,
    keywords: List[str],
    listings: List[Dict[str, Any]],
    professor_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
    top_k: Optional[int] = 10,
) -> List[MatchResult]:
    """Match a posted 'I need X' Technology Request against active listings.
    Same core as match_buyer_to_listings, just with a request built directly
    from the request's own fields instead of a registered buyer profile."""
    request = {
        "project_description": description,
        "challenges": "",
        "industry": "",
        "technical_area": [title],
        "required_expertise": keywords or [],
        "tech_stack": [],
    }
    professor_lookup = professor_lookup or {}

    ids, names, tags, patent_shapes, texts, raw_listings = [], [], [], [], [], []
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
        raw_listings.append(listing)

    scored = _score_pool(
        query_patent=None, query_request=request,
        candidate_ids=ids, candidate_names=names, candidate_tags=tags,
        candidate_patent_shapes=patent_shapes, candidate_requests=None, candidate_texts=texts,
        namespace="listings", semantic_weight=0.65, keyword_weight=0.35, top_k=top_k,
    )

    by_id = {l["listing_id"]: l for l in raw_listings}
    out: List[MatchResult] = []
    for cid, name, tag, score, sem, kw, conf, reasons, domains, keywords_out in scored:
        listing = by_id[cid]
        prof = professor_lookup.get(str(listing.get("professor_id", "")), {})
        out.append(MatchResult(
            target_kind="listing", target_id=cid, target_name=name, tag=tag,
            score=score, semantic_score=sem, keyword_score=kw, confidence=conf,
            reasons=reasons, matching_domains=domains, matching_keywords=keywords_out,
            professor_id=listing.get("professor_id", ""),
            professor_name=prof.get("name", ""),
            department=prof.get("department", ""),
            status=listing.get("status", ""),
            asking_price_inr=listing.get("asking_price_inr"),
            licensing_terms=listing.get("licensing_terms") or {},
            domain_tags=listing.get("domain_tags") or [],
            industry_tags=listing.get("industry_tags") or [],
        ))
    return out


# ─── Direction C: Buyer (Professor/Institute) -> Raw Patent Pool ───────────

def _flatten_patent_pool(professors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pool: List[Dict[str, Any]] = []
    for prof in professors:
        professor_id = str(prof.get("professor_id", ""))
        professor_name = str(prof.get("name", ""))
        department = str(prof.get("department", ""))
        institute = str(prof.get("institute") or _INSTITUTION_NAME)
        for patent in prof.get("patents") or []:
            pool.append({
                "patent": patent,
                "professor_id": professor_id,
                "professor_name": professor_name,
                "department": department,
                "institute": institute,
            })
    return pool


def discover_patents_for_buyer(
    buyer_profile: Dict[str, Any],
    buyer_type: str,
    professors: List[Dict[str, Any]],
    top_k: int = 20,
) -> List[MatchResult]:
    """Rank every patent on the platform against one professor-or-institute
    buyer profile. A professor buyer never sees their own patents."""
    if buyer_type not in BUYER_TYPES:
        raise ValueError(f"Unknown buyer_type: {buyer_type}")
    request = _REQUEST_ADAPTERS[buyer_type](buyer_profile)
    exclude_professor_id = _buyer_identity(buyer_type, buyer_profile) if buyer_type == "professor" else None

    pool = [
        entry for entry in _flatten_patent_pool(professors)
        if not exclude_professor_id or entry["professor_id"] != exclude_professor_id
    ]
    if not pool:
        return []

    ids, names, tags, patent_shapes, texts = [], [], [], [], []
    for entry in pool:
        pid = patent_id(entry["patent"], entry["professor_id"])
        ids.append(pid)
        names.append(str(entry["patent"].get("title", "")))
        tags.append(entry["department"])
        patent_shapes.append(entry["patent"])
        texts.append(_patent_text(entry["patent"], entry["department"]))

    scored = _score_pool(
        query_patent=None, query_request=request,
        candidate_ids=ids, candidate_names=names, candidate_tags=tags,
        candidate_patent_shapes=patent_shapes, candidate_requests=None, candidate_texts=texts,
        namespace="patent_pool", semantic_weight=0.70, keyword_weight=0.30, top_k=top_k,
    )

    by_id = {pid: entry for pid, entry in zip(ids, pool)}
    out: List[MatchResult] = []
    for cid, name, tag, score, sem, kw, conf, reasons, domains, keywords in scored:
        entry = by_id[cid]
        patent = entry["patent"]
        own_domains = _classify_domain(_patent_text(patent, entry["department"]))
        technology_domain = own_domains[0] if own_domains else (entry["department"] or "General R&D")
        commercialization = _commercialization_score(patent)
        suggested_action, collaboration_mode = _suggested_action_and_mode(buyer_type, score, commercialization)
        out.append(MatchResult(
            target_kind="patent", target_id=cid, target_name=name, tag=tag,
            score=score, semantic_score=sem, keyword_score=kw, confidence=conf,
            reasons=reasons, matching_domains=domains, matching_keywords=keywords,
            professor_id=entry["professor_id"], professor_name=entry["professor_name"],
            department=entry["department"], status=patent.get("status", ""),
            technology_domain=technology_domain,
            commercialization_score=commercialization,
            patent_readiness=_patent_readiness(patent),
            suggested_action=suggested_action,
            collaboration_mode=collaboration_mode,
            institute=entry["institute"],
        ))
    return out


@dataclass
class ProfessorPatentGroup:
    """One professor's patents from a direction-C discovery run, grouped
    after scoring rather than before - see group_patents_by_professor()."""
    professor_id: str
    professor_name: str
    department: str
    institute: str
    patent_count: int
    max_score: float
    average_score: float
    patents: List[MatchResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "professor_id": self.professor_id,
            "professor_name": self.professor_name,
            "department": self.department,
            "institute": self.institute,
            "patent_count": self.patent_count,
            "max_score": self.max_score,
            "average_score": self.average_score,
            "patents": [p.to_dict() for p in self.patents],
        }


def group_patents_by_professor(
    matches: List[MatchResult],
    top_k_professors: Optional[int] = None,
    patents_per_professor: Optional[int] = None,
) -> List[ProfessorPatentGroup]:
    """Group an already-scored discover_patents_for_buyer() result by
    professor (surfacing each professor's affiliated institute), sorted by
    each professor's best-scoring patent. Callers MUST pass an untruncated
    match list (discover_patents_for_buyer(..., top_k=None)) and truncate
    here instead - truncating before grouping would undercount or drop
    professors whose several decent-but-not-top-K patents each fall outside
    a small top_k cutoff (same reasoning as Matching Engine 7's professor
    aggregation). No second embedding pass is needed since this operates
    directly on MatchResults that already carry score/professor/institute."""
    groups: Dict[str, List[MatchResult]] = {}
    for m in matches:
        if not m.professor_id:
            continue
        groups.setdefault(m.professor_id, []).append(m)

    out: List[ProfessorPatentGroup] = []
    for pid, ms in groups.items():
        ms.sort(key=lambda m: -m.score)
        featured = ms[:patents_per_professor] if patents_per_professor else ms
        out.append(ProfessorPatentGroup(
            professor_id=pid,
            professor_name=ms[0].professor_name,
            department=ms[0].department,
            institute=ms[0].institute,
            patent_count=len(ms),
            max_score=ms[0].score,
            average_score=round(sum(m.score for m in ms) / len(ms), 1),
            patents=featured,
        ))

    out.sort(key=lambda g: -g.max_score)
    return out[:top_k_professors] if top_k_professors else out


# ─── Backwards-compatible module-level function ────────────────────────────

def match_patent_to_audience_single(
    patent: Dict[str, Any],
    target_type: str,
    candidates: List[Dict[str, Any]],
    exclude_id: Optional[str] = None,
    top_k: Optional[int] = 10,
) -> List[MatchResult]:
    return match_patent_to_audience(patent, target_type, candidates, exclude_id=exclude_id, top_k=top_k)


__all__ = [
    "ProblemStatement", "load_problem_statements", "patent_id", "_build_reasons",
    "TARGET_TYPES", "BUYER_TYPES", "_CATEGORY_LABELS",
    "MatchResult", "ProfessorPatentGroup",
    "match_patent_to_audience", "match_patent_to_all_audiences",
    "match_buyer_to_listings", "discover_patents_for_buyer",
    "group_patents_by_professor",
    "match_technology_request_to_listings",
    "prewarm_candidates", "prewarm_patent_pool",
    "_get_shared_embedder", "_REQUEST_ADAPTERS",
]
