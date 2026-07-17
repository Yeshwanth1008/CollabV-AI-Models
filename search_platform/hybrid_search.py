"""
Retrieval + fusion + metadata filtering — the middle of the pipeline:

  embedding -> vector search -> BM25 search -> hybrid fusion -> metadata filter

Reranking and final score combination happen one layer up in
search_service.py, which is also where spell-correction/expansion are
invoked before this module ever sees the query.
"""
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from .bm25_index import get_bm25_index
from .config import get_settings
from .embeddings import encode_query
from .models import UserProfile, UserRole
from .schemas import SearchFilters
from .vector_store import get_vector_repository

FUSION_WEIGHTS = {"semantic": 0.45, "keyword": 0.30, "name": 0.25}


def _name_score(query: str, name: str) -> float:
    q, n = query.lower().strip(), name.lower().strip()
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if q in n:
        return 0.85
    return fuzz.WRatio(q, n) / 100.0 * 0.75  # fuzzy match capped below exact/substring


def apply_metadata_filters(db: Session, filters: Optional[SearchFilters]) -> Optional[set[str]]:
    """Returns the set of ids passing hard filters, or None if no filters given."""
    if filters is None:
        return None
    stmt = select(UserProfile.id)
    if filters.role:
        stmt = stmt.where(UserProfile.role.in_([r.value if isinstance(r, UserRole) else r for r in filters.role]))
    if filters.organization:
        stmt = stmt.where(UserProfile.organization.ilike(f"%{filters.organization}%"))
    if filters.department:
        stmt = stmt.where(UserProfile.department.ilike(f"%{filters.department}%"))
    if filters.location:
        stmt = stmt.where(UserProfile.location.ilike(f"%{filters.location}%"))
    if filters.min_activity_score is not None:
        stmt = stmt.where(UserProfile.activity_score >= filters.min_activity_score)
    if filters.skills:
        stmt = stmt.where(UserProfile.skills.contains(filters.skills))

    any_filter_set = any([
        filters.role, filters.organization, filters.department,
        filters.location, filters.skills, filters.min_activity_score is not None,
    ])
    if not any_filter_set:
        return None
    return {str(r[0]) for r in db.execute(stmt).all()}


def retrieve_and_fuse(db: Session, query: str, filters: Optional[SearchFilters]) -> list[dict]:
    """
    Broad unfiltered vector + BM25 retrieval, fused, THEN metadata-filtered —
    matching the pipeline order in the spec. Filters narrow after fusion
    rather than before, so a tight filter never starves recall by
    intersecting with only the top-K of an unfiltered ANN search.
    """
    settings = get_settings()
    filtered_ids = apply_metadata_filters(db, filters)
    # If filters are active, search the whole index rather than top_k_candidates
    # so post-fusion filtering can't strand valid matches outside the pool —
    # cheap at this dataset's scale (thousands of rows, in-process indexes).
    pool_size = None if filtered_ids is not None else settings.search_top_k_candidates
    pool_size = pool_size or 100_000

    query_vec = encode_query(query)
    vector_hits = dict(get_vector_repository().search(query_vec, top_k=pool_size))
    bm25_hits = dict(get_bm25_index().search(query, top_k=pool_size))

    union_ids = set(vector_hits) | set(bm25_hits)
    if filtered_ids is not None:
        union_ids &= filtered_ids
    if not union_ids:
        return []

    names = {
        str(r[0]): r[1]
        for r in db.execute(
            select(UserProfile.id, UserProfile.name).where(UserProfile.id.in_(union_ids))
        ).all()
    }

    candidates = []
    for uid in union_ids:
        semantic = max(vector_hits.get(uid, 0.0), 0.0)
        keyword = max(bm25_hits.get(uid, 0.0), 0.0)
        name = _name_score(query, names.get(uid, ""))
        fused = (
            FUSION_WEIGHTS["semantic"] * semantic
            + FUSION_WEIGHTS["keyword"] * keyword
            + FUSION_WEIGHTS["name"] * name
        )
        candidates.append({"id": uid, "semantic": semantic, "keyword": keyword, "name_score": name, "fused": fused})

    candidates.sort(key=lambda c: c["fused"], reverse=True)
    return candidates
