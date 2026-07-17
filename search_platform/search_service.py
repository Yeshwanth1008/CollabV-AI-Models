"""
Orchestrates the full pipeline:

  query -> spell correction -> query expansion -> embedding -> vector search
  -> BM25 -> hybrid fusion -> metadata filtering -> cross-encoder rerank
  -> final ranking-signal combination -> LLM explanation -> highlighted,
  ranked results.
"""
import re
import time
import uuid

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .bm25_index import get_bm25_index
from .embeddings import encode_query
from .explain import build_template_explanation, generate_explanations_batch
from .hybrid_search import retrieve_and_fuse
from .models import UserProfile
from .ranking import combine_scores, skills_match_score
from .reranker import get_reranker, rerank
from .schemas import MatchExplanation, SearchRequest, SearchResponse, SearchResultItem
from .spell_correct import correct_query
from .query_expansion import expand_query
from .vocabulary import get_vocabulary

RERANK_POOL = 20
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")


def highlight(text: str, terms: list[str]) -> str:
    if not text or not terms:
        return text
    unique_terms = sorted({t for t in terms if len(t) > 1}, key=len, reverse=True)
    if not unique_terms:
        return text
    pattern = re.compile("|".join(re.escape(t) for t in unique_terms), re.IGNORECASE)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", text)


def search(db: Session, req: SearchRequest) -> SearchResponse:
    start = time.perf_counter()
    vocab = get_vocabulary()

    corrected_query, was_corrected = correct_query(req.query, vocab)
    retrieval_query = corrected_query
    expanded_query, expansion_terms = expand_query(retrieval_query)

    fused = retrieve_and_fuse(db, expanded_query, req.filters)
    total_candidates = len(fused)
    if not fused:
        return SearchResponse(
            query=req.query,
            corrected_query=corrected_query if was_corrected else None,
            expanded_terms=expansion_terms,
            total_candidates=0,
            results=[],
            search_time_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    pool = fused[: max(RERANK_POOL, req.offset + req.limit)]
    pool_ids = [c["id"] for c in pool]

    rows = db.execute(select(UserProfile).where(UserProfile.id.in_(pool_ids))).scalars().all()
    profiles = {str(p.id): p for p in rows}

    rerank_input = [(c["id"], profiles[c["id"]].searchable_text) for c in pool if c["id"] in profiles]
    rerank_scores = rerank(corrected_query, rerank_input)

    query_tokens = set(TOKEN_RE.findall(expanded_query.lower()))

    combined = []
    for c in pool:
        p = profiles.get(c["id"])
        if p is None:
            continue
        sk_score, matched_skills, matched_research = skills_match_score(query_tokens, p.skills, p.research_areas)
        combined.append({
            "id": c["id"],
            "name": p.name,
            "role": p.role.value,
            "organization": p.organization,
            "headline": p.headline,
            "skills": p.skills or [],
            "research_areas": p.research_areas or [],
            "semantic": c["semantic"],
            "keyword": c["keyword"],
            "rerank": rerank_scores.get(c["id"], 0.0),
            "skills_match": sk_score,
            "matched_skills": matched_skills,
            "matched_research_areas": matched_research,
            "followers": p.followers,
            "connections": p.connections,
            "activity_score": p.activity_score,
            "profile_completion": p.profile_completion,
            "updated_at": p.updated_at,
        })

    combine_scores(combined)  # sorts descending, sets matching_score
    page = combined[req.offset: req.offset + req.limit]

    # Explanations default to the free, instant template — grounded in the
    # same matched_skills/matched_research_areas/scores an LLM call would
    # use, just without the network round trip. A synchronous LLM call here
    # scales with result count (10 results ~= 6s+ of generation), which is
    # not an acceptable cost for every keystroke-triggered search. The real
    # LLM is used for the on-demand "why this match?" path — see
    # explain_single() / POST /explain — where the cost is paid once, for
    # one result, only when a user actually asks.
    results = []
    for c in page:
        matched_keywords = sorted(
            query_tokens & set(TOKEN_RE.findall(" ".join(c["skills"] + c["research_areas"] + [c["headline"]]).lower()))
        )[:6]
        summary = build_template_explanation(c) if req.explain else ""

        explanation = MatchExplanation(
            matched_skills=c["matched_skills"],
            matched_research_areas=c["matched_research_areas"],
            matched_keywords=matched_keywords,
            semantic_similarity=round(c["semantic"], 4),
            keyword_score=round(c["keyword"], 4),
            rerank_score=round(c["rerank"], 4),
            summary=summary,
        )
        results.append(SearchResultItem(
            id=c["id"],
            name=c["name"],
            role=c["role"],
            organization=c["organization"],
            headline=c["headline"],
            skills=c["skills"],
            research_areas=c["research_areas"],
            matching_score=c["matching_score"],
            explanation=explanation,
            highlighted_headline=highlight(c["headline"], list(query_tokens) + expansion_terms),
        ))

    elapsed = (time.perf_counter() - start) * 1000
    return SearchResponse(
        query=req.query,
        corrected_query=corrected_query if was_corrected else None,
        expanded_terms=expansion_terms,
        total_candidates=total_candidates,
        results=results,
        search_time_ms=round(elapsed, 2),
    )


def explain_single(db: Session, query: str, user_id: uuid.UUID) -> MatchExplanation:
    """
    On-demand, real-LLM explanation for exactly one (query, profile) pair —
    the "why this match?" path. Costs one small LLM call, paid only when a
    user actually clicks, instead of eagerly for every result on every
    search (see the comment in search() for why that doesn't scale).
    """
    profile = db.get(UserProfile, user_id)
    if profile is None:
        raise ValueError(f"User {user_id} not found")

    query_tokens = set(TOKEN_RE.findall(query.lower()))
    sk_score, matched_skills, matched_research = skills_match_score(
        query_tokens, profile.skills, profile.research_areas
    )

    semantic = 0.0
    if profile.embedding:
        qvec = encode_query(query)
        pvec = np.asarray(profile.embedding, dtype=np.float32)
        semantic = float(max(qvec @ pvec, 0.0))
    keyword = get_bm25_index().score_for_id(query, str(user_id))
    rerank_score = get_reranker().predict([(query, profile.searchable_text)])[0]
    rerank_score = float(1 / (1 + np.exp(-rerank_score)))

    candidate = {
        "id": str(user_id), "name": profile.name, "role": profile.role.value,
        "headline": profile.headline, "matched_skills": matched_skills,
        "matched_research_areas": matched_research, "semantic": semantic,
        "keyword": keyword, "rerank": rerank_score,
    }
    explanation_text = generate_explanations_batch(query, [], [candidate]).get(
        str(user_id), build_template_explanation(candidate)
    )

    return MatchExplanation(
        matched_skills=matched_skills, matched_research_areas=matched_research,
        semantic_similarity=round(semantic, 4), keyword_score=round(keyword, 4),
        rerank_score=round(rerank_score, 4), summary=explanation_text,
    )
