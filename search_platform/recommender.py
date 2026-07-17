"""
/similar-users: pure nearest-neighbor over the profile's own embedding
(same "kind" of profile — near-duplicate research/skill fingerprint).

/recommend: reuses the full search pipeline seeded from the target user's
own skills/research/interests as a pseudo-query, so it benefits from the
same hybrid fusion + rerank + ranking-signal combination as a normal search
— "people and organizations you'd plausibly search for," not just nearest
neighbors.
"""
import uuid

import numpy as np
from sqlalchemy.orm import Session

from .models import UserProfile
from .schemas import MatchExplanation, SearchRequest, SearchResultItem
from .search_service import search
from .vector_store import get_vector_repository


def similar_users(db: Session, user_id: uuid.UUID, limit: int = 10, same_role_only: bool = False) -> list[SearchResultItem]:
    target = db.get(UserProfile, user_id)
    if target is None:
        raise ValueError(f"User {user_id} not found")
    if not target.embedding:
        return []

    vec = np.asarray(target.embedding, dtype=np.float32)
    hits = get_vector_repository().search(vec, top_k=limit + 20)
    hits = [(uid, sim) for uid, sim in hits if uid != str(user_id)]
    if not hits:
        return []

    ids = [uuid.UUID(uid) for uid, _ in hits[: limit + 20]]
    rows = {str(p.id): p for p in db.query(UserProfile).filter(UserProfile.id.in_(ids)).all()}

    results = []
    for uid, sim in hits:
        p = rows.get(uid)
        if p is None:
            continue
        if same_role_only and p.role != target.role:
            continue
        shared_skills = sorted(set(p.skills or []) & set(target.skills or []))
        shared_research = sorted(set(p.research_areas or []) & set(target.research_areas or []))
        summary = (
            f"Similar profile to {target.name}: shares {len(shared_skills)} skill(s) "
            f"and {len(shared_research)} research area(s), semantic similarity {sim:.2f}."
        )
        results.append(SearchResultItem(
            id=p.id, name=p.name, role=p.role, organization=p.organization,
            headline=p.headline, skills=p.skills or [], research_areas=p.research_areas or [],
            matching_score=round(float(sim), 4),
            explanation=MatchExplanation(
                matched_skills=shared_skills, matched_research_areas=shared_research,
                semantic_similarity=round(float(sim), 4), summary=summary,
            ),
        ))
        if len(results) >= limit:
            break

    return results


def recommend(db: Session, user_id: uuid.UUID, limit: int = 10) -> list[SearchResultItem]:
    target = db.get(UserProfile, user_id)
    if target is None:
        raise ValueError(f"User {user_id} not found")

    pseudo_query_parts = (target.research_areas or []) + (target.skills or []) + (target.interests or [])
    pseudo_query = " ".join(pseudo_query_parts) or target.headline or target.name

    resp = search(db, SearchRequest(query=pseudo_query, limit=limit + 1, explain=True))
    return [r for r in resp.results if str(r.id) != str(user_id)][:limit]
