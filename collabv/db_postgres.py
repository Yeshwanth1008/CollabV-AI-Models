"""
CollabV AI - PostgreSQL persistence layer (SQLAlchemy 2.0 + asyncpg + pgvector)

Drop-in replacement for the SQLite layer when DATABASE_URL is set. All models
use SQLAlchemy 2.0 typed declarative style. pgvector extension provides
native vector similarity search on professor embeddings (384 dims).

Public API mirrors collabv/database.py:
    init_db_async(url)
    save_request_async(...)
    save_result_async(...)
    save_feedback_async(...)
    get_history_async(limit)
    get_stats_async()
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from sqlalchemy import (
    Boolean, Float, ForeignKey, Index, Integer, JSON, String, Text, Index,
    select, func as sql_func,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine, AsyncEngine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None  # falls back to JSON storage in dev


# ─── Base + models ────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), default="company_user", nullable=False)
    api_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)


class CompanyRequestRow(Base):
    __tablename__ = "company_requests"

    company_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[Optional[str]] = mapped_column(String(200))
    technical_area: Mapped[Optional[dict]] = mapped_column(JSON)
    required_expertise: Mapped[Optional[dict]] = mapped_column(JSON)
    technology_stack: Mapped[Optional[dict]] = mapped_column(JSON)
    project_description: Mapped[Optional[str]] = mapped_column(Text)
    challenges: Mapped[Optional[str]] = mapped_column(Text)
    collaboration_type: Mapped[Optional[str]] = mapped_column(String(100))
    location_preference: Mapped[Optional[str]] = mapped_column(String(100))
    research_level: Mapped[Optional[str]] = mapped_column(String(50))
    budget_tier: Mapped[Optional[str]] = mapped_column(String(50))
    timeline_months: Mapped[Optional[int]] = mapped_column(Integer)
    raw_text: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)


class ProfessorProfile(Base):
    __tablename__ = "professor_profiles"

    professor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(200), index=True)
    biography: Mapped[Optional[str]] = mapped_column(Text)
    research_areas: Mapped[Optional[dict]] = mapped_column(JSON)
    publications: Mapped[Optional[dict]] = mapped_column(JSON)
    patents: Mapped[Optional[dict]] = mapped_column(JSON)
    raw_profile: Mapped[Optional[dict]] = mapped_column(JSON)  # full original record
    # 384-dim embedding vector. Native pgvector when available, falls back to
    # JSON list when running locally without the extension.
    if Vector is not None:
        embedding: Mapped[Optional[list]] = mapped_column(Vector(384))
    else:
        embedding: Mapped[Optional[dict]] = mapped_column(JSON)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)


class MatchResultRow(Base):
    __tablename__ = "match_results"

    match_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(64), ForeignKey("company_requests.company_id"), index=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(200))
    top_score: Mapped[Optional[float]] = mapped_column(Float)
    results: Mapped[dict] = mapped_column(JSON, nullable=False)
    parsed_tags: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)


class FeedbackRow(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(64), ForeignKey("match_results.match_id"), index=True)
    professor_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)


class MatchExplanationRow(Base):
    __tablename__ = "match_explanations"

    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    professor_id: Mapped[str] = mapped_column(String(64), index=True)
    request_hash: Mapped[str] = mapped_column(String(64), index=True)
    explanation: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)


class WeightHistoryRow(Base):
    __tablename__ = "weight_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    weights: Mapped[dict] = mapped_column(JSON, nullable=False)
    improvement_score: Mapped[Optional[float]] = mapped_column(Float)
    feedback_count: Mapped[Optional[int]] = mapped_column(Integer)
    applied_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(String(200))


class DealAssessmentRow(Base):
    __tablename__ = "deal_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(64), ForeignKey("match_results.match_id"), index=True)
    professor_id: Mapped[str] = mapped_column(String(64), index=True)
    success_probability: Mapped[Optional[float]] = mapped_column(Float)
    confidence_level: Mapped[Optional[str]] = mapped_column(String(32))
    band: Mapped[Optional[str]] = mapped_column(String(32))
    assessment: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)


# ─── Marketplace models (mirror Alembic migration 0002) ───────────────────

class PatentListingRow(Base):
    __tablename__ = "patent_listings"

    listing_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    professor_id: Mapped[str] = mapped_column(String(64), ForeignKey("professor_profiles.professor_id"), nullable=False, index=True)
    patent_number: Mapped[Optional[str]] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[Optional[str]] = mapped_column(Text)
    claims_text: Mapped[Optional[str]] = mapped_column(Text)
    inventor_names: Mapped[Optional[dict]] = mapped_column(JSON)
    granted_date: Mapped[Optional[str]] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    licensing_terms: Mapped[Optional[dict]] = mapped_column(JSON)
    asking_price_inr: Mapped[Optional[float]] = mapped_column(Float)
    domain_tags: Mapped[Optional[dict]] = mapped_column(JSON)
    industry_tags: Mapped[Optional[dict]] = mapped_column(JSON)
    abstract_source: Mapped[Optional[str]] = mapped_column(String(32), default="unknown")
    abstract_status: Mapped[Optional[str]] = mapped_column(String(32), default="none")
    indian_patent_number: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    activated_at: Mapped[Optional[float]] = mapped_column(Float)
    approved_at: Mapped[Optional[float]] = mapped_column(Float)
    approved_by_user_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("users.id"))
    if Vector is not None:
        embedding: Mapped[Optional[list]] = mapped_column(Vector(384))
    else:
        embedding: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)


class BuyerProfileRow(Base):
    __tablename__ = "buyer_profiles"

    buyer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), unique=True, nullable=False, index=True)
    org_name: Mapped[str] = mapped_column(String(200), nullable=False)
    org_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    industries_of_interest: Mapped[Optional[dict]] = mapped_column(JSON)
    technical_areas: Mapped[Optional[dict]] = mapped_column(JSON)
    use_cases: Mapped[Optional[str]] = mapped_column(Text)
    tech_maturity_preference: Mapped[Optional[str]] = mapped_column(String(32))
    budget_band: Mapped[Optional[str]] = mapped_column(String(32))
    geographic_scope: Mapped[Optional[dict]] = mapped_column(JSON)
    seller_preferences: Mapped[Optional[dict]] = mapped_column(JSON)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    if Vector is not None:
        embedding: Mapped[Optional[list]] = mapped_column(Vector(384))
    else:
        embedding: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)


class MarketplaceProposalRow(Base):
    __tablename__ = "marketplace_proposals"

    proposal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    listing_id: Mapped[str] = mapped_column(String(64), ForeignKey("patent_listings.listing_id"), nullable=False, index=True)
    buyer_id: Mapped[str] = mapped_column(String(64), ForeignKey("buyer_profiles.buyer_id"), nullable=False, index=True)
    inventor_id: Mapped[str] = mapped_column(String(64), ForeignKey("professor_profiles.professor_id"), nullable=False, index=True)
    proposal_text: Mapped[Optional[str]] = mapped_column(Text)
    match_score: Mapped[Optional[float]] = mapped_column(Float)
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    explanation: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="sent", index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)
    responded_at: Mapped[Optional[float]] = mapped_column(Float)


class MarketplaceInquiryRow(Base):
    __tablename__ = "marketplace_inquiries"

    inquiry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    listing_id: Mapped[str] = mapped_column(String(64), ForeignKey("patent_listings.listing_id"), nullable=False, index=True)
    buyer_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("buyer_profiles.buyer_id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    message: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", index=True)
    match_score_at_inquiry: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)
    responded_at: Mapped[Optional[float]] = mapped_column(Float)


class MarketplaceEventRow(Base):
    __tablename__ = "marketplace_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(32))
    subject_listing_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("patent_listings.listing_id"), index=True)
    subject_buyer_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("buyer_profiles.buyer_id"), index=True)
    match_score_at_event: Mapped[Optional[float]] = mapped_column(Float)
    position_in_ranking: Mapped[Optional[int]] = mapped_column(Integer)
    query_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False, index=True)


class MarketplaceExplanationRow(Base):
    __tablename__ = "marketplace_explanations"

    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    explanation_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, nullable=False)


# ─── Engine + session factory ─────────────────────────────────────────────

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def _convert_url(url: str) -> str:
    """SQLAlchemy needs the asyncpg dialect prefix."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _resolve_database_url() -> str:
    """Resolve DATABASE_URL or build it from individual env vars.

    Supported env-var forms (in priority order):
      1. DATABASE_URL  (full connection string)
      2. DATABASE_HOST + DATABASE_USER + DATABASE_PASSWORD + DATABASE_NAME
         (Terraform sets the host/user/name; Secrets Manager injects the password
         as DB_PASSWORD or DATABASE_PASSWORD)
    """
    full = os.environ.get("DATABASE_URL")
    if full:
        return full
    host = os.environ.get("DATABASE_HOST")
    if host:
        user = os.environ.get("DATABASE_USER", "collabv")
        pw = os.environ.get("DATABASE_PASSWORD") or os.environ.get("DB_PASSWORD", "")
        name = os.environ.get("DATABASE_NAME", "collabv")
        port = os.environ.get("DATABASE_PORT", "5432")
        return f"postgresql://{user}:{pw}@{host}:{port}/{name}"
    raise RuntimeError("DATABASE_URL or DATABASE_HOST must be set")


async def create_engine(url: Optional[str] = None) -> AsyncEngine:
    """Build the async engine + sessionmaker (idempotent)."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    db_url = _convert_url(url or _resolve_database_url())
    _engine = create_async_engine(
        db_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_session() -> AsyncSession:
    if _sessionmaker is None:
        raise RuntimeError("create_engine() must be called first")
    return _sessionmaker()


async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async generator yielding a session with commit/rollback."""
    session = get_session()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ─── Schema management ────────────────────────────────────────────────────

async def init_db_async(url: Optional[str] = None) -> None:
    """Create all tables. Also creates the vector extension if available."""
    await create_engine(url)
    async with _engine.begin() as conn:
        # Try to create the pgvector extension - safe if it already exists
        try:
            from sqlalchemy import text
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            pass  # extension may not be available in dev
        await conn.run_sync(Base.metadata.create_all)


# ─── Persistence helpers (mirror collabv/database.py signatures) ─────────

async def save_request_async(company_id: str, data: dict) -> None:
    async with get_session() as s:
        row = await s.get(CompanyRequestRow, company_id)
        if row is None:
            row = CompanyRequestRow(company_id=company_id)
            s.add(row)
        row.company_name = data.get("company_name", "")
        row.industry = data.get("industry", "")
        row.technical_area = data.get("technical_area", [])
        row.required_expertise = data.get("required_expertise", [])
        row.technology_stack = data.get("technology_stack", data.get("tech_stack", []))
        row.project_description = data.get("project_description", "")
        row.challenges = data.get("challenges", "")
        row.collaboration_type = data.get("collaboration_type", "")
        row.location_preference = data.get("location_preference", "")
        row.research_level = data.get("research_level", "")
        row.budget_tier = data.get("budget_tier", "")
        row.timeline_months = int(data.get("timeline_months", 0) or 0)
        row.raw_text = data.get("raw_text", "")
        row.created_at = time.time()
        await s.commit()


async def save_result_async(match_id: str, company_id: str, company_name: str,
                            results: list, parsed_tags: Optional[dict] = None) -> None:
    top_score = float(results[0]["score"]) if results else 0.0
    async with get_session() as s:
        row = MatchResultRow(
            match_id=match_id, company_id=company_id, company_name=company_name,
            top_score=top_score, results=results, parsed_tags=parsed_tags,
            created_at=time.time(),
        )
        s.add(row)
        await s.commit()


async def save_feedback_async(match_id: str, professor_id: str,
                              action: str, reason: str = "") -> None:
    async with get_session() as s:
        s.add(FeedbackRow(
            match_id=match_id, professor_id=professor_id,
            action=action, reason=reason, created_at=time.time(),
        ))
        await s.commit()


async def get_history_async(limit: int = 20) -> List[dict]:
    async with get_session() as s:
        stmt = select(MatchResultRow).order_by(MatchResultRow.created_at.desc()).limit(limit)
        rows = (await s.execute(stmt)).scalars().all()
    out = []
    for r in rows:
        results = r.results or []
        out.append({
            "match_id": r.match_id,
            "company_id": r.company_id,
            "company_name": r.company_name,
            "top_score": r.top_score,
            "num_results": len(results),
            "top_professor": results[0]["professor_name"] if results else None,
            "top_department": results[0]["department"] if results else None,
            "parsed_tags": r.parsed_tags,
            "created_at": r.created_at,
        })
    return out


async def get_stats_async() -> dict:
    async with get_session() as s:
        total_requests = (await s.execute(select(sql_func.count(CompanyRequestRow.company_id)))).scalar_one()
        total_matches = (await s.execute(select(sql_func.count(MatchResultRow.match_id)))).scalar_one()
        total_feedback = (await s.execute(select(sql_func.count(FeedbackRow.id)))).scalar_one()
        avg_score = (await s.execute(select(sql_func.avg(MatchResultRow.top_score)))).scalar_one()
    return {
        "total_requests": int(total_requests or 0),
        "total_matches": int(total_matches or 0),
        "total_feedback": int(total_feedback or 0),
        "avg_top_score": round(float(avg_score), 1) if avg_score else 0,
    }


# ─── Vector search helper ─────────────────────────────────────────────────

async def vector_search(query_embedding: list, top_k: int = 20) -> List[tuple[str, float]]:
    """Native pgvector cosine similarity search."""
    if Vector is None:
        raise RuntimeError("pgvector not installed; falls back to FAISS via embeddings.py")
    async with get_session() as s:
        from sqlalchemy import text
        stmt = text("""
            SELECT professor_id, 1 - (embedding <=> :q) AS sim
            FROM professor_profiles
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> :q
            LIMIT :k
        """)
        rows = (await s.execute(stmt, {"q": query_embedding, "k": top_k})).all()
    return [(r[0], float(r[1])) for r in rows]


__all__ = [
    "Base", "User", "CompanyRequestRow", "ProfessorProfile", "MatchResultRow",
    "FeedbackRow", "MatchExplanationRow", "WeightHistoryRow", "DealAssessmentRow",
    # Marketplace
    "PatentListingRow", "BuyerProfileRow", "MarketplaceProposalRow",
    "MarketplaceInquiryRow", "MarketplaceEventRow", "MarketplaceExplanationRow",
    # Engine + helpers
    "create_engine", "get_session", "session_scope", "init_db_async",
    "save_request_async", "save_result_async", "save_feedback_async",
    "get_history_async", "get_stats_async", "vector_search",
]
