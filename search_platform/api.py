"""
FastAPI app for the AI-powered semantic user search & recommendation
platform. Backend-only service — no UI. Meant to be called by the CollabV
website's own frontend. Endpoints: /search, /autocomplete, /recommend,
/similar-users, /user/{id}, /index-user, /update-user, /explain.
"""
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import recommender
from .cache import get_cache, make_key
from .db import Base, engine, get_session
from .ingest import refresh_indexes, upsert_profile
from .models import UserProfile, UserRole
from .schemas import (
    AutocompleteSuggestion, ExplainRequest, MatchExplanation, RecommendRequest,
    SearchRequest, SearchResponse, SimilarUsersRequest, UserProfileIn, UserProfileOut,
)
from .vocabulary import get_vocabulary
from . import search_service

app = FastAPI(
    title="CollabV Semantic User Search & Recommendation Platform",
    version="1.0",
    description="RAG-based hybrid search (BM25 + BGE embeddings + cross-encoder rerank + LLM explanations) "
                 "over a multi-role professional/academic network.",
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.get("/")
async def service_info():
    """Backend-only service info — no UI is served here. See /docs."""
    return {
        "service": app.title,
        "version": app.version,
        "docs": "/docs",
        "endpoints": ["/search", "/autocomplete", "/recommend", "/similar-users",
                      "/user/{id}", "/index-user", "/update-user/{id}", "/explain", "/health"],
    }


def _to_out(row: UserProfile) -> UserProfileOut:
    return UserProfileOut(
        id=row.id, name=row.name, role=row.role, headline=row.headline, bio=row.bio,
        organization=row.organization, department=row.department, job_title=row.job_title,
        location=row.location, skills=row.skills or [], research_areas=row.research_areas or [],
        interests=row.interests or [], projects=row.projects or [], publications=row.publications or [],
        patents=row.patents or [], experience=row.experience or [], education=row.education or [],
        keywords=row.keywords or [], tags=row.tags or [], languages=row.languages or [],
        recent_posts=row.recent_posts or [], github=row.github, linkedin=row.linkedin,
        website=row.website, followers=row.followers, connections=row.connections,
        activity_score=row.activity_score, profile_completion=row.profile_completion,
    )


@app.post("/search", response_model=SearchResponse)
async def search_endpoint(req: SearchRequest, db: Session = Depends(get_session)):
    cache = get_cache()
    key = make_key("search", req.model_dump(mode="json"))
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = search_service.search(db, req)
    cache.set(key, result)
    return result


@app.get("/autocomplete", response_model=list[AutocompleteSuggestion])
async def autocomplete_endpoint(
    q: str = Query(..., min_length=2), limit: int = Query(10, ge=1, le=20)
):
    vocab = get_vocabulary()
    hits = vocab.autocomplete(q, limit=limit)
    return [
        AutocompleteSuggestion(text=t, type=k, role=UserRole(r) if r else None)
        for t, k, r in hits
    ]


@app.post("/recommend")
async def recommend_endpoint(req: RecommendRequest, db: Session = Depends(get_session)):
    try:
        return recommender.recommend(db, req.user_id, limit=req.limit)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/similar-users")
async def similar_users_endpoint(req: SimilarUsersRequest, db: Session = Depends(get_session)):
    try:
        return recommender.similar_users(db, req.user_id, limit=req.limit, same_role_only=req.same_role_only)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/explain", response_model=MatchExplanation)
async def explain_endpoint(req: ExplainRequest, db: Session = Depends(get_session)):
    """On-demand real-LLM explanation for one result — see search_service.explain_single."""
    try:
        return search_service.explain_single(db, req.query, req.user_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/user/{user_id}", response_model=UserProfileOut)
async def get_user(user_id: uuid.UUID, db: Session = Depends(get_session)):
    row = db.get(UserProfile, user_id)
    if row is None:
        raise HTTPException(404, "User not found")
    return _to_out(row)


@app.post("/index-user", response_model=UserProfileOut)
async def index_user(profile: UserProfileIn, db: Session = Depends(get_session)):
    row = upsert_profile(db, profile)
    get_cache().clear()
    return _to_out(row)


@app.put("/update-user/{user_id}", response_model=UserProfileOut)
async def update_user(user_id: uuid.UUID, profile: UserProfileIn, db: Session = Depends(get_session)):
    try:
        row = upsert_profile(db, profile, user_id=user_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    get_cache().clear()
    return _to_out(row)


@app.get("/health")
async def health(db: Session = Depends(get_session)):
    count = db.query(UserProfile).count()
    return {"status": "healthy", "profiles_indexed": count}


@app.on_event("startup")
async def startup():
    Base.metadata.create_all(bind=engine)
    from .db import SessionLocal
    with SessionLocal() as db:
        refresh_indexes(db)

    # Load the embedding + cross-encoder models now, not on the first
    # request — without this, whoever sends the first search after a
    # (re)start eats the ~30-100s one-time model load themselves.
    import time
    t0 = time.perf_counter()
    from .embeddings import encode_query
    from .reranker import rerank
    encode_query("warmup")
    rerank("warmup", [("warmup-id", "warmup text")])
    print(f"Models warmed up in {time.perf_counter() - t0:.1f}s")
    print("search_platform ready")
