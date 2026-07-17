"""
CollabV AI - FastAPI Server (v3 - production)
===============================================
REST API for professor-company matching with all v3 enhancements:
  - Patent scoring         (Model 3)
  - Collaboration readiness (Model 4)
  - Deal success scoring   (Model 6)
  - Contract NLP / MoU     (Model 7)
  - Dense embeddings       (sentence-transformers + FAISS)
  - LLM match explanations (Claude / rule-based fallback)
  - Feedback retraining    (Nelder-Mead weight optimization)

Run: uvicorn collabv.api:app --reload --port 8000
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
import json as _json


class _NumpyAwareJSONResponse(JSONResponse):
    """JSONResponse that handles numpy scalars (float32/int64/etc).

    The matching engine returns numpy.float32 from dense-embedding scores; the
    default FastAPI encoder raises on those because numpy scalars aren't
    iterable and don't have __dict__. Without this, any /match/run that goes
    through the dense path 500s — and the smoke test rightly blocks CI on it.
    """
    def render(self, content) -> bytes:
        def _default(o):
            try:
                import numpy as _np
                if isinstance(o, _np.generic):
                    return o.item()
            except ImportError:
                pass
            raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
        return _json.dumps(
            content, ensure_ascii=False, allow_nan=False,
            indent=None, separators=(",", ":"), default=_default,
        ).encode("utf-8")
from pydantic import BaseModel, Field, constr

from .matching_engine import MatchingEngine, CompanyRequest
from .need_parser import parse_need
from . import database as db
from .security import (
    install_security, rate_limit_match, rate_limit_contract,
    require_auth_or_api_key,
)
from .errors import ErrorCode, api_error

# ─── Load .env ──────────────────────────────────────────────────────────────

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

PORT = int(os.getenv("PORT", "8000"))
PROFESSORS_FILE = os.getenv("PROFESSORS_FILE", "professors_live.json")
DB_FILE = os.getenv("DB_FILE", "collabv_data.db")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
ENABLE_EMBEDDINGS = os.getenv("ENABLE_EMBEDDINGS", "true").lower() == "true"
ENABLE_LLM_EXPLAIN = os.getenv("ENABLE_LLM_EXPLAIN", "true").lower() == "true"

# ─── FastAPI App ─────────────────────────────────────────────────────────────

openapi_tags = [
    {"name": "Health",     "description": "Liveness, readiness, and component status."},
    {"name": "Auth",       "description": "Register, login, and rotate API keys."},
    {"name": "Matching",   "description": "Run professor-company matching and retrieve results."},
    {"name": "Professors", "description": "Browse the IITM professor directory and analyse individual profiles."},
    {"name": "Contracts",  "description": "Generate MoUs from templates, parse uploaded contracts, diff two contracts."},
    {"name": "Feedback",   "description": "Accept/reject feedback that feeds retraining."},
    {"name": "Admin",      "description": "Retraining, embeddings rebuild, weight history. Admin role recommended."},
    {"name": "Deals",      "description": "Deal success probability scoring."},
    {"name": "Marketplace","description": "Patent listings + buyer profiles + Mode A retrieval. Listings start as 'draft'; activation goes draft -> pending_approval -> active. Stub-owned listings can only be activated by admin/TTO."},
]

app = FastAPI(
    default_response_class=_NumpyAwareJSONResponse,
    title="CollabV AI",
    description=(
        "B2B platform that matches companies with IIT Madras professors for R&D "
        "collaboration. Live-data-only: the professor directory, patents, "
        "companies, problem statements, student/employee/institute profiles, "
        "job postings, and research opportunities all start empty and are "
        "populated exclusively by real submissions through this API - ranking "
        "uses research alignment, semantic similarity, patent portfolios, "
        "collaboration readiness, and deal-success scoring over whatever data "
        "has been registered live."
    ),
    version="3.0.0",
    debug=DEBUG,
    openapi_tags=openapi_tags,
    contact={"name": "CollabV AI", "url": "https://collabv.ai"},
)

# Security: CORS (configurable origins), security headers, exception handler.
install_security(app)

# ─── State ───────────────────────────────────────────────────────────────────

engine: Optional[MatchingEngine] = None
company_requests_cache: Dict[str, dict] = {}
match_results_cache: Dict[str, dict] = {}
professors_by_id_cache: Dict[str, Dict[str, Any]] = {}

# Mock cross-institute affiliation data. The real professor dataset is
# IIT-Madras-only (every record's "university" field is some spelling of
# "IIT Madras"), so there is no genuine multi-institute affiliation to key
# off of. Institutes buying patents "from professors across different
# institutes" needs varied affiliations to demo against, so each professor
# is deterministically assigned one of these on startup (stable across
# restarts, keyed by professor_id) into a separate "institute" field -
# the real "university" field is left untouched.
_MOCK_INSTITUTES = [
    "IIT Madras", "IIT Bombay", "IIT Delhi", "IIT Kanpur",
    "IIT Kharagpur", "IISc Bangalore", "IIT Guwahati", "IIT Roorkee",
]


def _mock_institute_for_professor(professor_id: str) -> str:
    digest = hashlib.sha256(professor_id.encode("utf-8")).hexdigest()
    return _MOCK_INSTITUTES[int(digest[:8], 16) % len(_MOCK_INSTITUTES)]

# Lazy singletons for v3 features
_deal_scorer = None
_explainer = None
_contract_parser = None
_retrainer = None
_readiness_predictor = None
_patent_scorer = None


def _resolved_db_path() -> str:
    return str(Path(__file__).parent.parent / DB_FILE)


def _get_deal_scorer():
    global _deal_scorer
    if _deal_scorer is None:
        from .deal_scorer import DealScorer, SQLiteFeedbackProvider
        provider = SQLiteFeedbackProvider(_resolved_db_path())
        _deal_scorer = DealScorer(feedback_provider=provider)
    return _deal_scorer


def _get_explainer():
    global _explainer
    if _explainer is None:
        from .explainer import MatchExplainer
        _explainer = MatchExplainer(
            db_path=_resolved_db_path(),
            use_claude=ENABLE_LLM_EXPLAIN,
        )
    return _explainer


def _get_contract_parser():
    global _contract_parser
    if _contract_parser is None:
        from .contract_nlp import ContractParser
        _contract_parser = ContractParser()
    return _contract_parser


def _get_retrainer():
    global _retrainer
    if _retrainer is None:
        from .retrainer import WeightRetrainer
        _retrainer = WeightRetrainer(_resolved_db_path())
    return _retrainer


def _get_readiness_predictor():
    global _readiness_predictor
    if _readiness_predictor is None:
        from .collab_readiness import CollabReadinessPredictor
        _readiness_predictor = CollabReadinessPredictor()
    return _readiness_predictor


def _get_patent_scorer():
    global _patent_scorer
    if _patent_scorer is None:
        from .patent_scorer import PatentScorer
        _patent_scorer = PatentScorer()
    return _patent_scorer


@app.on_event("startup")
async def startup():
    global engine, professors_by_id_cache

    db_path = _resolved_db_path()
    db.DB_PATH = db_path
    db.init_db(db_path)
    app.state.db_path = db_path
    try:
        from .auth import init_auth_tables
        init_auth_tables(db_path)
    except Exception as e:
        print(f"[api] Auth tables init failed: {e}")

    try:
        from .patent_problem_db import init_patent_problem_tables
        init_patent_problem_tables(db_path)
    except Exception as e:
        print(f"[api] Patent/problem-statement tables init failed: {e}")

    try:
        from .patent_marketplace_db import init_patent_marketplace_tables
        init_patent_marketplace_tables(db_path)
    except Exception as e:
        print(f"[api] Patent marketplace tables init failed: {e}")

    try:
        from .job_matching_db import init_job_matching_tables
        init_job_matching_tables(db_path)
    except Exception as e:
        print(f"[api] Job matching tables init failed: {e}")

    try:
        from .research_opportunity_db import init_research_opportunity_tables
        init_research_opportunity_tables(db_path)
    except Exception as e:
        print(f"[api] Research opportunity tables init failed: {e}")

    # Live-data-only: professors_live.json is a permanent, always-present empty
    # base directory - the retired seed archives (iitm_professors_nlp.json etc.)
    # are deliberately never searched for here anymore. The only source of
    # professors is this empty base merged with whatever's been registered
    # live via POST /professor/profile (see the merge block below).
    prof_path = Path(__file__).parent.parent / PROFESSORS_FILE

    engine = MatchingEngine(str(prof_path), enable_embeddings=ENABLE_EMBEDDINGS)
    professors_by_id_cache = {
        str(p.get("professor_id", "")): p for p in engine.professors
    }

    # Merge in professors created/edited via POST /professor/profile since
    # the last restart - the base JSON directory above is static and never
    # rewritten in place, so this DB table is the only durable record of
    # those changes (see patent_marketplace_db.save_professor_profile).
    try:
        from .patent_marketplace_db import list_professor_profiles
        _overrides = list_professor_profiles(db_path)
        for _override in _overrides:
            _pid = str(_override.get("professor_id", ""))
            if not _pid:
                continue
            _existing = professors_by_id_cache.get(_pid)
            if _existing:
                _existing.update(_override)
            else:
                engine.professors.append(_override)
                professors_by_id_cache[_pid] = _override
        if _overrides:
            engine._build_profiles()
            try:
                from .knowledge_graph import KnowledgeGraph
                engine.knowledge_graph = KnowledgeGraph(engine.professors).build()
            except Exception as e:
                print(f"[api] KG rebuild after profile merge failed: {e}")

            # The dense-embedding index was already built/loaded inside
            # MatchingEngine.__init__() from the static JSON list alone -
            # load_index() loads whatever's on disk unconditionally, with no
            # check that it still matches the current professor set. Without
            # this, professors merged in above would be invisible to Tier-3
            # dense retrieval until someone manually deleted the index file.
            # A full rebuild is cheap at our scale (hundreds of professors).
            if engine.embedding_engine is not None and engine.embedding_engine.is_ready:
                try:
                    engine.embedding_engine.build_professor_index(engine.professors)
                    index_path = Path(__file__).parent.parent / "collabv_embeddings.index"
                    engine.embedding_engine.save_index(str(index_path))
                    print(f"[api] Rebuilt embedding index to include merged professor profile override(s)")
                except Exception as e:
                    print(f"[api] Embedding index rebuild after profile merge failed: {e}")

            print(f"[api] Merged {len(_overrides)} persisted professor profile override(s)")
    except Exception as e:
        print(f"[api] Professor profile overrides load failed: {e}")

    for _pid, _prof in professors_by_id_cache.items():
        _prof.setdefault("institute", _mock_institute_for_professor(_pid))
    print(f"[api] Engine ready with {len(engine.professors)} professors")

    def _warm_engine_5():
        # Loading the sentence-transformers model takes ~15-30s, and
        # embedding the professor candidate pool (largest audience type)
        # takes several more seconds - without this, the first "AI Matching
        # Engine" request after every server restart pays both costs
        # synchronously. Runs in a background thread so it doesn't delay
        # server startup/health checks.
        try:
            from .matching_engine_5 import _get_shared_embedder, prewarm_candidates
            eng = _get_shared_embedder()
            print(f"[api] Engine 5 embedder pre-warmed (ready={eng.is_ready})")
            if eng.is_ready and engine:
                prewarm_candidates("professor", engine.professors)
                print(f"[api] Engine 5 professor candidate embeddings pre-warmed ({len(engine.professors)})")
            if eng.is_ready:
                company_candidates = _audience_candidates("company")
                prewarm_candidates("company", company_candidates)
                print(f"[api] Engine 5 company candidate embeddings pre-warmed ({len(company_candidates)})")
            if eng.is_ready and engine:
                from .matching_engine_5 import prewarm_patent_pool
                prewarm_patent_pool(engine.professors)
                print("[api] Patent pool embeddings pre-warmed")
        except Exception as e:
            print(f"[api] Engine 5 embedder pre-warm failed: {e}")

    threading.Thread(target=_warm_engine_5, daemon=True).start()


# ─── Request/Response Models ────────────────────────────────────────────────

class CompanyRequestInput(BaseModel):
    company_name: str = Field(..., max_length=200)
    technical_area: List[str] = Field(default_factory=list, max_length=20)
    industry: str = Field("", max_length=200)
    tech_stack: List[str] = Field(default_factory=list, max_length=20)
    required_expertise: List[str] = Field(default_factory=list, max_length=20)
    project_description: str = Field("", max_length=5000)
    challenges: str = Field("", max_length=5000)
    collaboration_type: str = Field("", max_length=100)
    location_preference: str = Field("Any", max_length=100)
    research_level: str = Field("applied", max_length=50)
    budget_tier: str = Field("medium", max_length=50)
    timeline_months: int = Field(12, ge=0, le=120)


class MatchRunInput(BaseModel):
    company_id: Optional[str] = Field(None, max_length=64)
    top_k: int = Field(10, ge=1, le=50)
    raw_text: Optional[str] = Field(None, max_length=10000)
    company_name: Optional[str] = Field(None, max_length=200)
    location_preference: Optional[str] = Field("Any", max_length=100)
    budget_tier: Optional[str] = Field("medium", max_length=50)
    timeline_months: Optional[int] = Field(12, ge=0, le=120)
    include_deal_score: bool = True
    include_explanations: bool = True
    explain_top_k: int = Field(5, ge=0, le=20)


class NeedParseInput(BaseModel):
    text: str = Field(..., max_length=10000)
    use_claude: bool = True


class FeedbackInput(BaseModel):
    match_id: str = Field(..., max_length=64)
    professor_id: str = Field(..., max_length=64)
    action: str = Field(..., max_length=32)
    reason: str = Field("", max_length=500)


class DealScoreInput(BaseModel):
    match_id: str = Field(..., max_length=64)
    professor_id: str = Field(..., max_length=64)


class ProfessorProfileInput(BaseModel):
    """Spec Layer 1: POST /professor/profile shape."""
    professor_id: Optional[str] = Field(None, max_length=64)
    name: str = Field(..., max_length=200)
    biography: str = Field("", max_length=5000)
    research_areas: List[str] = Field(default_factory=list, max_length=30)
    publications: List[str] = Field(default_factory=list, max_length=200)
    patents: List[Dict[str, Any]] = Field(default_factory=list, max_length=200)
    department: str = Field(..., max_length=200)
    experience_years: Optional[Any] = None
    industry_exposure: List[str] = Field(default_factory=list, max_length=30)
    collaboration_history: str = Field("", max_length=5000)
    technical_expertise: List[str] = Field(default_factory=list, max_length=50)
    university: str = Field("IIT Madras", max_length=200)
    location: str = Field("Chennai", max_length=200)
    contact: Dict[str, Any] = Field(default_factory=dict)


class ContractParseInput(BaseModel):
    text: str = Field(..., max_length=200000)


class ContractCompareInput(BaseModel):
    text_a: str = Field(..., max_length=200000)
    text_b: str = Field(..., max_length=200000)


class ContractGenerateInput(BaseModel):
    type: str
    company_name: str
    professor_name: str
    department: str = ""
    research_area: str = ""
    amount: float = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class SimulateWeightsInput(BaseModel):
    weights: Dict[str, float]


# ─── Helper ─────────────────────────────────────────────────────────────────

def _py(v):
    """Cast numpy scalars to Python primitives. FastAPI's jsonable_encoder
    runs before our custom JSONResponse render path and chokes on numpy
    types, so we have to scrub at the engine boundary."""
    try:
        import numpy as _np
        if isinstance(v, _np.generic):
            return v.item()
    except ImportError:
        pass
    return v


def _build_match_response(match_id, company_id, company_name, results, parsed_tags=None,
                          deal_assessments=None, explanations=None):
    deal_lookup = {a.professor_id: a for a in (deal_assessments or [])}
    exp_lookup = {e.professor_id: e for e in (explanations or [])}

    out = []
    for r in results:
        item = {
            "professor_name": r.professor_name,
            "professor_id": r.professor_id,
            "department": r.department,
            "score": _py(r.score),
            "match_score": _py(r.score),             # spec alias
            # Internal tier scores (back-compat) — all cast since the engine
            # builds these by combining numpy similarity scores.
            "tier1_score": _py(r.tier1_score),
            "tier2_score": _py(r.tier2_score),
            "tier3_score": _py(r.tier3_score),
            # New v4 scores
            "patent_score": _py(r.patent_score),
            "readiness_score": _py(r.readiness_score),
            "contextual_readiness": _py(r.contextual_readiness),
            "innovation_score": _py(r.innovation_score),
            "kg_domain_score": _py(r.kg_domain_score),
            "innovation_bridges": r.innovation_bridges,
            # ─── Spec-aligned aliases (Layer 5) ───
            "skill_score": _py(r.skill_score),
            "domain_score": _py(r.domain_score),
            "application_score": _py(r.application_score),
            "experience_score": _py(r.experience_score),
            "collab_readiness_score": _py(r.collab_readiness_score),
            "top_reasons": r.reasons[:3],
            "reasons": r.reasons,
            "contact": r.contact,
        }
        if r.professor_id in deal_lookup:
            assess = deal_lookup[r.professor_id]
            item["deal_assessment"] = assess.to_dict()
            item["deal_probability"] = assess.success_percent
            item["deal_band"] = assess.band
        if r.professor_id in exp_lookup:
            item["explanation"] = exp_lookup[r.professor_id].to_dict()
        out.append(item)

    resp = {
        "match_id": match_id,
        "company_id": company_id,
        "company_name": company_name,
        "results": out,
    }
    if parsed_tags:
        resp["parsed_tags"] = parsed_tags
    return resp


def _request_from_input(input: MatchRunInput) -> tuple[CompanyRequest, str, Optional[dict]]:
    """Build a CompanyRequest from a /match/run input. Returns (request, company_id, parsed_tags)."""
    if input.raw_text:
        parsed = parse_need(input.raw_text, use_claude=False)
        fields = parsed.to_company_request_fields()
        cid = f"CRQ-{uuid.uuid4().hex[:8].upper()}"
        cname = input.company_name or "Unknown Company"
        request = CompanyRequest(
            company_id=cid,
            company_name=cname,
            technical_area=fields["technical_area"],
            required_expertise=fields["required_expertise"],
            tech_stack=fields["tech_stack"],
            industry=fields["industry"],
            project_description=fields["project_description"],
            challenges=input.raw_text,
            collaboration_type=fields["collaboration_type"],
            location_preference=input.location_preference or "Any",
            research_level=fields["research_level"],
            budget_tier=input.budget_tier or "medium",
            timeline_months=input.timeline_months or 12,
        )
        db.save_request(cid, {
            "company_name": cname,
            "raw_text": input.raw_text,
            **fields,
        })
        parsed_tags = {
            "technical_domains": parsed.technical_domains,
            "required_expertise_tags": parsed.required_expertise_tags,
            "technology_stack": parsed.technology_stack,
            "industry_sector": parsed.industry_sector,
            "rd_type": parsed.rd_type,
            "collaboration_type": parsed.collaboration_type,
        }
        return request, cid, parsed_tags

    if not input.company_id:
        raise api_error(ErrorCode.MISSING_INPUT, "Provide either raw_text or company_id")
    req_data = company_requests_cache.get(input.company_id)
    if not req_data:
        raise api_error(ErrorCode.COMPANY_NOT_FOUND)
    request = CompanyRequest(
        company_id=req_data["company_id"],
        company_name=req_data["company_name"],
        technical_area=req_data["technical_area"],
        industry=req_data["industry"],
        tech_stack=req_data["tech_stack"],
        required_expertise=req_data["required_expertise"],
        project_description=req_data["project_description"],
        challenges=req_data["challenges"],
        collaboration_type=req_data["collaboration_type"],
        location_preference=req_data["location_preference"],
        research_level=req_data["research_level"],
        budget_tier=req_data["budget_tier"],
        timeline_months=req_data["timeline_months"],
    )
    return request, input.company_id, None


# ─── Health / Frontend ──────────────────────────────────────────────────────

@app.get("/health", tags=["Health"], summary="Quick health check")
async def health():
    n_profs = len(engine.professors) if engine else 0
    stats = db.get_stats()

    # Marketplace embedding-engine status surfaces as a top-level degraded flag.
    # Force engine initialization here so /health is meaningful even before any
    # marketplace endpoint has been touched — otherwise the lazy global would
    # be None and we'd silently report degraded=false on a broken install.
    mp_degraded = False
    mp_errors: list[str] = []
    try:
        mp_engine = _get_marketplace_engine()
        if mp_engine.index is not None:
            if mp_engine.index.patent_engine.load_error:
                mp_errors.append(f"patent: {mp_engine.index.patent_engine.load_error}")
            if mp_engine.index.buyer_engine.load_error:
                mp_errors.append(f"buyer: {mp_engine.index.buyer_engine.load_error}")
            mp_degraded = mp_engine.index.embeddings_degraded
    except Exception as exc:
        mp_errors.append(f"marketplace status probe failed: {exc}")
        mp_degraded = True

    return {
        "status": "degraded" if mp_degraded else "ok",
        "version": "3.0.0",
        "professors_loaded": n_profs,
        "embeddings_enabled": bool(engine and engine.embedding_engine and engine.embedding_engine.is_ready),
        "marketplace_embeddings_degraded": mp_degraded,
        "marketplace_embedding_errors": mp_errors,
        "total_matches": stats["total_matches"],
        "avg_score": stats["avg_top_score"],
    }


@app.get(
    "/marketplace/status",
    tags=["Marketplace"],
    summary="Marketplace engine + index health (auth-free; for CI / smoke tests)",
    description=(
        "Returns a self-describing snapshot of the marketplace embedding "
        "engines: whether each model loaded, why it didn't, how many items "
        "are indexed, and a single degraded boolean. CI should assert "
        "degraded=false before exercising any Mode A/B endpoint."
    ),
)
async def marketplace_status():
    try:
        eng = _get_marketplace_engine()
    except Exception as exc:
        return {
            "degraded": True,
            "engine_initialized": False,
            "error": f"engine init failed: {exc}",
        }
    if eng.index is None:
        return {
            "degraded": True,
            "engine_initialized": True,
            "error": "index manager not initialized",
        }
    s = eng.index.stats()
    return {
        "degraded": eng.index.embeddings_degraded,
        "engine_initialized": True,
        "patent_index": s["patent_index"],
        "buyer_index": s["buyer_index"],
    }


@app.get("/")
async def api_status():
    # Pure API service - the standalone bundled demo page has been retired.
    # The real CollabV website (a separate deployment) is the UI; this
    # backend is consumed purely as an API from here on. See /docs for the
    # full endpoint reference.
    return {"message": "CollabV AI API is running. Visit /docs for API documentation."}


# ─── Company / Match ────────────────────────────────────────────────────────

@app.post("/company/request", tags=["Matching"], summary="Save a structured company request")
async def submit_company_request(req: CompanyRequestInput):
    cid = f"CRQ-{uuid.uuid4().hex[:8].upper()}"
    data = {"company_id": cid, **req.model_dump()}
    company_requests_cache[cid] = data
    db.save_request(cid, data)
    return {"company_id": cid, "status": "created"}


@app.post(
    "/match/run",
    tags=["Matching"],
    summary="Rank professors against a company request",
    description=(
        "Submit a free-text brief (raw_text) or a structured company_id. "
        "Returns the top professors ranked by composite score, deal success "
        "probability, and an LLM-generated explanation for the top results."
    ),
    dependencies=[Depends(rate_limit_match), Depends(require_auth_or_api_key)],
)
async def run_matching(input: MatchRunInput):
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)

    request, cid, parsed_tags = _request_from_input(input)
    results = engine.match(request, top_k=input.top_k)
    match_id = f"M-{uuid.uuid4().hex[:8].upper()}"

    deal_assessments = None
    if input.include_deal_score and results:
        try:
            scorer = _get_deal_scorer()
            match_dicts = [
                {
                    "professor_id": r.professor_id,
                    "professor_name": r.professor_name,
                    "department": r.department,
                    "score": r.score,
                    "tier1_score": r.tier1_score,
                    "tier2_score": r.tier2_score,
                    "tier3_score": r.tier3_score,
                    "patent_score": r.patent_score,
                    "readiness_score": r.readiness_score,
                    "contextual_readiness": r.contextual_readiness,
                }
                for r in results
            ]
            deal_assessments = scorer.batch_score(match_dicts, professors_by_id_cache, request)
            for assess in deal_assessments:
                db.save_deal_assessment(match_id, assess.to_dict())
        except Exception as e:
            print(f"[api] Deal scoring failed: {e}")

    explanations = None
    if input.include_explanations and results:
        try:
            explainer = _get_explainer()
            match_dicts = [
                {
                    "professor_id": r.professor_id,
                    "professor_name": r.professor_name,
                    "department": r.department,
                    "score": r.score,
                    "tier1_score": r.tier1_score,
                    "tier2_score": r.tier2_score,
                    "patent_score": r.patent_score,
                    "readiness_score": r.readiness_score,
                    "reasons": r.reasons,
                }
                for r in results[:input.explain_top_k]
            ]
            explanations = explainer.explain_batch(
                match_dicts, professors_by_id_cache, request,
                top_k=input.explain_top_k,
            )
        except Exception as e:
            print(f"[api] Explanation generation failed: {e}")

    resp = _build_match_response(
        match_id, cid, request.company_name, results,
        parsed_tags=parsed_tags,
        deal_assessments=deal_assessments,
        explanations=explanations,
    )
    match_results_cache[match_id] = resp
    db.save_result(match_id, cid, request.company_name, resp["results"], parsed_tags)
    return resp


@app.get("/match/results/{match_id}", tags=["Matching"], summary="Fetch a saved match by ID")
async def get_match_results(match_id: str):
    if match_id in match_results_cache:
        return match_results_cache[match_id]
    raise api_error(ErrorCode.MATCH_NOT_FOUND)


@app.get("/match/results/{match_id}/explain", tags=["Matching"], summary="Regenerate an LLM explanation for one professor in a match")
async def regenerate_explanation(match_id: str, professor_id: str):
    if match_id not in match_results_cache:
        raise api_error(ErrorCode.MATCH_NOT_FOUND)
    resp = match_results_cache[match_id]
    match = next((r for r in resp["results"] if r["professor_id"] == professor_id), None)
    if not match:
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND, "That professor isn't part of this match")
    prof = professors_by_id_cache.get(professor_id, {})
    request = next((c for c in company_requests_cache.values()
                    if c.get("company_id") == resp.get("company_id")), None) or {}
    explanation = _get_explainer().explain_match(prof, request, match)
    return explanation.to_dict()


# ─── Feedback ───────────────────────────────────────────────────────────────

@app.post(
    "/feedback/submit",
    tags=["Feedback"],
    summary="Record accept/reject feedback for a match",
    description="Feedback feeds the weight retraining loop. Action is 'accept' or 'reject'.",
)
async def submit_feedback(input: FeedbackInput):
    db.save_feedback(input.match_id, input.professor_id, input.action, input.reason)
    return {"status": "logged"}


# ─── Need parsing ───────────────────────────────────────────────────────────

@app.post("/needs/parse", tags=["Matching"], summary="Convert plain-text brief to structured tags")
async def parse_needs(input: NeedParseInput):
    result = parse_need(input.text, use_claude=input.use_claude)
    return {
        "parsed": {
            "technical_domains": result.technical_domains,
            "required_expertise_tags": result.required_expertise_tags,
            "technology_stack": result.technology_stack,
            "industry_sector": result.industry_sector,
            "rd_type": result.rd_type,
            "collaboration_type": result.collaboration_type,
            "timeline_months": result.timeline_months,
            "budget_tier": result.budget_tier,
            "ip_preference": result.ip_preference,
            "matching_query": result.matching_query,
        },
        "company_request_fields": result.to_company_request_fields(),
    }


# ─── Professors ─────────────────────────────────────────────────────────────

@app.get("/professors", tags=["Professors"], summary="List professors, optionally filtered by department")
async def list_professors(department: Optional[str] = None, limit: int = 50):
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)
    profs = engine.get_professors(department)
    return {
        "count": len(profs),
        "professors": [
            {
                "professor_id": p.get("professor_id"),
                "name": p["name"],
                "department": p["department"],
                "designation": p.get("designation", ""),
                "research_areas": p.get("research_areas", [])[:5],
                "patent_count": len(p.get("patents") or []),
            }
            for p in profs[:limit]
        ],
    }


@app.post(
    "/professor/profile",
    tags=["Professors"],
    summary="Add or update a professor profile (spec Layer 1)",
    description=(
        "Create a new professor profile or update an existing one. If "
        "professor_id is omitted a new ID is generated. Triggers an embedding "
        "+ knowledge-graph refresh for that professor."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def upsert_professor_profile(req: ProfessorProfileInput):
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)

    pid = req.professor_id or f"USR-PROF-{uuid.uuid4().hex[:8].upper()}"
    payload = req.model_dump()
    payload["professor_id"] = pid

    # Find existing record or append a new one
    existing = professors_by_id_cache.get(pid)
    if existing:
        existing.update(payload)
        prof = existing
        created = False
    else:
        engine.professors.append(payload)
        professors_by_id_cache[pid] = payload
        prof = payload
        created = True
    prof.setdefault("institute", _mock_institute_for_professor(pid))

    # Refresh derived structures so the new prof can be matched immediately
    try:
        engine._build_profiles()
    except Exception as e:
        print(f"[api] _build_profiles failed: {e}")

    # Rebuild knowledge graph (cheap at our scale, ~50ms)
    if engine.knowledge_graph is not None:
        try:
            from .knowledge_graph import KnowledgeGraph
            engine.knowledge_graph = KnowledgeGraph(engine.professors).build()
        except Exception as e:
            print(f"[api] KG rebuild failed: {e}")

    # Recompute embedding for just this professor (incremental update)
    if engine.embedding_engine is not None and engine.embedding_engine.is_ready:
        try:
            from .embeddings import EmbeddingEngine
            text = EmbeddingEngine._professor_text(prof)
            new_vec = engine.embedding_engine.encode(text)
            if engine.embedding_engine._matrix is not None:
                import numpy as np
                if existing:
                    idx = engine.embedding_engine.prof_ids.index(pid)
                    engine.embedding_engine._matrix[idx] = new_vec[0]
                else:
                    engine.embedding_engine._matrix = np.vstack(
                        [engine.embedding_engine._matrix, new_vec]
                    )
                    engine.embedding_engine.prof_ids.append(pid)
        except Exception as e:
            print(f"[api] embedding refresh failed: {e}")

    # Persist so this survives a restart - engine.professors/professors_by_id_cache
    # above are in-memory only, and the base JSON directory is never rewritten.
    try:
        from .patent_marketplace_db import save_professor_profile
        save_professor_profile(pid, prof, _resolved_db_path())
    except Exception as e:
        print(f"[api] Professor profile persistence failed: {e}")

    return {
        "professor_id": pid,
        "status": "created" if created else "updated",
        "name": prof.get("name"),
        "department": prof.get("department"),
    }


@app.delete(
    "/professor/profile/{professor_id}",
    tags=["Professors"],
    summary="Remove a professor profile",
    dependencies=[Depends(require_auth_or_api_key)],
)
async def delete_professor_profile(professor_id: str):
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)
    if professor_id not in professors_by_id_cache:
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND)
    engine.professors = [p for p in engine.professors
                         if str(p.get("professor_id", "")) != professor_id]
    professors_by_id_cache.pop(professor_id, None)
    try:
        engine._build_profiles()
    except Exception:
        pass
    try:
        from .patent_marketplace_db import delete_professor_profile as _delete_persisted
        _delete_persisted(professor_id, _resolved_db_path())
    except Exception as e:
        print(f"[api] Professor profile deletion (persisted) failed: {e}")
    return {"professor_id": professor_id, "status": "deleted"}


@app.get("/professor/{professor_id}", tags=["Professors"], summary="Full professor profile")
async def get_professor(professor_id: str):
    prof = professors_by_id_cache.get(professor_id)
    if not prof:
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND)
    return prof


@app.get("/professor/{professor_id}/patents", tags=["Professors"], summary="Patent portfolio + scoring breakdown")
async def professor_patents(professor_id: str):
    prof = professors_by_id_cache.get(professor_id)
    if not prof:
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND)
    return _get_patent_scorer().get_patent_insights(prof)


@app.get(
    "/professor/{professor_id}/patents-list",
    tags=["Professors"],
    summary="Full patent list with stable patent_id (for the Patent Marketplace 'sell this patent' flow)",
)
async def professor_patents_list(professor_id: str):
    prof = professors_by_id_cache.get(professor_id)
    if not prof:
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND)
    from .matching_engine_5 import patent_id as compute_patent_id
    patents = [
        {
            "patent_id": compute_patent_id(p, professor_id),
            "title": p.get("title", ""),
            "patent_number": p.get("patent_number", ""),
            "filing_date": p.get("filing_date") or p.get("year") or "",
            "status": p.get("status", ""),
        }
        for p in (prof.get("patents") or [])
    ]
    return {"professor_id": professor_id, "count": len(patents), "patents": patents}


@app.get("/professor/{professor_id}/readiness", tags=["Professors"], summary="Collaboration readiness breakdown")
async def professor_readiness(professor_id: str):
    prof = professors_by_id_cache.get(professor_id)
    if not prof:
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND)
    return _get_readiness_predictor().predict_readiness(prof).to_dict()


@app.get("/readiness/departments", tags=["Professors"], summary="Aggregate readiness scores by department")
async def readiness_by_department():
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)
    return _get_readiness_predictor().get_department_readiness(engine.professors)


# ─── Knowledge graph (spec Layer 2) ─────────────────────────────────────────

@app.get("/kg/stats", tags=["Professors"], summary="Knowledge graph node + edge counts")
async def kg_stats():
    if not engine or not engine.knowledge_graph:
        raise api_error(ErrorCode.ENGINE_NOT_READY, "Knowledge graph not built")
    return engine.knowledge_graph.stats()


@app.get(
    "/kg/professor/{professor_id}/related",
    tags=["Professors"],
    summary="Professors related by shared skills/domains/departments (BFS up to N hops)",
)
async def kg_related(professor_id: str, max_hops: int = 2, limit: int = 10):
    if not engine or not engine.knowledge_graph:
        raise api_error(ErrorCode.ENGINE_NOT_READY, "Knowledge graph not built")
    if professor_id not in professors_by_id_cache:
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND)
    related = engine.knowledge_graph.related_professors(professor_id, max_hops=max_hops)
    out = []
    for pid, dist in related[:limit]:
        p = professors_by_id_cache.get(pid)
        if p:
            out.append({
                "professor_id": pid,
                "name": p.get("name"),
                "department": p.get("department"),
                "distance": dist,
            })
    return {"related": out, "total": len(related)}


@app.get(
    "/kg/industry-bridge",
    tags=["Professors"],
    summary="Find professors active in BOTH industries",
)
async def kg_industry_bridge(a: str, b: str):
    if not engine or not engine.knowledge_graph:
        raise api_error(ErrorCode.ENGINE_NOT_READY, "Knowledge graph not built")
    ids = engine.knowledge_graph.industry_bridge(a, b)
    out = []
    for pid in ids[:50]:
        p = professors_by_id_cache.get(pid)
        if p:
            out.append({
                "professor_id": pid,
                "name": p.get("name"),
                "department": p.get("department"),
            })
    return {"bridges": out, "industry_a": a, "industry_b": b}


# ─── Deal scoring ──────────────────────────────────────────────────────────

@app.post("/deal/score", tags=["Deals"], summary="Compute deal success probability for one professor in a match")
async def deal_score(input: DealScoreInput):
    if input.match_id not in match_results_cache:
        raise api_error(ErrorCode.MATCH_NOT_FOUND)
    match_resp = match_results_cache[input.match_id]
    match = next((r for r in match_resp["results"] if r["professor_id"] == input.professor_id), None)
    if not match:
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND, "That professor isn't part of this match")
    prof = professors_by_id_cache.get(input.professor_id, {})
    # Reconstruct request
    cid = match_resp.get("company_id")
    req_data = company_requests_cache.get(cid, {})
    request = CompanyRequest(
        company_id=cid or "",
        company_name=req_data.get("company_name", ""),
        technical_area=req_data.get("technical_area", []),
        industry=req_data.get("industry", ""),
        tech_stack=req_data.get("tech_stack", []),
        required_expertise=req_data.get("required_expertise", []),
        project_description=req_data.get("project_description", ""),
        challenges=req_data.get("challenges", ""),
        collaboration_type=req_data.get("collaboration_type", ""),
        location_preference=req_data.get("location_preference", "Any"),
        research_level=req_data.get("research_level", ""),
        budget_tier=req_data.get("budget_tier", "medium"),
        timeline_months=req_data.get("timeline_months", 12),
    )
    assessment = _get_deal_scorer().score_deal(match, prof, request)
    return assessment.to_dict()


# ─── Contracts ──────────────────────────────────────────────────────────────

@app.post(
    "/contract/parse",
    tags=["Contracts"],
    summary="Extract structured terms from a contract",
    dependencies=[Depends(rate_limit_contract), Depends(require_auth_or_api_key)],
)
async def contract_parse(input: ContractParseInput):
    terms = _get_contract_parser().parse(input.text)
    return terms.to_dict()


@app.post(
    "/contract/compare",
    tags=["Contracts"],
    summary="Diff two contracts and highlight significant changes",
    dependencies=[Depends(rate_limit_contract), Depends(require_auth_or_api_key)],
)
async def contract_compare(input: ContractCompareInput):
    parser = _get_contract_parser()
    a = parser.parse(input.text_a)
    b = parser.parse(input.text_b)
    diff = parser.compare(a, b)
    return diff.to_dict()


@app.get("/contract/templates", tags=["Contracts"], summary="List the 5 built-in MoU templates")
async def contract_templates():
    from .contract_nlp import ContractParser
    return {"templates": ContractParser.list_templates()}


@app.post(
    "/contract/generate",
    tags=["Contracts"],
    summary="Fill an MoU template with company / professor details",
    dependencies=[Depends(require_auth_or_api_key)],
)
async def contract_generate(input: ContractGenerateInput):
    try:
        text = _get_contract_parser().generate_template(
            collab_type=input.type,
            company_name=input.company_name,
            professor_name=input.professor_name,
            department=input.department,
            research_area=input.research_area,
            amount=input.amount,
            start_date=input.start_date,
            end_date=input.end_date,
            **input.extra,
        )
    except ValueError as e:
        raise api_error(ErrorCode.UNKNOWN_TEMPLATE, str(e))
    return {"contract": text}


# ─── Retraining / Embeddings ────────────────────────────────────────────────

@app.post(
    "/retrain/run",
    tags=["Admin"],
    summary="Run weight retraining from feedback",
    description="Requires at least 30 feedback records. Returns the new weights and an improvement score.",
)
async def retrain_run():
    update = _get_retrainer().retrain_weights()
    # Reload weights into the engine
    if engine:
        try:
            from .retrainer import load_weights
            engine.factor_weights = load_weights()
        except Exception:
            pass
    return update.to_dict()


@app.get("/retrain/stats", tags=["Admin"], summary="Feedback analysis + weight-update history")
async def retrain_stats():
    rt = _get_retrainer()
    return {
        "analysis": rt.analyze_feedback().to_dict(),
        "history": rt.get_weight_history(limit=10),
    }


@app.post("/retrain/simulate", tags=["Admin"], summary="Project the gap that proposed weights would produce on historical data")
async def retrain_simulate(input: SimulateWeightsInput):
    sim = _get_retrainer().simulate_weights(input.weights)
    return {
        "weights": sim.weights,
        "accepted_mean": sim.accepted_mean,
        "rejected_mean": sim.rejected_mean,
        "gap": sim.gap,
        "note": sim.note,
    }


@app.post("/retrain/rollback", tags=["Admin"], summary="Revert to the previous weight set")
async def retrain_rollback():
    previous = _get_retrainer().rollback()
    if engine and previous:
        engine.factor_weights = previous
    return {"weights": previous or {}, "status": "rolled_back" if previous else "no_history"}


@app.post("/embeddings/rebuild", tags=["Admin"], summary="Rebuild the dense embedding index (after professor data changes)")
async def embeddings_rebuild():
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)
    if not engine.embedding_engine:
        raise api_error(ErrorCode.EMBEDDINGS_UNAVAILABLE)
    engine.embedding_engine.build_professor_index(engine.professors, show_progress=False)
    index_path = Path(__file__).parent.parent / "collabv_embeddings.index"
    try:
        engine.embedding_engine.save_index(str(index_path))
    except Exception as e:
        return {"status": "rebuilt", "indexed": len(engine.embedding_engine.prof_ids), "save_error": str(e)}
    return {"status": "rebuilt", "indexed": len(engine.embedding_engine.prof_ids)}


# ─── History ────────────────────────────────────────────────────────────────

@app.get("/history", tags=["Matching"], summary="Recent match history with aggregate stats")
async def get_history(limit: int = 20):
    return {"history": db.get_history(limit), "stats": db.get_stats()}


# ─── Auth ───────────────────────────────────────────────────────────────────

@app.post("/auth/register", tags=["Auth"], summary="Create a new account")
async def auth_register(payload: dict):
    from .auth import UserRegisterInput, create_user
    try:
        user = create_user(_resolved_db_path(), UserRegisterInput(**payload))
    except HTTPException:
        raise
    except Exception as e:
        # Discriminate between "duplicate email" (409) and validation (400)
        if "already" in str(e).lower():
            raise api_error(ErrorCode.EMAIL_ALREADY_REGISTERED)
        raise api_error(ErrorCode.INVALID_REQUEST, "Registration failed - check your inputs")
    return user.model_dump()


@app.post("/auth/login", tags=["Auth"], summary="Issue access + refresh tokens")
async def auth_login(payload: dict):
    from .auth import authenticate, issue_tokens, UserLoginInput
    try:
        creds = UserLoginInput(**payload)
    except Exception:
        raise api_error(ErrorCode.INVALID_REQUEST, "Email and password are required")
    user = authenticate(_resolved_db_path(), creds.email, creds.password)
    if not user:
        raise api_error(ErrorCode.INVALID_CREDENTIALS)
    tokens = issue_tokens(user)
    return {**tokens.model_dump(), "user": user.model_dump()}


@app.post(
    "/auth/api-key/generate",
    tags=["Auth"],
    summary="Rotate the caller's API key",
    dependencies=[Depends(require_auth_or_api_key)],
)
async def auth_generate_api_key(request: Request):
    import secrets, sqlite3
    user = require_auth_or_api_key(request)
    if not user:
        raise api_error(ErrorCode.AUTHENTICATION_REQUIRED)
    new_key = secrets.token_urlsafe(32)
    conn = sqlite3.connect(_resolved_db_path())
    try:
        conn.execute("UPDATE users SET api_key=? WHERE id=?", (new_key, user["id"]))
        conn.commit()
    finally:
        conn.close()
    return {"api_key": new_key}


@app.get(
    "/auth/me",
    tags=["Auth"],
    summary="Current user profile (resolved from API key or JWT)",
    dependencies=[Depends(require_auth_or_api_key)],
)
async def auth_me(request: Request):
    user = require_auth_or_api_key(request)
    if not user:
        raise api_error(ErrorCode.AUTHENTICATION_REQUIRED)
    return user


# ═══════════════════════════════════════════════════════════════════════════
# MARKETPLACE - Patent listings + buyer profiles + Mode A retrieval (Phase 1)
# ═══════════════════════════════════════════════════════════════════════════
# Constraints enforced here:
#   - Every listing status change goes through mdb.transition_listing.
#     api.py never calls mdb.update_listing_status directly. The unsafe
#     primitive is reserved for migrations/seeds only.
#   - Three exception codes from transition_listing map through api_error:
#       LISTING_NOT_FOUND               -> ErrorCode.LISTING_NOT_FOUND (404)
#       LISTING_NOT_ACTIVATABLE         -> ErrorCode.LISTING_NOT_ACTIVATABLE (400)
#       STUB_REQUIRES_ADMIN_ACTIVATION  -> ErrorCode.STUB_REQUIRES_ADMIN_ACTIVATION (403)

from . import marketplace_db as mdb     # noqa: E402

_marketplace_engine = None


class PatentListingInputAPI(BaseModel):
    """Inventor-facing payload to create a draft listing. Status is NEVER
    settable here - lifecycle goes through POST .../transition."""
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


class PatentListingPatchInput(BaseModel):
    """All optional - PATCH only updates fields you send."""
    title: Optional[str] = Field(None, max_length=500)
    abstract: Optional[str] = Field(None, max_length=10000)
    claims_text: Optional[str] = Field(None, max_length=200000)
    inventor_names: Optional[List[str]] = Field(None, max_length=20)
    licensing_terms: Optional[Dict[str, Any]] = None
    asking_price_inr: Optional[float] = Field(None, ge=0)
    domain_tags: Optional[List[str]] = Field(None, max_length=20)
    industry_tags: Optional[List[str]] = Field(None, max_length=20)


class TransitionInput(BaseModel):
    target_status: str = Field(..., max_length=32)
    reason: Optional[str] = Field(None, max_length=500)


class BuyerProfileInputAPI(BaseModel):
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


class CandidateBuyersInputAPI(BaseModel):
    top_k: int = Field(10, ge=1, le=50)
    include_synthetic: bool = Field(False,
        description="Admin/testing flag. Default False — synthetic buyers are "
                    "excluded from real inventor rankings.")
    include_explanations: bool = False
    explain_top_k: int = Field(5, ge=0, le=20)


class InquiryInputAPI(BaseModel):
    message: str = Field("", max_length=2000)


class ClaimProfessorInput(BaseModel):
    professor_id: str = Field(..., max_length=64)


# ─── Helpers ──────────────────────────────────────────────────────────────

def _get_marketplace_engine():
    global _marketplace_engine
    if _marketplace_engine is None:
        from .marketplace_engine import MarketplaceEngine
        _marketplace_engine = MarketplaceEngine(db_path=_resolved_db_path())
    return _marketplace_engine


def _current_user_or_anon(request: Request) -> Optional[Dict[str, Any]]:
    """Resolve auth without forcing it. Returns the user payload (dict from
    require_auth_or_api_key) or None for guests."""
    try:
        return require_auth_or_api_key(request)
    except HTTPException:
        return None


def _actor_role(user: Optional[Dict[str, Any]], listing: Dict[str, Any]) -> Optional[str]:
    """Return 'admin', 'inventor', or None for the given (user, listing) pair.

    'inventor' is granted only when the user has explicitly claimed the listing's
    professor profile (users.linked_professor_id == listing.professor_id).
    """
    if not user:
        return None
    role = (user.get("role") or "").lower()
    if role == "admin":
        return "admin"
    user_id = user.get("id")
    if not user_id:
        return None
    from .auth import get_user_link
    linked = get_user_link(_resolved_db_path(), user_id)
    if linked and linked == listing.get("professor_id"):
        return "inventor"
    return None


def _map_lifecycle_error(exc) -> HTTPException:
    """Map InvalidLifecycleTransition codes -> ErrorCode -> api_error."""
    code_map = {
        "LISTING_NOT_FOUND":               ErrorCode.LISTING_NOT_FOUND,
        "LISTING_NOT_ACTIVATABLE":         ErrorCode.LISTING_NOT_ACTIVATABLE,
        "STUB_REQUIRES_ADMIN_ACTIVATION":  ErrorCode.STUB_REQUIRES_ADMIN_ACTIVATION,
    }
    code = code_map.get(getattr(exc, "code", ""), ErrorCode.INVALID_REQUEST)
    return api_error(code, str(exc))


def _professor_lookup() -> Dict[str, Dict[str, Any]]:
    return professors_by_id_cache or {}


# ─── Listings: create / read / patch / transition ────────────────────────

@app.post(
    "/marketplace/listings",
    tags=["Marketplace"],
    summary="Create a draft patent listing (inventor-only)",
    description=(
        "Creates a listing in 'draft' state. Status is never accepted from "
        "the client. Activation goes through POST .../transition."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def create_listing(req: Request, payload: PatentListingInputAPI,
                         background_tasks: BackgroundTasks):
    user = require_auth_or_api_key(req)
    if not user:
        raise api_error(ErrorCode.AUTHENTICATION_REQUIRED)
    role = (user.get("role") or "").lower()
    if role not in ("admin", "professor_user"):
        raise api_error(ErrorCode.AUTHORIZATION_FAILED,
                        "Only professor_user or admin can create listings")
    from .auth import get_user_link
    linked = get_user_link(_resolved_db_path(), user.get("id") or "")
    if not linked and role != "admin":
        raise api_error(ErrorCode.AUTHORIZATION_FAILED,
                        "Claim a professor profile first "
                        "(POST /marketplace/inventor/claim)")
    professor_id = linked if linked else None
    if not professor_id and role == "admin":
        raise api_error(ErrorCode.MISSING_INPUT,
                        "Admin creating a listing must include professor_id (extension TBD)")

    listing_id = mdb.save_listing({
        "professor_id":     professor_id,
        "patent_number":    payload.patent_number,
        "title":            payload.title,
        "abstract":         payload.abstract,
        "claims_text":      payload.claims_text,
        "inventor_names":   payload.inventor_names,
        "granted_date":     payload.granted_date,
        "status":           mdb.LISTING_DRAFT,    # ← never trust client
        "licensing_terms":  payload.licensing_terms,
        "asking_price_inr": payload.asking_price_inr,
        "domain_tags":      payload.domain_tags,
        "industry_tags":    payload.industry_tags,
        "abstract_source":  "inventor" if payload.abstract else "pending_fetch",
    }, db_path=_resolved_db_path())

    # If the inventor didn't provide an abstract, kick off a NON-blocking
    # background fetch. The listing-create call returns immediately; the
    # abstract trickles in once Google Patents / IITM TTO responds.
    if not payload.abstract:
        background_tasks.add_task(_async_fetch_abstract_into_db,
                                  listing_id, _resolved_db_path())

    return {"listing_id": listing_id, "status": mdb.LISTING_DRAFT, "created": True,
            "abstract_fetch_enqueued": not bool(payload.abstract)}


async def _async_fetch_abstract_into_db(listing_id: str, db_path: str) -> None:
    """Background task: NO-OP today.

    Set as a no-op deliberately after probing both viable upstream sources:
      - ip.iitm.ac.in TTO pages don't contain abstract text (stub pages, only
        metadata + contact info)
      - Google Patents direct URLs return 404 for IITM-granted Indian patent
        numbers (coverage gap)
      - Google Patents search is JS-rendered and not statically scrapeable

    Path forward is inventor-paste at activation time (Phase 2). To keep the
    no-fetch state visible in DATA (not just in this log line), this task
    sets abstract_status='none' on the listing row, so:
      - The UI can render "no abstract yet" badges from the data
      - The inventor-paste flow flips none -> pasted
      - A future commercial-API integration flips none -> fetched
      - Operators can grep listings stuck on 'none' to drive backfill

    BackgroundTasks wiring is preserved so flipping the body to a real fetch
    is a one-line code change when path (1) or (2) lands.
    """
    try:
        import sqlite3 as _sq, time as _t
        conn = _sq.connect(db_path)
        try:
            # Only mark if not already 'pasted' or 'fetched' - don't
            # overwrite inventor-entered or upstream-fetched abstracts.
            conn.execute(
                """UPDATE patent_listings
                   SET abstract_status = COALESCE(NULLIF(abstract_status, ''), 'none'),
                       updated_at = ?
                   WHERE listing_id = ?
                     AND (abstract_status IS NULL
                          OR abstract_status NOT IN ('pasted', 'fetched'))""",
                (_t.time(), listing_id),
            )
            conn.commit()
        finally:
            conn.close()
        print(f"[abstract] {listing_id}: no upstream available, status='none' "
              "(inventor-paste flow pending - see _async_fetch_abstract_into_db docstring)")
    except Exception as e:
        print(f"[abstract] {listing_id}: status update crashed: {e}")


@app.get(
    "/marketplace/listings",
    tags=["Marketplace"],
    summary="Public browse — only ACTIVE listings, with optional filters",
    description=(
        "Open, unauthenticated. Returns only listings with status='active' "
        "(the consent boundary). Optional filters: domain (matches "
        "domain_tags), industry (matches industry_tags), q (substring match "
        "on title). Pagination via limit (max 100) + offset."
    ),
)
async def browse_listings(
    request: Request,
    domain: Optional[str] = None,
    industry: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    # Fetch the whole active set so filtering + count + pagination are accurate.
    # In-memory filter — fine while the active set is small. Move to SQL once
    # the active count grows past ~10k.
    rows = mdb.list_active_listings(
        limit=10000,
        offset=0,
        db_path=_resolved_db_path(),
    )
    def matches(l: Dict[str, Any]) -> bool:
        if domain and domain.lower() not in [t.lower() for t in (l.get("domain_tags") or [])]:
            return False
        if industry and industry.lower() not in [t.lower() for t in (l.get("industry_tags") or [])]:
            return False
        if q and q.lower() not in (l.get("title") or "").lower():
            return False
        return True
    filtered = [l for l in rows if matches(l)]
    page = filtered[offset:offset + limit]
    for l in page:
        prof = professors_by_id_cache.get(l.get("professor_id", ""), {})
        l["owner_name"] = prof.get("name") or l.get("professor_id")
        l["department"] = prof.get("department", "")
    return {
        "count": len(filtered),
        "limit": limit,
        "offset": offset,
        "has_more": len(filtered) > offset + limit,
        "listings": page,
    }


@app.get(
    "/marketplace/listings/{listing_id}",
    tags=["Marketplace"],
    summary="Fetch a listing (active is public; other states require owner-or-admin)",
)
async def get_listing(request: Request, listing_id: str):
    listing = mdb.get_listing(listing_id, db_path=_resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)
    # Public path: active listings visible to anyone (incl. guests, no auth)
    if listing.get("status") == mdb.LISTING_ACTIVE:
        return listing
    # Non-active path: must be owner or admin
    user = _current_user_or_anon(request)
    if not user:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)   # don't leak existence
    actor = _actor_role(user, listing)
    if actor not in ("admin", "inventor"):
        raise api_error(ErrorCode.LISTING_NOT_FOUND)   # also don't leak
    return listing


@app.patch(
    "/marketplace/listings/{listing_id}",
    tags=["Marketplace"],
    summary="Edit listing metadata (only allowed in 'draft' or 'paused' state)",
    dependencies=[Depends(require_auth_or_api_key)],
)
async def patch_listing(request: Request, listing_id: str, payload: PatentListingPatchInput):
    listing = mdb.get_listing(listing_id, db_path=_resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)
    user = require_auth_or_api_key(request)
    actor = _actor_role(user, listing)
    if actor not in ("admin", "inventor"):
        raise api_error(ErrorCode.NOT_LISTING_OWNER)

    # Reject edits on locked states
    locked = {mdb.LISTING_PENDING_APPROVAL, mdb.LISTING_SOLD, mdb.LISTING_WITHDRAWN}
    if listing.get("status") in locked and actor != "admin":
        raise api_error(ErrorCode.LISTING_INACTIVE,
                        f"Editing is disabled while listing is {listing.get('status')!r}")

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return {"listing_id": listing_id, "no_op": True}
    # Reuse save_listing (UPSERT) - merge updates onto current
    merged = {**listing, **updates}
    # Don't let the merge change status / lifecycle stamps
    merged["status"] = listing.get("status")
    merged["activated_at"] = listing.get("activated_at")
    merged["approved_at"] = listing.get("approved_at")
    merged["approved_by_user_id"] = listing.get("approved_by_user_id")
    # Inventor-paste flow: when the abstract field is included in the PATCH,
    # flip abstract_status to reflect provenance. Non-empty paste -> 'pasted';
    # explicit empty string clears it back to 'none'. We don't ever stomp
    # 'fetched' here — that's reserved for future upstream-fetch paths.
    if "abstract" in updates:
        new_abs = (updates.get("abstract") or "").strip()
        current_status = (listing.get("abstract_status") or "none").lower()
        if new_abs:
            merged["abstract_status"] = "pasted"
        elif current_status != "fetched":
            merged["abstract_status"] = "none"
    mdb.save_listing(merged, db_path=_resolved_db_path())
    return {"listing_id": listing_id, "updated_fields": sorted(updates.keys()),
            "abstract_status": merged.get("abstract_status")}


@app.post(
    "/marketplace/listings/{listing_id}/transition",
    tags=["Marketplace"],
    summary="Change listing status (single chokepoint; all gating happens here)",
    description=(
        "Transitions go through marketplace_db.transition_listing. State "
        "machine + stub gate are enforced server-side. Inventors can move "
        "draft <-> pending_approval; only admin/TTO stamps active. Stub-"
        "owned listings can ONLY be activated by admin."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def transition_listing_endpoint(request: Request, listing_id: str, payload: TransitionInput):
    listing = mdb.get_listing(listing_id, db_path=_resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)
    user = require_auth_or_api_key(request)
    actor = _actor_role(user, listing)
    if actor not in ("admin", "inventor"):
        raise api_error(ErrorCode.NOT_LISTING_OWNER)
    try:
        result = mdb.transition_listing(
            listing_id=listing_id,
            target_status=payload.target_status,
            actor_role=actor,
            actor_user_id=user.get("id"),
            professor_lookup=_professor_lookup(),
            db_path=_resolved_db_path(),
        )
    except mdb.InvalidLifecycleTransition as e:
        raise _map_lifecycle_error(e)
    # Refresh + PERSIST the patent embedding index whenever a listing crosses
    # into or out of the active set. Without save_indices() the upsert is
    # in-memory only and a server restart would lose every activation since
    # the last full /embeddings/rebuild. The index file on disk is the source
    # of truth that Mode B reads on cold boot.
    new_status = result.get("new_status")
    old_status = result.get("old_status")
    active_set_changed = (
        new_status == mdb.LISTING_ACTIVE or old_status == mdb.LISTING_ACTIVE
    )
    if active_set_changed:
        try:
            engine = _get_marketplace_engine()
            engine.index.upsert_listing(
                mdb.get_listing(listing_id, db_path=_resolved_db_path()),
                db_path=_resolved_db_path(),
            )
            # upsert_listing currently does a full rebuild internally for
            # 'active' upserts. For transitions OUT of active it returns early,
            # so we drive the rebuild here too.
            if new_status != mdb.LISTING_ACTIVE and old_status == mdb.LISTING_ACTIVE:
                engine.index.build_patent_index(db_path=_resolved_db_path())
            engine.index.save_indices()
        except Exception as exc:
            print(f"[marketplace] index refresh+persist after transition failed: {exc}")
    return result


@app.get(
    "/marketplace/inventor/listings",
    tags=["Marketplace"],
    summary="The caller's own listings (any state)",
    dependencies=[Depends(require_auth_or_api_key)],
)
async def my_listings(request: Request):
    user = require_auth_or_api_key(request)
    from .auth import get_user_link
    linked = get_user_link(_resolved_db_path(), user.get("id") or "")
    if not linked:
        # No active link yet — surface the claim state so the UI can
        # distinguish "you haven't applied" from "your application is queued"
        # from "your last claim was rejected".
        claim = mdb.latest_claim_for_user(user.get("id") or "",
                                          db_path=_resolved_db_path())
        return {
            "linked_professor_id": None,
            "listings": [],
            "claim_state": (claim["status"] if claim else "none"),
            "claim": claim,
            "note": _claim_state_note(claim),
        }
    rows = mdb.list_listings_for_professor(linked, db_path=_resolved_db_path())
    # Enrich with owner profile_type so the UI can suppress the activate action
    # on patent_stub listings (those are admin-only by design; a real inventor
    # would 403 at transition time). A correctly-claimed faculty profile will
    # always report 'faculty' here; 'patent_stub' should be unreachable through
    # the legitimate claim flow but we surface the field defensively.
    owner = (professors_by_id_cache or {}).get(linked) or {}
    profile_type = owner.get("profile_type") or "faculty"
    for r in rows:
        r["owner_profile_type"] = profile_type
    return {"linked_professor_id": linked, "owner_profile_type": profile_type,
            "listings": rows, "count": len(rows)}


@app.get(
    "/marketplace/admin/pending-listings",
    tags=["Marketplace"],
    summary="Pending-approval queue (admin/TTO only)",
    description=(
        "Lists every listing currently in pending_approval state, in the "
        "order it was submitted. The approval action is POST "
        ".../transition with target_status='active'. Stub-owned listings "
        "appear here too — they remain admin-only to activate regardless."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def admin_pending_listings(request: Request):
    user = require_auth_or_api_key(request)
    if (user.get("role") or "").lower() != "admin":
        raise api_error(ErrorCode.AUTHORIZATION_FAILED, "Admin only")
    import sqlite3 as _sq
    conn = _sq.connect(_resolved_db_path())
    conn.row_factory = _sq.Row
    try:
        rows = conn.execute(
            """SELECT * FROM patent_listings
               WHERE status = 'pending_approval'
               ORDER BY updated_at DESC""",
        ).fetchall()
    finally:
        conn.close()
    out = []
    lookup = professors_by_id_cache or {}
    for r in rows:
        d = mdb._row_to_listing(r)
        prof = lookup.get(d.get("professor_id") or "") or {}
        d["owner_profile_type"] = prof.get("profile_type") or "faculty"
        d["owner_name"] = prof.get("name") or prof.get("professor_id")
        out.append(d)
    return {"listings": out, "count": len(out)}


def _engine_unavailable_response(mode: str) -> Optional[Dict[str, Any]]:
    """Return a structured engine_unavailable response when the dense engine
    isn't ready, else None. Callers should short-circuit on a non-None result
    BEFORE invoking the recommender — otherwise the engine returns empty
    candidates that are visually identical to 'no matches found'.

    Operators see the root cause via /marketplace/status and the loud startup
    log line ([EMBEDDINGS DEGRADED] ...).
    """
    try:
        eng = _get_marketplace_engine()
    except Exception as exc:
        return {
            "status": "engine_unavailable",
            "mode": mode,
            "candidates": [],
            "message": f"Marketplace engine failed to initialize: {exc}",
            "operator_hint": "Check /marketplace/status and server logs.",
        }
    if eng.index is None or eng.index.embeddings_degraded:
        s = eng.index.stats() if eng.index else {}
        return {
            "status": "engine_unavailable",
            "mode": mode,
            "candidates": [],
            "message": (
                "Dense embedding engine is degraded — recommendations and "
                "matching aren't available right now. This is NOT 'no results'; "
                "the model couldn't load."
            ),
            "operator_hint": "Check /marketplace/status for load_error details.",
            "engine_status": s,
        }
    return None


def _claim_state_note(claim: Optional[Dict[str, Any]]) -> str:
    """Human-readable hint paired with claim_state for the inventor dashboard."""
    if not claim:
        return ("No faculty profile claimed yet. Submit a claim with your "
                "professor_id; an admin/TTO will verify and approve before you "
                "can see or activate your listings.")
    s = claim["status"]
    if s == mdb.CLAIM_PENDING:
        return (f"Your claim on {claim['requested_professor_id']} is awaiting "
                "admin verification. You'll see your listings once approved.")
    if s == mdb.CLAIM_REJECTED:
        return (f"Your last claim on {claim['requested_professor_id']} was "
                "rejected. Contact the TTO if you believe this was in error, "
                "or submit a new claim for the correct profile.")
    if s == mdb.CLAIM_APPROVED:
        # Approved-but-not-yet-linked means an admin click-path bug — they
        # approved without setting linked_professor_id. Surface, don't hide.
        return ("Your claim was approved but the link isn't set. Contact admin.")
    return ""


@app.post(
    "/marketplace/inventor/claim",
    tags=["Marketplace"],
    summary="Request to claim a faculty professor profile (requires admin approval)",
    description=(
        "Creates a PENDING claim request. Does NOT immediately link the user "
        "to the requested professor_id — an admin must review the claim via "
        "/marketplace/admin/claim-requests and approve it. Until then, the "
        "inventor sees a 'pending verification' state on their dashboard and "
        "cannot see/activate any listings. Idempotent: re-submitting the same "
        "request returns the existing pending/approved row rather than "
        "creating a duplicate.\n\n"
        "TODO(scale): admin approval is the manual interim mechanism. For "
        "automated scale, replace with email-domain match against the "
        "professor's contact record, a one-time verification email to the "
        "on-file address, or an Institute SSO assertion."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def inventor_claim(request: Request, payload: ClaimProfessorInput):
    user = require_auth_or_api_key(request)
    role = (user.get("role") or "").lower()
    if role not in ("admin", "professor_user"):
        raise api_error(ErrorCode.AUTHORIZATION_FAILED,
                        "Only professor_user or admin can claim a faculty profile")
    if payload.professor_id not in (professors_by_id_cache or {}):
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND)
    claim = mdb.create_claim(
        user_id=user.get("id") or "",
        requested_professor_id=payload.professor_id,
        db_path=_resolved_db_path(),
    )
    return {"claim_id": claim["claim_id"], "status": claim["status"],
            "requested_professor_id": claim["requested_professor_id"]}


# ─── Admin: claim-request queue ──────────────────────────────────────────

@app.get(
    "/marketplace/admin/claim-requests",
    tags=["Marketplace"],
    summary="List pending professor-profile claim requests (admin/TTO only)",
    dependencies=[Depends(require_auth_or_api_key)],
)
async def list_claim_requests(request: Request):
    user = require_auth_or_api_key(request)
    if (user.get("role") or "").lower() != "admin":
        raise api_error(ErrorCode.AUTHORIZATION_FAILED, "Admin only")
    pending = mdb.list_pending_claims(db_path=_resolved_db_path())
    # Enrich each row with the requesting user's email + name and the requested
    # professor's name so the queue is reviewable without extra round-trips.
    import sqlite3 as _sq
    conn = _sq.connect(_resolved_db_path())
    conn.row_factory = _sq.Row
    user_lookup = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, email, name, role, created_at FROM users").fetchall()}
    conn.close()
    lookup = professors_by_id_cache or {}
    out = []
    for c in pending:
        u = user_lookup.get(c["user_id"]) or {}
        prof = lookup.get(c["requested_professor_id"]) or {}
        c["requester_email"] = u.get("email")
        c["requester_name"] = u.get("name")
        c["requested_professor_name"] = prof.get("name") or c["requested_professor_id"]
        c["requested_professor_dept"] = prof.get("department")
        c["requested_profile_type"]  = prof.get("profile_type") or "faculty"
        out.append(c)
    return {"claims": out, "count": len(out)}


class ReviewClaimInput(BaseModel):
    approve: bool
    review_note: Optional[str] = Field(None, max_length=500)


@app.post(
    "/marketplace/admin/claim-requests/{claim_id}/review",
    tags=["Marketplace"],
    summary="Approve or reject a professor-profile claim request (admin/TTO only)",
    description=(
        "On approve: flips the claim to 'approved' AND sets the requesting "
        "user's users.linked_professor_id. On reject: flips to 'rejected'; "
        "no link is set. Either way the inventor's dashboard reflects the "
        "new state on next load. Cannot re-review a claim that's already "
        "approved or rejected (returns 400 from the underlying helper)."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def review_claim_request(request: Request, claim_id: str, payload: ReviewClaimInput):
    user = require_auth_or_api_key(request)
    if (user.get("role") or "").lower() != "admin":
        raise api_error(ErrorCode.AUTHORIZATION_FAILED, "Admin only")
    try:
        result = mdb.review_claim(
            claim_id, approve=payload.approve,
            reviewer_user_id=user.get("id") or "",
            review_note=payload.review_note,
            db_path=_resolved_db_path(),
        )
    except ValueError as e:
        raise api_error(ErrorCode.INVALID_REQUEST, str(e))
    if payload.approve:
        # Approval is the ONLY path that sets linked_professor_id. Inventor's
        # self-claim never does it directly anymore.
        from .auth import link_user_to_professor
        ok = link_user_to_professor(_resolved_db_path(),
                                    result["user_id"],
                                    result["requested_professor_id"])
        if not ok:
            # Approved row is in place, but the user disappeared. Edge case —
            # surface honestly rather than silently leaving an approved-but-
            # unlinked claim.
            raise api_error(ErrorCode.AUTHENTICATION_REQUIRED,
                            "Approved, but requesting user row not found")
    return {"claim_id": claim_id, "status": result["status"],
            "linked_professor_id": (
                result["requested_professor_id"] if payload.approve else None
            )}


# ─── Buyer profiles ───────────────────────────────────────────────────────

@app.post(
    "/marketplace/buyers",
    tags=["Marketplace"],
    summary="Create or replace the caller's buyer profile",
    dependencies=[Depends(require_auth_or_api_key)],
)
async def create_buyer(request: Request, payload: BuyerProfileInputAPI):
    user = require_auth_or_api_key(request)
    user_id = user.get("id")
    existing = mdb.get_buyer_by_user(user_id, db_path=_resolved_db_path())
    bid = mdb.save_buyer({
        "buyer_id": existing["buyer_id"] if existing else None,
        "user_id": user_id,
        **payload.model_dump(),
        "is_synthetic": False,
    }, db_path=_resolved_db_path())
    return {"buyer_id": bid, "created": not existing}


@app.get(
    "/marketplace/buyers/me",
    tags=["Marketplace"],
    summary="The caller's buyer profile",
    dependencies=[Depends(require_auth_or_api_key)],
)
async def get_my_buyer(request: Request):
    user = require_auth_or_api_key(request)
    buyer = mdb.get_buyer_by_user(user.get("id"), db_path=_resolved_db_path())
    if not buyer:
        raise api_error(ErrorCode.BUYER_NOT_FOUND)
    return buyer


# ─── Mode A: candidate buyers for a patent ───────────────────────────────

@app.post(
    "/marketplace/listings/{listing_id}/candidate-buyers",
    tags=["Marketplace"],
    summary="Mode A - rank candidate buyers for an inventor-owned listing",
    description=(
        "Returns ranked buyers. Students are always excluded. Synthetic "
        "buyers are excluded by default; pass include_synthetic=true to "
        "include them (admin/testing only - real users can't see synthetics)."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def candidate_buyers(request: Request, listing_id: str, payload: CandidateBuyersInputAPI):
    listing = mdb.get_listing(listing_id, db_path=_resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)
    user = require_auth_or_api_key(request)
    actor = _actor_role(user, listing)
    if actor not in ("admin", "inventor"):
        raise api_error(ErrorCode.NOT_LISTING_OWNER)

    # Engine-down short-circuit: return engine_unavailable (NOT empty candidates)
    # so the UI can render "engine is down" instead of "no buyers match you".
    unavailable = _engine_unavailable_response("buyers_for_patent")
    if unavailable is not None:
        return {**unavailable, "subject_id": listing_id}

    # Enforce: only admins may set include_synthetic=True
    role = (user.get("role") or "").lower()
    include_synth = bool(payload.include_synthetic) and role == "admin"

    # Build a user_lookup so the rules layer can identify students
    import sqlite3 as _sq
    conn = _sq.connect(_resolved_db_path())
    conn.row_factory = _sq.Row
    user_lookup = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, role, company_name FROM users").fetchall()}
    conn.close()

    result = _get_marketplace_engine().recommend_buyers_for_patent(
        listing_id=listing_id, top_k=payload.top_k,
        exclude_students=True,
        include_synthetic=include_synth,
        include_explanations=payload.include_explanations,
        explain_top_k=payload.explain_top_k,
        user_lookup=user_lookup,
        professor_lookup=_professor_lookup(),
    )
    response = result.to_dict()
    # Explicit no-eligible-buyers signal so the frontend can render a meaningful state
    if not response.get("candidates"):
        response["status"] = "no_eligible_buyers"
        n_total = mdb.list_buyers(include_synthetic=True, db_path=_resolved_db_path())
        n_synth = sum(1 for b in n_total if b.get("is_synthetic"))
        response["message"] = (
            f"No eligible buyers found. The buyer population currently "
            f"contains {len(n_total)} profiles ({n_synth} are synthetic and "
            f"excluded by default). Admins can set include_synthetic=true "
            f"to exercise Mode A against the synthetic population for testing."
        )
        response["include_synthetic_available_to_role"] = (role == "admin")
    else:
        response["status"] = "ok"
    return response


# ─── Inquiries ───────────────────────────────────────────────────────────

@app.post(
    "/marketplace/listings/{listing_id}/inquiry",
    tags=["Marketplace"],
    summary="Buyer (or student) self-initiated buy-interest on an active listing",
    description=(
        "Inquiries are only accepted against listings whose status is "
        "'active'. Inquiries against any other state are rejected, so the "
        "consent model can't leak."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def create_inquiry(request: Request, listing_id: str, payload: InquiryInputAPI):
    listing = mdb.get_listing(listing_id, db_path=_resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)
    # Hard rule: only ACTIVE listings accept inquiries
    if listing.get("status") != mdb.LISTING_ACTIVE:
        raise api_error(ErrorCode.LISTING_INACTIVE,
                        f"This listing is not accepting inquiries (status={listing.get('status')!r}).")
    user = require_auth_or_api_key(request)
    user_id = user.get("id")
    buyer = mdb.get_buyer_by_user(user_id, db_path=_resolved_db_path())
    inquiry_id = mdb.save_inquiry({
        "listing_id": listing_id,
        "buyer_id":   buyer["buyer_id"] if buyer else None,
        "user_id":    user_id,
        "message":    payload.message,
        "status":     "new",
    }, db_path=_resolved_db_path())
    return {"inquiry_id": inquiry_id, "status": "new"}


# ─── Mode B: recommend patents for a buyer ───────────────────────────────

class RecommendPatentsInput(BaseModel):
    top_k: int = Field(20, ge=1, le=50)
    include_explanations: bool = False
    explain_top_k: int = Field(5, ge=0, le=20)


@app.post(
    "/marketplace/buyer/recommendations",
    tags=["Marketplace"],
    summary="Mode B — rank candidate ACTIVE patents for the logged-in buyer",
    description=(
        "Uses the symmetric retrieve→rules→rerank pipeline. Only listings "
        "in status='active' are eligible (the consent boundary). The buyer "
        "is resolved from the caller's user_id (buyer_profiles.user_id), so "
        "this is a 'recommendations for me' endpoint — there is no buyer_id "
        "path parameter. Returns the same MarketplaceMatchResult shape as "
        "Mode A."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def recommend_patents(request: Request, payload: RecommendPatentsInput):
    user = require_auth_or_api_key(request)
    user_id = user.get("id") or ""
    buyer = mdb.get_buyer_by_user(user_id, db_path=_resolved_db_path())
    if not buyer:
        raise api_error(
            ErrorCode.BUYER_NOT_FOUND,
            "Create a buyer profile first via POST /marketplace/buyers.",
        )
    # Same engine-down short-circuit as Mode A. Without this, the buyer sees
    # "No active patents in the marketplace yet" — which is the message we use
    # when there genuinely are zero active listings, an ambiguous signal.
    unavailable = _engine_unavailable_response("patents_for_buyer")
    if unavailable is not None:
        return {**unavailable, "subject_id": buyer["buyer_id"]}
    result = _get_marketplace_engine().recommend_patents_for_buyer(
        buyer_id=buyer["buyer_id"],
        top_k=payload.top_k,
        include_explanations=payload.include_explanations,
        explain_top_k=payload.explain_top_k,
    )
    response = result.to_dict()
    if not response.get("candidates"):
        response["status"] = "no_active_patents"
        n_active = len(mdb.list_active_listings(db_path=_resolved_db_path()))
        if n_active == 0:
            response["message"] = (
                "No active patents in the marketplace yet. Listings become "
                "visible here only after an inventor submits one and admin "
                "approves it."
            )
        else:
            response["message"] = (
                f"None of the {n_active} active listings passed the rules "
                "filter for your profile. Refine your buyer profile or try "
                "again as the inventory grows."
            )
    else:
        response["status"] = "ok"
    return response


# ─── Inquiry inbox + thread ──────────────────────────────────────────────

@app.get(
    "/marketplace/inbox",
    tags=["Marketplace"],
    summary="Combined inbox: inquiries sent (buyer side) + received (inventor side)",
    description=(
        "Returns two arrays:\n"
        "  - sent: inquiries the caller submitted (buyer/student side)\n"
        "  - received: inquiries on listings the caller owns "
        "(inventor side; empty if the caller hasn't claimed a faculty profile)\n"
        "Each row is enriched with the listing's title + the requester's "
        "email so the UI doesn't need follow-up round-trips."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def get_inbox(request: Request):
    user = require_auth_or_api_key(request)
    user_id = user.get("id") or ""
    from .auth import get_user_link
    linked_prof = get_user_link(_resolved_db_path(), user_id)

    sent = mdb.list_inquiries_for_user(user_id, db_path=_resolved_db_path())
    if linked_prof:
        my_listings = mdb.list_listings_for_professor(
            linked_prof, db_path=_resolved_db_path(),
        )
        my_listing_ids = [l["listing_id"] for l in my_listings]
        received = mdb.list_inquiries_for_listings(
            my_listing_ids, db_path=_resolved_db_path(),
        )
    else:
        received = []

    # Enrich: title for each listing referenced, requester email for received
    import sqlite3 as _sq
    conn = _sq.connect(_resolved_db_path())
    conn.row_factory = _sq.Row
    referenced_lids = {i["listing_id"] for i in sent} | {i["listing_id"] for i in received}
    title_lookup: Dict[str, str] = {}
    if referenced_lids:
        placeholders = ",".join("?" for _ in referenced_lids)
        for r in conn.execute(
            f"SELECT listing_id, title FROM patent_listings WHERE listing_id IN ({placeholders})",
            list(referenced_lids),
        ).fetchall():
            title_lookup[r["listing_id"]] = r["title"]
    user_email_lookup: Dict[str, str] = {}
    user_ids_for_received = {i["user_id"] for i in received if i.get("user_id")}
    if user_ids_for_received:
        placeholders = ",".join("?" for _ in user_ids_for_received)
        for r in conn.execute(
            f"SELECT id, email FROM users WHERE id IN ({placeholders})",
            list(user_ids_for_received),
        ).fetchall():
            user_email_lookup[r["id"]] = r["email"]
    conn.close()

    for i in sent:
        i["listing_title"] = title_lookup.get(i["listing_id"], i["listing_id"])
    for i in received:
        i["listing_title"] = title_lookup.get(i["listing_id"], i["listing_id"])
        i["requester_email"] = user_email_lookup.get(i.get("user_id") or "", "")
    return {
        "sent": sent, "received": received,
        "is_inventor": bool(linked_prof),
        "counts": {"sent": len(sent), "received": len(received)},
    }


class RespondInquiryInput(BaseModel):
    status: str = Field(..., description="acknowledged | accepted | declined")


@app.post(
    "/marketplace/inquiries/{inquiry_id}/respond",
    tags=["Marketplace"],
    summary="Inventor responds to an inquiry (acknowledged / accepted / declined)",
    description=(
        "Only the inventor of the underlying listing (or an admin) may respond. "
        "Status transitions: new -> acknowledged -> accepted | declined. "
        "We don't block backward transitions in v1 — the timeline is the "
        "source of truth, status is the current label."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def respond_inquiry(request: Request, inquiry_id: str, payload: RespondInquiryInput):
    user = require_auth_or_api_key(request)
    if payload.status not in ("acknowledged", "accepted", "declined"):
        raise api_error(ErrorCode.INVALID_REQUEST,
                        "status must be acknowledged | accepted | declined")
    inq = mdb.get_inquiry(inquiry_id, db_path=_resolved_db_path())
    if not inq:
        raise api_error(ErrorCode.INQUIRY_NOT_FOUND
                        if hasattr(ErrorCode, "INQUIRY_NOT_FOUND")
                        else ErrorCode.INVALID_REQUEST,
                        "Inquiry not found")
    listing = mdb.get_listing(inq["listing_id"], db_path=_resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)
    actor = _actor_role(user, listing)
    if actor not in ("admin", "inventor"):
        raise api_error(ErrorCode.AUTHORIZATION_FAILED,
                        "Only the inventor of this listing (or admin) can respond.")
    try:
        updated = mdb.update_inquiry_status(inquiry_id, payload.status,
                                            db_path=_resolved_db_path())
    except ValueError as e:
        raise api_error(ErrorCode.INVALID_REQUEST, str(e))
    return {"inquiry_id": inquiry_id, "status": updated["status"],
            "responded_at": updated["responded_at"]}


# ─── Admin: rebuild embedding indices ────────────────────────────────────

@app.post(
    "/marketplace/embeddings/rebuild",
    tags=["Marketplace"],
    summary="Rebuild both the buyer and the (active) patent embedding indices",
    description=(
        "Builds the BUYER index from all buyer_profiles (incl. synthetics) "
        "so Mode A has a population to query, AND the patent index from "
        "all ACTIVE listings. Drafts are never embedded."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def rebuild_marketplace_embeddings(request: Request):
    user = require_auth_or_api_key(request)
    role = (user.get("role") or "").lower()
    if role != "admin":
        raise api_error(ErrorCode.AUTHORIZATION_FAILED, "Admin only")
    eng = _get_marketplace_engine()
    if not eng.index:
        raise api_error(ErrorCode.EMBEDDINGS_UNAVAILABLE,
                        "MarketplaceIndex not initialized")
    n_buyers = eng.index.build_buyer_index(db_path=_resolved_db_path(),
                                            include_synthetic=True)
    n_patents = eng.index.build_patent_index(db_path=_resolved_db_path())
    try:
        eng.index.save_indices()
    except Exception as e:
        return {"buyers_indexed": n_buyers, "patents_indexed": n_patents,
                "save_error": str(e)}
    return {"buyers_indexed": n_buyers, "patents_indexed": n_patents,
            "patents_skipped_drafts": True}


# ═══════════════════════════════════════════════════════════════════════════
# MATCHING ENGINE 2 — Professor → Company matching
# ═══════════════════════════════════════════════════════════════════════════

openapi_tags.append({
    "name": "ProfessorMatch",
    "description": (
        "Reverse matching: given a professor profile, rank all company project "
        "listings by fit. Uses Matching Engine 2 with 5-layer scoring: "
        "research domain (30%), technical skills (25%), AI/ML methods (20%), "
        "publications (15%), industry domain (10%)."
    ),
})

_prof_match_engine = None


def _get_prof_match_engine():
    global _prof_match_engine
    if _prof_match_engine is None:
        from .matching_engine_2 import ProfessorMatchEngine
        # Live-data-only: companies_file=None skips the retired
        # 100_Companies_Collaboration_Schema.xlsx seed sheet entirely - the
        # engine's own companies_file-and-Path.exists() guard already
        # degrades to zero seed companies gracefully. The .companies
        # property still unions this with live company_profiles rows, so
        # real registrations keep working immediately.
        _prof_match_engine = ProfessorMatchEngine(companies_file=None, db_path=_resolved_db_path())
    return _prof_match_engine


class ProfessorMatchRunInput(BaseModel):
    professor_id: str = Field(..., max_length=64)
    top_k: Optional[int] = Field(None, ge=1, le=200)


@app.post(
    "/professor-match/run",
    tags=["ProfessorMatch"],
    summary="Rank all company projects against a professor profile (Engine 2)",
    description=(
        "Accepts a professor_id, looks up the full profile from the loaded "
        "professor index, and scores every company project against it using "
        "Matching Engine 2. Returns ranked companies with score breakdowns, "
        "matched skills/techniques, reasons, and collaboration suggestions."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def run_professor_match(payload: ProfessorMatchRunInput):
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)
    prof = professors_by_id_cache.get(payload.professor_id)
    if not prof:
        raise api_error(ErrorCode.PROFESSOR_NOT_FOUND)
    try:
        from .matching_engine_2 import run_professor_match as _run
        resp = _run(prof, top_k=payload.top_k, db_path=_resolved_db_path())
        return resp.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Professor match failed: {e}")


@app.get(
    "/professor-match/companies",
    tags=["ProfessorMatch"],
    summary="List all company projects available for professor matching",
)
async def list_company_projects():
    try:
        eng = _get_prof_match_engine()
        companies = eng.companies
        return {
            "count": len(companies),
            "companies": [
                {
                    "company_id": c.company_id,
                    "company_name": c.company_name,
                    "industry_domain": c.industry_domain,
                    "sector": c.sector,
                    "technical_area": c.technical_area,
                    "location": c.location,
                }
                for c in companies
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not load companies: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# MATCHING ENGINE 3 — Patent → Problem Statement matching
# MATCHING ENGINE 4 — Problem Statement → Patent matching
# ═══════════════════════════════════════════════════════════════════════════

openapi_tags.append({
    "name": "PatentProblemMatch",
    "description": (
        "Patent Smart Matches: Engine 3 matches a professor's patent portfolio "
        "(from patent_scraper.py) against the research problem-statement "
        "compendium, for the Professor Dashboard. Engine 4 is the reverse - "
        "matches a problem statement against every scraped patent, for the "
        "Company Dashboard. Both reuse Model 3 (patent_scorer.PatentScorer) "
        "for scoring and persist their top-K results to patent_smart_matches "
        "so dashboards read a stable, pre-ranked list."
    ),
})


class Engine4RunInput(BaseModel):
    problem_statement_id: str = Field(..., max_length=32)
    top_k: int = Field(10, ge=1, le=50)


@app.get(
    "/problem-statements",
    tags=["PatentProblemMatch"],
    summary="List all live-submitted research problem statements",
)
async def list_problem_statements():
    from .patent_problem_db import get_problem_statements
    statements = get_problem_statements(_resolved_db_path())
    return {"count": len(statements), "problem_statements": statements}


@app.get(
    "/problem-statements/{problem_statement_id}",
    tags=["PatentProblemMatch"],
    summary="Fetch a single problem statement",
)
async def get_problem_statement_detail(problem_statement_id: str):
    from .patent_problem_db import get_problem_statement
    ps = get_problem_statement(problem_statement_id, _resolved_db_path())
    if not ps:
        raise api_error(ErrorCode.PROBLEM_STATEMENT_NOT_FOUND)
    return ps


class ProblemStatementInput(BaseModel):
    company_id: str = Field("", max_length=64)
    sector: str = Field("", max_length=200)
    title: str = Field(..., max_length=300)
    description: str = Field("", max_length=4000)
    problem_statement: str = Field("", max_length=4000)
    expected_outcomes: List[str] = Field(default_factory=list, max_length=15)


@app.post(
    "/problem-statements",
    tags=["PatentProblemMatch"],
    summary="Submit a real research problem statement (live-data-only: this is the only way one enters the system)",
)
async def create_problem_statement(payload: ProblemStatementInput):
    from .patent_problem_db import save_problem_statement
    problem_id = save_problem_statement(payload.model_dump(), _resolved_db_path())
    return {"id": problem_id, "saved": True}



@app.post(
    "/matching-engine-4/run",
    tags=["PatentProblemMatch"],
    summary="Rank patents against a problem statement (Engine 4)",
    description=(
        "Scores every scraped patent (across all professors) against the "
        "given problem statement using Matching Engine 4. Persists the "
        "top_k matches so the Company Dashboard's Patent Smart Matches "
        "section can read them."
    ),
    dependencies=[Depends(require_auth_or_api_key)],
)
async def run_matching_engine_4(payload: Engine4RunInput):
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)
    from .matching_engine_4 import run_problem_to_patent_match
    try:
        results = run_problem_to_patent_match(
            payload.problem_statement_id, engine.professors,
            top_k=payload.top_k, persist=True, db_path=_resolved_db_path(),
        )
    except ValueError:
        raise api_error(ErrorCode.PROBLEM_STATEMENT_NOT_FOUND)
    return {
        "problem_statement_id": payload.problem_statement_id,
        "count": len(results),
        "matches": results,
    }


@app.get(
    "/matching-engine-4/problem/{problem_statement_id}/matches",
    tags=["PatentProblemMatch"],
    summary="Company Dashboard: read persisted Patent Smart Matches",
)
async def get_engine_4_matches(problem_statement_id: str):
    from .patent_problem_db import get_matches_for_problem_statement
    matches = get_matches_for_problem_statement(problem_statement_id, _resolved_db_path())
    return {
        "problem_statement_id": problem_statement_id,
        "count": len(matches),
        "matches": matches,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MATCHING ENGINE 4 (enhanced) — Company Profile + Company Dashboard
# ═══════════════════════════════════════════════════════════════════════════

openapi_tags.append({
    "name": "CompanyDashboard",
    "description": (
        "Enhanced Engine 4: matches a company (registered profile and/or a "
        "selected problem statement, merged when both exist) against "
        "Professor Profiles (reuses Model 1's MatchingEngine.match) and "
        "Patents (existing Engine 4 scoring), so a company gets relevant "
        "recommendations even without ever posting a problem statement."
    ),
})


class CompanyProfileInput(BaseModel):
    company_id: str = Field(..., max_length=64)
    company_name: str = Field(..., max_length=200)
    description: str = Field("", max_length=3000)
    industry: str = Field("", max_length=200)
    business_domain: str = Field("", max_length=200)
    products_services: List[str] = Field(default_factory=list, max_length=30)
    technologies_used: List[str] = Field(default_factory=list, max_length=30)
    tech_stack: List[str] = Field(default_factory=list, max_length=30)
    research_interests: List[str] = Field(default_factory=list, max_length=30)
    business_objectives: str = Field("", max_length=2000)
    focus_areas: List[str] = Field(default_factory=list, max_length=30)
    keywords: List[str] = Field(default_factory=list, max_length=30)
    market_segment: str = Field("", max_length=200)
    innovation_challenges: str = Field("", max_length=2000)
    strategic_goals: str = Field("", max_length=2000)
    existing_projects: List[str] = Field(default_factory=list, max_length=30)
    preferred_collaboration_areas: List[str] = Field(default_factory=list, max_length=10)
    company_size: str = Field("", max_length=50)
    category: str = Field("", max_length=50)


@app.post("/marketplace/company-profile", tags=["CompanyDashboard"], summary="Create/update a company profile")
async def upsert_company_profile(payload: CompanyProfileInput):
    from .patent_marketplace_db import save_company_profile
    save_company_profile(payload.company_id, payload.model_dump(exclude={"company_id"}), _resolved_db_path())
    return {"company_id": payload.company_id, "saved": True}


@app.get("/marketplace/company-profile/{company_id}", tags=["CompanyDashboard"], summary="Fetch a company profile")
async def get_company_profile_detail(company_id: str):
    from .patent_marketplace_db import get_company_profile
    profile = get_company_profile(company_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.COMPANY_NOT_FOUND)
    return profile


@app.get("/marketplace/company-profiles", tags=["CompanyDashboard"], summary="Browse company profiles")
async def browse_company_profiles():
    from .patent_marketplace_db import list_company_profiles
    profiles = list_company_profiles(_resolved_db_path())
    return {"count": len(profiles), "profiles": profiles}


class CompanyRecommendationsInput(BaseModel):
    problem_statement_id: Optional[str] = Field(None, max_length=32)
    top_k_professors: int = Field(5, ge=1, le=20)
    patents_per_professor: int = Field(3, ge=0, le=10)


@app.post(
    "/company-dashboard/{company_id}/recommendations",
    tags=["CompanyDashboard"],
    summary="Recommended Professors (with nested matching patents) for a company",
    description=(
        "Builds a combined query from the company's registered profile "
        "and/or the given problem_statement_id (merged when both exist - "
        "preferred; either alone still works). Returns recommended_professors "
        "(Model 1, enriched with confidence/collaboration-type); each "
        "professor's own patents are re-scored against the request and "
        "nested under them as matching_patents, so a professor and their "
        "patents never appear as duplicate, disconnected entries."
    ),
)
async def get_company_recommendations(company_id: str, payload: CompanyRecommendationsInput):
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)
    from .matching_engine_4 import build_combined_request, match_company_to_professors
    from .patent_marketplace_db import get_company_profile

    try:
        request = build_combined_request(
            company_id=company_id, problem_statement_id=payload.problem_statement_id,
            db_path=_resolved_db_path(),
        )
    except ValueError as e:
        raise api_error(ErrorCode.MISSING_INPUT, str(e))

    profile = get_company_profile(company_id, _resolved_db_path())
    company_name = profile.get("company_name") if profile else company_id

    recommended_professors = match_company_to_professors(
        engine, request, company_id, company_name, professors_by_id_cache,
        top_k=payload.top_k_professors,
        patents_per_professor=payload.patents_per_professor,
    )

    return {
        "company_id": company_id,
        "problem_statement_id": payload.problem_statement_id,
        "used_profile": profile is not None,
        "used_problem_statement": payload.problem_statement_id is not None,
        "recommended_professors": recommended_professors,
    }


class LogProfessorInteractionInput(BaseModel):
    professor_id: str = Field(..., max_length=64)
    interaction_type: str = Field(..., max_length=32)
    match_score: Optional[float] = None


@app.post(
    "/company-dashboard/{company_id}/professor-interactions",
    tags=["CompanyDashboard"],
    summary="Log a company<->professor interaction (view/connect/invite/etc.)",
)
async def log_company_professor_interaction(company_id: str, payload: LogProfessorInteractionInput):
    from .patent_marketplace_db import log_professor_interaction
    log_professor_interaction(
        company_id, payload.professor_id, payload.interaction_type,
        payload.match_score, _resolved_db_path(),
    )
    return {"logged": True}


# ═══════════════════════════════════════════════════════════════════════════
# MATCHING ENGINE 5 — Patent Marketplace: audience matching + offers
# ═══════════════════════════════════════════════════════════════════════════

openapi_tags.append({
    "name": "PatentMarketplace",
    "description": (
        "Professor Dashboard 'sell your patent' feature. Engine 5 ranks a "
        "single patent against any of five registered audience types "
        "(company, student, employee, professor, institute); professors can "
        "then send a direct offer to a specific candidate, and the "
        "candidate can view/respond to offers addressed to them."
    ),
})


def _resolve_patent(patent_id: str):
    """Look up a scraped patent + its owning professor from a Matching-Engine-3
    style id ('{professor_id}:{patent_number_or_hash}')."""
    if not patent_id or ":" not in patent_id:
        return None, None
    professor_id = patent_id.split(":", 1)[0]
    prof = professors_by_id_cache.get(professor_id)
    if not prof:
        return None, None
    from .matching_engine_5 import patent_id as compute_patent_id
    for patent in (prof.get("patents") or []):
        if compute_patent_id(patent, professor_id) == patent_id:
            return patent, prof
    return None, None


def _audience_candidates(target_type: str) -> List[Dict[str, Any]]:
    if target_type == "company":
        # Companies = registered buyer_profiles + the research problem-
        # statement compendium (each statement represents a generic company
        # need) - merged into one ranked pool per Matching Engine 3+5 combine.
        from .marketplace_db import list_buyers
        from .patent_problem_db import get_problem_statements
        buyers = list_buyers(db_path=_resolved_db_path())
        problem_statements = get_problem_statements(_resolved_db_path())
        return [*buyers, *problem_statements]
    if target_type == "professor":
        return engine.professors if engine else []
    if target_type == "student":
        from .patent_marketplace_db import list_student_profiles
        return list_student_profiles(_resolved_db_path())
    if target_type == "employee":
        from .patent_marketplace_db import list_employee_profiles
        return list_employee_profiles(_resolved_db_path())
    if target_type == "institute":
        from .patent_marketplace_db import list_institute_profiles
        return list_institute_profiles(_resolved_db_path())
    raise api_error(ErrorCode.INVALID_TARGET_TYPE)


class ProfileInput(BaseModel):
    user_id: str = Field(..., max_length=64)


class StudentProfileInput(ProfileInput):
    name: str = Field(..., max_length=200)
    institute: str = Field("", max_length=200)
    field_of_study: str = Field("", max_length=200)
    skills: List[str] = Field(default_factory=list, max_length=30)
    interests: List[str] = Field(default_factory=list, max_length=30)
    research_areas: List[str] = Field(default_factory=list, max_length=30)
    bio: str = Field("", max_length=2000)
    education: List[str] = Field(default_factory=list, max_length=15)
    projects: List[str] = Field(default_factory=list, max_length=15)
    publications: List[str] = Field(default_factory=list, max_length=15)
    certifications: List[str] = Field(default_factory=list, max_length=15)
    internships: List[str] = Field(default_factory=list, max_length=15)
    work_experience: List[str] = Field(default_factory=list, max_length=15)
    startup_interests: List[str] = Field(default_factory=list, max_length=10)
    career_goals: str = Field("", max_length=1000)
    preferred_domains: List[str] = Field(default_factory=list, max_length=15)
    achievements_soft_skills: List[str] = Field(default_factory=list, max_length=15)
    resume_filename: str = Field("", max_length=255)
    resume_text: str = Field("", max_length=20000)
    resume_file_path: str = Field("", max_length=500)


class EmployeeProfileInput(ProfileInput):
    name: str = Field(..., max_length=200)
    company_name: str = Field("", max_length=200)
    job_title: str = Field("", max_length=200)
    industry: str = Field("", max_length=200)
    skills: List[str] = Field(default_factory=list, max_length=30)
    interests: List[str] = Field(default_factory=list, max_length=30)
    bio: str = Field("", max_length=2000)
    education: List[str] = Field(default_factory=list, max_length=15)
    projects: List[str] = Field(default_factory=list, max_length=15)
    publications: List[str] = Field(default_factory=list, max_length=15)
    certifications: List[str] = Field(default_factory=list, max_length=15)
    internships: List[str] = Field(default_factory=list, max_length=15)
    work_experience: List[str] = Field(default_factory=list, max_length=15)
    industry_expertise: List[str] = Field(default_factory=list, max_length=15)
    innovation_interests: List[str] = Field(default_factory=list, max_length=15)
    startup_interests: List[str] = Field(default_factory=list, max_length=10)
    career_goals: str = Field("", max_length=1000)
    preferred_domains: List[str] = Field(default_factory=list, max_length=15)
    achievements_soft_skills: List[str] = Field(default_factory=list, max_length=15)
    resume_filename: str = Field("", max_length=255)
    resume_text: str = Field("", max_length=20000)
    resume_file_path: str = Field("", max_length=500)


class InstituteProfileInput(ProfileInput):
    institute_name: str = Field(..., max_length=200)
    focus_areas: List[str] = Field(default_factory=list, max_length=30)
    departments: List[str] = Field(default_factory=list, max_length=30)
    collaboration_types: List[str] = Field(default_factory=list, max_length=10)
    bio: str = Field("", max_length=2000)


@app.post("/marketplace/profiles/student", tags=["PatentMarketplace"], summary="Create/update a student profile")
async def upsert_student_profile(payload: StudentProfileInput):
    from .patent_marketplace_db import save_student_profile
    save_student_profile(payload.user_id, payload.model_dump(exclude={"user_id"}), _resolved_db_path())
    return {"user_id": payload.user_id, "saved": True}


@app.get("/marketplace/profiles/students", tags=["PatentMarketplace"], summary="Browse student profiles")
async def browse_student_profiles():
    from .patent_marketplace_db import list_student_profiles
    profiles = list_student_profiles(_resolved_db_path())
    return {"count": len(profiles), "profiles": profiles}


@app.get("/marketplace/profiles/student/{user_id}", tags=["PatentMarketplace"], summary="Fetch one student profile")
async def get_student_profile_detail(user_id: str):
    from .patent_marketplace_db import get_student_profile
    profile = get_student_profile(user_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.BUYER_NOT_FOUND, "No student profile found for this id")
    return profile


@app.get(
    "/marketplace/profiles/student/{user_id}/resume/download",
    tags=["PatentMarketplace"],
    summary="Download/preview a student's uploaded resume file (Matching Engine 8: professor candidate view)",
)
async def download_student_resume(user_id: str):
    from .patent_marketplace_db import get_student_profile
    from . import resume_storage

    profile = get_student_profile(user_id, _resolved_db_path())
    if not profile or not profile.get("resume_file_path"):
        raise api_error(ErrorCode.RESUME_NOT_FOUND)

    path = resume_storage.resolve_resume_path(profile["resume_file_path"])
    if not path:
        raise api_error(ErrorCode.RESUME_NOT_FOUND)

    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    download_name = profile.get("resume_filename") or f"resume{path.suffix}"
    return FileResponse(path, filename=download_name, media_type=media_type)


@app.post("/marketplace/profiles/employee", tags=["PatentMarketplace"], summary="Create/update an employee profile")
async def upsert_employee_profile(payload: EmployeeProfileInput):
    from .patent_marketplace_db import save_employee_profile
    save_employee_profile(payload.user_id, payload.model_dump(exclude={"user_id"}), _resolved_db_path())
    return {"user_id": payload.user_id, "saved": True}


@app.get("/marketplace/profiles/employees", tags=["PatentMarketplace"], summary="Browse employee profiles")
async def browse_employee_profiles():
    from .patent_marketplace_db import list_employee_profiles
    profiles = list_employee_profiles(_resolved_db_path())
    return {"count": len(profiles), "profiles": profiles}


@app.post("/marketplace/profiles/institute", tags=["PatentMarketplace"], summary="Create/update an institute profile")
async def upsert_institute_profile(payload: InstituteProfileInput):
    from .patent_marketplace_db import save_institute_profile
    save_institute_profile(payload.user_id, payload.model_dump(exclude={"user_id"}), _resolved_db_path())
    return {"user_id": payload.user_id, "saved": True}


@app.get("/marketplace/profiles/institutes", tags=["PatentMarketplace"], summary="Browse institute profiles")
async def browse_institute_profiles():
    from .patent_marketplace_db import list_institute_profiles
    profiles = list_institute_profiles(_resolved_db_path())
    return {"count": len(profiles), "profiles": profiles}


@app.get(
    "/marketplace/audience/{target_type}",
    tags=["PatentMarketplace"],
    summary="Browse candidates of one audience type (company/student/employee/professor/institute)",
)
async def browse_audience(target_type: str):
    candidates = _audience_candidates(target_type)
    return {"target_type": target_type, "count": len(candidates), "candidates": candidates}


class MatchRequest(BaseModel):
    source_kind: str = Field(..., max_length=32)
    source_id: str = Field(..., max_length=64)
    target_kind: str = Field(..., max_length=32)
    top_k: int = Field(10, ge=1, le=100)
    domain: Optional[str] = Field(None, max_length=64)
    industry: Optional[str] = Field(None, max_length=64)
    max_price: Optional[float] = Field(None, ge=0)


@app.post(
    "/match",
    tags=["Matching"],
    summary="Unified matching engine - dispatches by source_kind/target_kind",
    description=(
        "Single entry point for every ranking direction the platform needs:\n\n"
        "- target_kind='audience': source_kind must be 'patent'; returns all 5 "
        "audience categories (company/professor/student/employee/institute) at once.\n"
        "- target_kind in ('company','professor','student','employee','institute'): "
        "source_kind must be 'patent'; ranks that one audience type against the patent.\n"
        "- target_kind='listing': source_kind is the buyer's own type "
        "(company/professor/student/employee/institute); ranks active marketplace listings.\n"
        "- target_kind='patent_pool': source_kind must be 'professor' or 'institute'; "
        "ranks every patent on the platform (not just curated listings) against that buyer."
    ),
)
async def run_match(payload: MatchRequest):
    from .matching_engine_5 import (
        match_patent_to_audience, match_patent_to_all_audiences,
        match_buyer_to_listings, discover_patents_for_buyer,
        TARGET_TYPES, _CATEGORY_LABELS,
    )

    if payload.target_kind in ("audience", *TARGET_TYPES):
        if payload.source_kind != "patent":
            raise api_error(ErrorCode.INVALID_REQUEST, "source_kind must be 'patent' for this target_kind")
        patent, prof = _resolve_patent(payload.source_id)
        if not patent or not prof:
            raise api_error(ErrorCode.PATENT_NOT_FOUND)
        exclude_id = str(prof.get("professor_id", ""))

        if payload.target_kind == "audience":
            from .matching_engine_5 import _get_shared_embedder
            candidates_by_type = {t: _audience_candidates(t) for t in TARGET_TYPES}
            categories = match_patent_to_all_audiences(patent, candidates_by_type, exclude_id=exclude_id, top_k=payload.top_k)
            return {
                "source_kind": "patent", "source_id": payload.source_id, "target_kind": "audience",
                "embeddings_ready": _get_shared_embedder().is_ready,
                "categories": {
                    t: {"label": _CATEGORY_LABELS[t], "count": len(matches), "matches": [m.to_dict() for m in matches]}
                    for t, matches in categories.items()
                },
            }

        candidates = _audience_candidates(payload.target_kind)
        matches = match_patent_to_audience(
            patent, payload.target_kind, candidates,
            exclude_id=exclude_id if payload.target_kind == "professor" else None,
            top_k=payload.top_k,
        )
        return {
            "source_kind": "patent", "source_id": payload.source_id, "target_kind": payload.target_kind,
            "count": len(matches), "matches": [m.to_dict() for m in matches],
        }

    if payload.target_kind == "listing":
        buyer_type = payload.source_kind
        if buyer_type not in ("company", "student", "employee", "professor", "institute"):
            raise api_error(ErrorCode.INVALID_TARGET_TYPE)
        buyer_profile = _get_buyer_profile(buyer_type, payload.source_id)
        if not buyer_profile:
            raise api_error(ErrorCode.BUYER_NOT_FOUND)

        from .marketplace_db import list_active_listings
        listings = list_active_listings(db_path=_resolved_db_path())
        matches = match_buyer_to_listings(
            buyer_type, buyer_profile, listings, top_k=payload.top_k,
            domain=payload.domain, industry=payload.industry, max_price=payload.max_price,
            professor_lookup=professors_by_id_cache,
        )
        return {
            "source_kind": buyer_type, "source_id": payload.source_id, "target_kind": "listing",
            "total_active_listings": len(listings), "count": len(matches),
            "matches": [m.to_dict() for m in matches],
        }

    if payload.target_kind == "patent_pool":
        if not engine:
            raise api_error(ErrorCode.ENGINE_NOT_READY)
        buyer_type = payload.source_kind
        if buyer_type not in ("professor", "institute"):
            raise api_error(ErrorCode.INVALID_REQUEST, "source_kind must be 'professor' or 'institute' for target_kind='patent_pool'")
        buyer_profile = _discover_buyer_profile(buyer_type, payload.source_id)
        if not buyer_profile:
            code = ErrorCode.PROFESSOR_NOT_FOUND if buyer_type == "professor" else ErrorCode.BUYER_NOT_FOUND
            raise api_error(code)
        matches = discover_patents_for_buyer(buyer_profile, buyer_type, engine.professors, top_k=payload.top_k)
        return {
            "source_kind": buyer_type, "source_id": payload.source_id, "target_kind": "patent_pool",
            "count": len(matches), "matches": [m.to_dict() for m in matches],
        }

    raise api_error(ErrorCode.INVALID_REQUEST, "target_kind must be one of: audience, company, professor, student, employee, institute, listing, patent_pool")


class DiscoverGroupedInput(BaseModel):
    top_k_professors: int = Field(10, ge=1, le=30)
    patents_per_professor: int = Field(5, ge=1, le=20)


@app.post(
    "/marketplace/discover/{buyer_type}/{buyer_id}/grouped",
    tags=["Matching"],
    summary="Cross-institute patent discovery, grouped by professor + affiliated institute (Engine 5)",
    description=(
        "Same ranking as POST /match with target_kind='patent_pool', but grouped "
        "by professor - each professor entry carries their affiliated institute, "
        "so an institute buyer sees patents from professors across every "
        "institute on the platform, organized by who owns them and where they're "
        "from, not a flat list. Scores the full patent pool untruncated, THEN "
        "groups by professor, THEN truncates (see "
        "matching_engine_5.group_patents_by_professor)."
    ),
)
async def discover_patents_grouped_by_professor(buyer_type: str, buyer_id: str, payload: DiscoverGroupedInput):
    from .matching_engine_5 import discover_patents_for_buyer, group_patents_by_professor, BUYER_TYPES

    if buyer_type not in BUYER_TYPES:
        raise api_error(ErrorCode.INVALID_REQUEST, "buyer_type must be 'professor' or 'institute'")
    if not engine:
        raise api_error(ErrorCode.ENGINE_NOT_READY)

    buyer_profile = _discover_buyer_profile(buyer_type, buyer_id)
    if not buyer_profile:
        code = ErrorCode.PROFESSOR_NOT_FOUND if buyer_type == "professor" else ErrorCode.BUYER_NOT_FOUND
        raise api_error(code)

    matches = discover_patents_for_buyer(buyer_profile, buyer_type, engine.professors, top_k=None)
    groups = group_patents_by_professor(
        matches, top_k_professors=payload.top_k_professors, patents_per_professor=payload.patents_per_professor,
    )
    return {
        "buyer_type": buyer_type, "buyer_id": buyer_id,
        "professor_count": len(groups),
        "groups": [g.to_dict() for g in groups],
    }


class MatchInteractionInput(BaseModel):
    source_kind: str = Field(..., max_length=32)
    source_id: str = Field(..., max_length=64)
    target_kind: str = Field(..., max_length=32)
    target_id: str = Field(..., max_length=64)
    interaction_type: str = Field(..., max_length=32)
    match_score: Optional[float] = None


@app.post(
    "/match/interactions",
    tags=["Matching"],
    summary="Log a match interaction (view/save/offer/bookmark/licensing-request/...)",
    description=(
        "Records an interaction event between any source and target entity "
        "(patent, listing, or an audience member). Feeds 'continuously "
        "improve recommendations' - captured now, not yet fed back into "
        "scoring (that needs enough interaction volume to be meaningful)."
    ),
)
async def record_match_interaction(payload: MatchInteractionInput):
    from .patent_marketplace_db import log_match_interaction, MATCH_INTERACTION_TYPES
    if payload.interaction_type not in MATCH_INTERACTION_TYPES:
        raise api_error(ErrorCode.INVALID_REQUEST, f"interaction_type must be one of {MATCH_INTERACTION_TYPES}")
    log_match_interaction(
        payload.source_kind, payload.source_id, payload.target_kind, payload.target_id,
        payload.interaction_type, payload.match_score, _resolved_db_path(),
    )
    return {"logged": True}


class CreateOfferInput(BaseModel):
    professor_id: str = Field(..., max_length=64)
    target_type: str = Field(..., max_length=32)
    target_id: str = Field(..., max_length=64)
    target_name: str = Field("", max_length=200)
    message: str = Field("", max_length=2000)
    match_score: Optional[float] = None
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list, max_length=10)


@app.post(
    "/marketplace/patents/{patent_id}/offers",
    tags=["PatentMarketplace"],
    summary="Send a direct patent offer to an audience candidate",
)
async def send_patent_offer(patent_id: str, payload: CreateOfferInput):
    if payload.target_type not in ("company", "student", "employee", "professor", "institute"):
        raise api_error(ErrorCode.INVALID_TARGET_TYPE)
    patent, prof = _resolve_patent(patent_id)
    if not patent or not prof:
        raise api_error(ErrorCode.PATENT_NOT_FOUND)

    from .patent_marketplace_db import create_offer
    offer_id = create_offer({
        "patent_id": patent_id,
        "patent_number": str(patent.get("patent_number", "")),
        "patent_title": str(patent.get("title", "")),
        "professor_id": payload.professor_id,
        "professor_name": str(prof.get("name", "")),
        "target_type": payload.target_type,
        "target_id": payload.target_id,
        "target_name": payload.target_name,
        "message": payload.message,
        "match_score": payload.match_score,
        "score_breakdown": payload.score_breakdown,
        "reasons": payload.reasons,
    }, _resolved_db_path())
    return {"offer_id": offer_id, "status": "sent"}


@app.get(
    "/marketplace/patents/offers/sent",
    tags=["PatentMarketplace"],
    summary="Professor's sent offers",
)
async def get_offers_sent(professor_id: str):
    from .patent_marketplace_db import list_offers_sent
    offers = list_offers_sent(professor_id, _resolved_db_path())
    return {"professor_id": professor_id, "count": len(offers), "offers": offers}


@app.get(
    "/marketplace/patents/offers/received",
    tags=["PatentMarketplace"],
    summary="Offers received by one audience candidate",
)
async def get_offers_received(target_type: str, target_id: str):
    if target_type not in ("company", "student", "employee", "professor", "institute"):
        raise api_error(ErrorCode.INVALID_TARGET_TYPE)
    from .patent_marketplace_db import list_offers_received
    offers = list_offers_received(target_type, target_id, _resolved_db_path())
    return {"target_type": target_type, "target_id": target_id, "count": len(offers), "offers": offers}


class RespondOfferInput(BaseModel):
    status: str = Field(..., max_length=16)


@app.post(
    "/marketplace/patents/offers/{offer_id}/respond",
    tags=["PatentMarketplace"],
    summary="Accept or decline a received patent offer",
)
async def respond_offer(offer_id: str, payload: RespondOfferInput):
    if payload.status not in ("accepted", "declined", "viewed"):
        raise api_error(ErrorCode.INVALID_REQUEST, "status must be accepted, declined, or viewed")
    from .patent_marketplace_db import respond_to_offer
    offer = respond_to_offer(offer_id, payload.status, _resolved_db_path())
    if not offer:
        raise api_error(ErrorCode.OFFER_NOT_FOUND)
    return offer


# ═══════════════════════════════════════════════════════════════════════════
# MATCHING ENGINE 6 — Buyer Dashboard: Discover Patents (audience -> listings)
# ═══════════════════════════════════════════════════════════════════════════

openapi_tags.append({
    "name": "PatentDiscovery",
    "description": (
        "Buyer Dashboard 'discover patents' feature for all five registered "
        "audience types. Engine 6 ranks active patent_listings (the real, "
        "priced, licensable marketplace entity) against a buyer's profile. "
        "Also covers purchase/licensing inquiries and wishlist."
    ),
})


def _get_buyer_profile(buyer_type: str, buyer_id: str) -> Optional[Dict[str, Any]]:
    if buyer_type == "company":
        from .marketplace_db import get_buyer
        return get_buyer(buyer_id, _resolved_db_path())
    if buyer_type == "professor":
        return professors_by_id_cache.get(buyer_id)
    if buyer_type == "student":
        from .patent_marketplace_db import get_student_profile
        return get_student_profile(buyer_id, _resolved_db_path())
    if buyer_type == "employee":
        from .patent_marketplace_db import get_employee_profile
        return get_employee_profile(buyer_id, _resolved_db_path())
    if buyer_type == "institute":
        from .patent_marketplace_db import get_institute_profile
        return get_institute_profile(buyer_id, _resolved_db_path())
    raise api_error(ErrorCode.INVALID_TARGET_TYPE)


# AI-ranked active patent listings for one buyer: see POST /match with
# target_kind="listing" (replaces the old dedicated
# /marketplace/discover/{buyer_type}/recommendations route).


class ListingInquiryInput(BaseModel):
    buyer_type: str = Field(..., max_length=32)
    buyer_id: str = Field(..., max_length=64)
    buyer_name: str = Field("", max_length=200)
    message: str = Field("", max_length=2000)
    match_score: Optional[float] = None
    inquiry_type: str = Field("inquiry", max_length=32)


@app.post(
    "/marketplace/listings/{listing_id}/inquire",
    tags=["PatentDiscovery"],
    summary="Contact the professor to negotiate a purchase/licensing deal",
)
async def inquire_about_listing(listing_id: str, payload: ListingInquiryInput):
    if payload.buyer_type not in ("company", "student", "employee", "professor", "institute"):
        raise api_error(ErrorCode.INVALID_TARGET_TYPE)
    if payload.inquiry_type not in ("inquiry", "purchase_request", "licensing_request"):
        raise api_error(ErrorCode.INVALID_REQUEST, "inquiry_type must be one of: inquiry, purchase_request, licensing_request")
    from .marketplace_db import get_listing
    listing = get_listing(listing_id, _resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)

    from .patent_marketplace_db import create_listing_inquiry
    prof = professors_by_id_cache.get(listing.get("professor_id", ""), {})
    inquiry_id = create_listing_inquiry({
        "listing_id": listing_id,
        "listing_title": listing.get("title", ""),
        "professor_id": listing.get("professor_id", ""),
        "professor_name": prof.get("name", ""),
        "buyer_type": payload.buyer_type,
        "buyer_id": payload.buyer_id,
        "buyer_name": payload.buyer_name,
        "message": payload.message,
        "match_score": payload.match_score,
        "inquiry_type": payload.inquiry_type,
    }, _resolved_db_path())
    return {"inquiry_id": inquiry_id, "status": "sent"}


@app.get(
    "/marketplace/listings/inquiries/sent",
    tags=["PatentDiscovery"],
    summary="Purchase/licensing inquiries a buyer has sent",
)
async def get_listing_inquiries_sent(buyer_type: str, buyer_id: str):
    from .patent_marketplace_db import list_listing_inquiries_for_buyer
    inquiries = list_listing_inquiries_for_buyer(buyer_type, buyer_id, _resolved_db_path())
    return {"buyer_type": buyer_type, "buyer_id": buyer_id, "count": len(inquiries), "inquiries": inquiries}


@app.get(
    "/marketplace/listings/inquiries/received",
    tags=["PatentDiscovery"],
    summary="Purchase/licensing inquiries a professor has received",
)
async def get_listing_inquiries_received(professor_id: str):
    from .patent_marketplace_db import list_listing_inquiries_for_professor
    inquiries = list_listing_inquiries_for_professor(professor_id, _resolved_db_path())
    return {"professor_id": professor_id, "count": len(inquiries), "inquiries": inquiries}


class RespondListingInquiryInput(BaseModel):
    status: str = Field(..., max_length=16)


@app.post(
    "/marketplace/listings/inquiries/{inquiry_id}/respond",
    tags=["PatentDiscovery"],
    summary="Professor responds to a purchase/licensing inquiry",
)
async def respond_listing_inquiry(inquiry_id: str, payload: RespondListingInquiryInput):
    if payload.status not in ("negotiating", "accepted", "declined", "viewed"):
        raise api_error(ErrorCode.INVALID_REQUEST, "status must be negotiating, accepted, declined, or viewed")
    from .patent_marketplace_db import respond_to_listing_inquiry
    inquiry = respond_to_listing_inquiry(inquiry_id, payload.status, _resolved_db_path())
    if not inquiry:
        raise api_error(ErrorCode.INQUIRY_NOT_FOUND)
    return inquiry


class WishlistInput(BaseModel):
    buyer_type: str = Field(..., max_length=32)
    buyer_id: str = Field(..., max_length=64)
    listing_id: str = Field(..., max_length=64)


@app.post("/marketplace/wishlist", tags=["PatentDiscovery"], summary="Save a listing to a buyer's wishlist")
async def wishlist_add(payload: WishlistInput):
    from .patent_marketplace_db import add_wishlist_item
    add_wishlist_item(payload.buyer_type, payload.buyer_id, payload.listing_id, _resolved_db_path())
    return {"saved": True}


@app.post("/marketplace/wishlist/remove", tags=["PatentDiscovery"], summary="Remove a listing from a buyer's wishlist")
async def wishlist_remove(payload: WishlistInput):
    from .patent_marketplace_db import remove_wishlist_item
    remove_wishlist_item(payload.buyer_type, payload.buyer_id, payload.listing_id, _resolved_db_path())
    return {"removed": True}


@app.get("/marketplace/wishlist", tags=["PatentDiscovery"], summary="A buyer's wishlisted listings, hydrated with listing details")
async def wishlist_list(buyer_type: str, buyer_id: str):
    from .patent_marketplace_db import list_wishlist_items
    from .marketplace_db import get_listing
    listing_ids = list_wishlist_items(buyer_type, buyer_id, _resolved_db_path())
    listings = []
    for lid in listing_ids:
        l = get_listing(lid, _resolved_db_path())
        if l:
            listings.append(l)
    return {"buyer_type": buyer_type, "buyer_id": buyer_id, "count": len(listings), "listings": listings}


# Buyer (professor/institute) -> raw platform-wide patent pool: see POST
# /match with target_kind="patent_pool" (replaces the old dedicated
# /discover-patents/{buyer_type}/{buyer_id} + .../interactions routes).

def _discover_buyer_profile(buyer_type: str, buyer_id: str) -> Optional[Dict[str, Any]]:
    if buyer_type == "professor":
        return professors_by_id_cache.get(buyer_id)
    if buyer_type == "institute":
        from .patent_marketplace_db import get_institute_profile
        return get_institute_profile(buyer_id, _resolved_db_path())
    raise api_error(ErrorCode.INVALID_REQUEST, "buyer_type must be 'professor' or 'institute'")


# ═══════════════════════════════════════════════════════════════════════════
# MATCHING ENGINE 7 — Student Dashboard: Student -> Patents & Professors
# ═══════════════════════════════════════════════════════════════════════════

openapi_tags.append({
    "name": "StudentDashboard",
    "description": (
        "AI Patent Marketplace for students: build a profile (optionally from "
        "an uploaded resume), get AI-ranked patents/professors to buy or "
        "license via Engine 7, view AI skill-gap analysis and startup-"
        "opportunity insights, and track saved/purchased/licensed patents."
    ),
})


@app.post(
    "/marketplace/profiles/student/resume",
    tags=["StudentDashboard"],
    summary="Upload a resume (PDF/DOCX/TXT) and get AI-suggested profile fields",
    description=(
        "Extracts text from the uploaded file and parses it (Claude, falling "
        "back to rule-based extraction) into suggested profile fields. "
        "Does NOT save the profile - the student reviews/edits the "
        "suggestions before calling POST /marketplace/profiles/student."
    ),
)
async def upload_student_resume(file: UploadFile = File(...), user_id: str = Form("")):
    from .resume_parser import extract_text_from_file, parse_resume, UnsupportedResumeFormat, is_extraction_too_sparse
    from . import resume_storage

    content = await file.read()
    if len(content) > resume_storage.MAX_RESUME_BYTES:
        raise api_error(
            ErrorCode.FILE_TOO_LARGE,
            f"Resume file is too large ({len(content) / 1_000_000:.1f} MB) - the limit is "
            f"{resume_storage.MAX_RESUME_BYTES // 1_000_000} MB.",
        )

    try:
        text = extract_text_from_file(file.filename or "", content)
    except UnsupportedResumeFormat as e:
        raise api_error(ErrorCode.INVALID_REQUEST, str(e))
    except Exception:
        raise api_error(
            ErrorCode.INVALID_REQUEST,
            "Couldn't read this file - it may be corrupted or not a valid PDF/DOCX. Please try "
            "re-exporting it or fill in your profile manually instead.",
        )

    if is_extraction_too_sparse(text):
        raise api_error(
            ErrorCode.INVALID_REQUEST,
            "Couldn't extract readable text from this file (it may be a scanned/image-only "
            "document). Please fill in your profile manually instead.",
        )

    resume_file_path = ""
    try:
        resume_file_path = resume_storage.save_resume_file(user_id or "resume", file.filename or "", content)
    except resume_storage.ResumeFileTooLarge as e:
        raise api_error(ErrorCode.FILE_TOO_LARGE, str(e))
    except resume_storage.UnsupportedResumeFileType:
        pass  # text was still extracted fine (e.g. a .txt upload) - just nothing to preview/download

    parsed = parse_resume(text, use_claude=True)
    result = parsed.to_dict()
    result["resume_filename"] = file.filename or ""
    result["resume_text"] = text[:20000]
    result["resume_file_path"] = resume_file_path
    return result


def _student_profile_completion(profile: Dict[str, Any]) -> float:
    fields = [
        "name", "institute", "field_of_study", "bio", "skills", "interests",
        "research_areas", "education", "projects", "certifications",
        "career_goals", "preferred_domains",
    ]
    filled = sum(1 for f in fields if profile.get(f))
    return round(100.0 * filled / len(fields), 1)


class StudentRecommendationsInput(BaseModel):
    top_k_patents: int = Field(20, ge=1, le=50)
    top_k_professors: int = Field(10, ge=1, le=30)
    patents_per_professor: int = Field(3, ge=1, le=10)


@app.post(
    "/student-dashboard/{student_id}/recommendations",
    tags=["StudentDashboard"],
    summary="AI-ranked patents + professors for a student (Engine 7)",
)
async def get_student_recommendations(student_id: str, payload: StudentRecommendationsInput):
    from .patent_marketplace_db import get_student_profile
    from .marketplace_db import list_active_listings
    from .matching_engine_7 import match_patents_for_buyer, match_professors_for_buyer

    profile = get_student_profile(student_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No student profile found - create one first")

    listings = list_active_listings(db_path=_resolved_db_path())
    recommended_patents = match_patents_for_buyer(
        "student", profile, listings, professors_by_id_cache, top_k=payload.top_k_patents,
    )
    recommended_professors = match_professors_for_buyer(
        "student", profile, listings, professors_by_id_cache,
        top_k=payload.top_k_professors, patents_per_professor=payload.patents_per_professor,
    )
    return {
        "student_id": student_id,
        "recommended_patents": [p.to_dict() for p in recommended_patents],
        "recommended_professors": [p.to_dict() for p in recommended_professors],
    }


@app.get(
    "/student-dashboard/{student_id}/overview",
    tags=["StudentDashboard"],
    summary="Dashboard overview stats for a student",
)
async def get_student_overview(student_id: str):
    from .patent_marketplace_db import get_student_profile, list_wishlist_items, list_transactions_for_buyer
    from .marketplace_db import list_active_listings
    from .matching_engine_7 import match_patents_for_buyer, match_professors_for_buyer

    profile = get_student_profile(student_id, _resolved_db_path())
    completion = _student_profile_completion(profile) if profile else 0.0

    recommended_patent_count = 0
    recommended_professor_count = 0
    avg_match_score = 0.0
    if profile:
        listings = list_active_listings(db_path=_resolved_db_path())
        patents = match_patents_for_buyer("student", profile, listings, professors_by_id_cache, top_k=20)
        professors = match_professors_for_buyer("student", profile, listings, professors_by_id_cache, top_k=10)
        recommended_patent_count = len(patents)
        recommended_professor_count = len(professors)
        if patents:
            avg_match_score = round(sum(p.match_score for p in patents) / len(patents), 1)

    saved_count = len(list_wishlist_items("student", student_id, _resolved_db_path()))
    transactions = list_transactions_for_buyer("student", student_id, _resolved_db_path())

    notifications_count = 0
    try:
        notif = await tech_transfer_notifications("student", student_id)
        notifications_count = notif.get("count", 0)
    except Exception:
        pass

    return {
        "student_id": student_id,
        "profile_completion_pct": completion,
        "avg_match_score": avg_match_score,
        "recommended_patent_count": recommended_patent_count,
        "recommended_professor_count": recommended_professor_count,
        "saved_patents_count": saved_count,
        "purchased_licensed_count": len(transactions),
        "notifications_count": notifications_count,
    }


@app.get(
    "/student-dashboard/{student_id}/patents/{listing_id}",
    tags=["StudentDashboard"],
    summary="Patent detail + this student's AI compatibility score",
)
async def get_student_patent_detail(student_id: str, listing_id: str):
    from .marketplace_db import get_listing
    from .patent_marketplace_db import get_student_profile
    from .matching_engine_7 import match_patents_for_buyer

    listing = get_listing(listing_id, _resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)

    compatibility = None
    profile = get_student_profile(student_id, _resolved_db_path())
    if profile:
        matches = match_patents_for_buyer("student", profile, [listing], professors_by_id_cache, top_k=1)
        if matches:
            compatibility = matches[0].to_dict()

    return {"listing": listing, "compatibility": compatibility}


@app.get(
    "/student-dashboard/{student_id}/skill-gap/{listing_id}",
    tags=["StudentDashboard"],
    summary="AI skill-gap analysis for a student against one patent",
)
async def get_student_skill_gap(student_id: str, listing_id: str):
    from .marketplace_db import get_listing
    from .patent_marketplace_db import get_student_profile
    from .skill_gap_analyzer import analyze_skill_gap

    profile = get_student_profile(student_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No student profile found - create one first")
    listing = get_listing(listing_id, _resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)

    result = analyze_skill_gap(student_id, profile, listing, use_claude=True, db_path=_resolved_db_path())
    return result.to_dict()


@app.get(
    "/patents/{listing_id}/startup-insights",
    tags=["StudentDashboard"],
    summary="AI startup-opportunity insights for one patent",
)
async def get_patent_startup_insights(listing_id: str):
    from .marketplace_db import get_listing
    from .startup_insights import generate_startup_insights

    listing = get_listing(listing_id, _resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)

    result = generate_startup_insights(listing, use_claude=True, db_path=_resolved_db_path())
    return result.to_dict()


@app.get(
    "/student-dashboard/{student_id}/purchased-licensed",
    tags=["StudentDashboard"],
    summary="A student's purchased and licensed patents",
)
async def get_student_transactions(student_id: str):
    from .patent_marketplace_db import list_transactions_for_buyer
    transactions = list_transactions_for_buyer("student", student_id, _resolved_db_path())
    return {"student_id": student_id, "count": len(transactions), "transactions": transactions}


class AcceptPurchaseInquiryInput(BaseModel):
    price: Optional[float] = Field(None, ge=0)
    license_expiry: Optional[float] = None


@app.post(
    "/marketplace/listings/inquiries/{inquiry_id}/accept-purchase",
    tags=["StudentDashboard"],
    summary="Professor accepts a buy/license inquiry, recording a (simulated) transaction",
)
async def accept_purchase_inquiry_endpoint(inquiry_id: str, payload: AcceptPurchaseInquiryInput):
    from .patent_marketplace_db import accept_purchase_inquiry
    result = accept_purchase_inquiry(
        inquiry_id, price=payload.price, license_expiry=payload.license_expiry,
        db_path=_resolved_db_path(),
    )
    if not result:
        raise api_error(ErrorCode.INQUIRY_NOT_FOUND)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# MATCHING ENGINE 9 — Job Postings: Student/Employee <-> Company
# ═══════════════════════════════════════════════════════════════════════════
# Job postings don't exist elsewhere on the platform (the rest of CollabV
# matches companies with professor-owned patents, not job-seekers with
# roles) - this section is net-new. Live-data-only: the demo seed script
# that used to populate sample postings has been retired to
# archive/seed_scripts/seed_job_postings.py - POST /jobs below is now the
# only way a job posting enters the system.

openapi_tags.append({
    "name": "MatchingEngine9",
    "description": (
        "AI Matching Engine 9: job postings CRUD (seed-populated for "
        "now), a multi-factor student -> job compatibility scorer "
        "(skills/experience/education/certifications/keywords/semantic "
        "similarity), the Student Dashboard's AI Matching Engine 9 list "
        "with auto-refresh on resume or job-posting changes, AI "
        "suggestions to close skill gaps, and Apply Now."
    ),
})


class JobPostingInput(BaseModel):
    company_id: str = Field("", max_length=64)
    company_name: str = Field(..., max_length=200)
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=8000)
    required_skills: List[str] = Field(default_factory=list, max_length=30)
    preferred_skills: List[str] = Field(default_factory=list, max_length=30)
    min_experience_years: float = Field(0, ge=0, le=40)
    education_requirement: str = Field("", max_length=300)
    certifications_preferred: List[str] = Field(default_factory=list, max_length=15)
    keywords: List[str] = Field(default_factory=list, max_length=30)
    domain_tags: List[str] = Field(default_factory=list, max_length=15)
    employment_type: str = Field("full_time", pattern="^(full_time|internship)$")
    is_remote: bool = False
    location: str = Field("", max_length=200)


@app.post("/jobs", tags=["MatchingEngine9"], summary="Create a job posting")
async def create_job_posting(payload: JobPostingInput):
    from .job_matching_db import save_job_posting
    job_id = save_job_posting(payload.model_dump(), _resolved_db_path())
    return {"job_id": job_id, "saved": True}


@app.get("/jobs", tags=["MatchingEngine9"], summary="Browse job postings")
async def browse_job_postings(
    status: Optional[str] = "active",
    employment_type: Optional[str] = None,
    is_remote: Optional[bool] = None,
):
    from .job_matching_db import list_job_postings
    jobs = list_job_postings(
        status=status, employment_type=employment_type, is_remote=is_remote,
        db_path=_resolved_db_path(),
    )
    return {"count": len(jobs), "jobs": jobs}


@app.get("/jobs/{job_id}", tags=["MatchingEngine9"], summary="Job posting detail")
async def get_job_posting_detail(job_id: str):
    from .job_matching_db import get_job_posting
    job = get_job_posting(job_id, _resolved_db_path())
    if not job:
        raise api_error(ErrorCode.JOB_NOT_FOUND)
    return job


class JobPostingUpdateInput(BaseModel):
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    required_skills: Optional[List[str]] = None
    preferred_skills: Optional[List[str]] = None
    min_experience_years: Optional[float] = None
    education_requirement: Optional[str] = None
    certifications_preferred: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    domain_tags: Optional[List[str]] = None
    employment_type: Optional[str] = None
    is_remote: Optional[bool] = None
    location: Optional[str] = None


@app.patch(
    "/jobs/{job_id}", tags=["MatchingEngine9"],
    summary="Update a job posting (bumps updated_at, triggering match re-scoring)",
)
async def update_job_posting(job_id: str, payload: JobPostingUpdateInput):
    from .job_matching_db import get_job_posting, save_job_posting
    existing = get_job_posting(job_id, _resolved_db_path())
    if not existing:
        raise api_error(ErrorCode.JOB_NOT_FOUND)
    updates = payload.model_dump(exclude_unset=True)
    merged = {**existing, **updates, "job_id": job_id}
    save_job_posting(merged, _resolved_db_path())
    return get_job_posting(job_id, _resolved_db_path())


@app.delete("/jobs/{job_id}", tags=["MatchingEngine9"], summary="Close a job posting")
async def delete_job_posting(job_id: str):
    from .job_matching_db import close_job_posting
    job = close_job_posting(job_id, _resolved_db_path())
    if not job:
        raise api_error(ErrorCode.JOB_NOT_FOUND)
    return job


def _ensure_fresh_match_scores(candidate_id: str, profile: Dict[str, Any], jobs: List[Dict[str, Any]]) -> None:
    """The auto-refresh mechanism: any job whose cached score predates the
    candidate's resume update or the job posting's own last edit is stale
    (or missing entirely) and gets recomputed here before the caller reads
    the list back. No background job queue - this runs synchronously on the
    dashboard's job-matches request, the same lazy-cache-invalidation idea
    as skill_gap_analyzer's updated_at-folded cache key. Shared by both the
    Student and Employee Dashboards - candidate_id is a student_id or
    employee_id, opaque to job_matching_db's generically-named columns."""
    from .job_matching_db import get_cached_match, save_match_score
    from .matching_engine_9 import score_student_against_all_jobs

    profile_version = profile.get("updated_at", 0)
    stale_jobs = []
    for job in jobs:
        job_version = job.get("updated_at", 0)
        cached = get_cached_match(candidate_id, job["job_id"], _resolved_db_path())
        if not cached or cached["profile_version"] != profile_version or cached["job_version"] != job_version:
            stale_jobs.append(job)

    if not stale_jobs:
        return

    matches = score_student_against_all_jobs(profile, stale_jobs)
    jobs_by_id = {j["job_id"]: j for j in stale_jobs}
    for match in matches:
        job = jobs_by_id[match.job_id]
        save_match_score(
            candidate_id, match.job_id, match.to_dict(),
            profile_version, job.get("updated_at", 0), _resolved_db_path(),
        )


@app.get(
    "/student-dashboard/{student_id}/job-matches",
    tags=["MatchingEngine9"],
    summary="AI-ranked job postings for a student, auto-refreshed on resume/job changes",
)
async def get_student_job_matches(
    student_id: str,
    sort: str = "match",
    employment_type: Optional[str] = None,
    is_remote: Optional[bool] = None,
):
    from .patent_marketplace_db import get_student_profile
    from .job_matching_db import list_job_postings, list_match_scores_for_student

    profile = get_student_profile(student_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No student profile found - create one first")

    jobs = list_job_postings(
        status="active", employment_type=employment_type, is_remote=is_remote,
        db_path=_resolved_db_path(),
    )
    _ensure_fresh_match_scores(student_id, profile, jobs)

    scores_by_job = {s["job_id"]: s for s in list_match_scores_for_student(student_id, _resolved_db_path())}
    jobs_by_id = {j["job_id"]: j for j in jobs}

    matches = []
    for job_id, score in scores_by_job.items():
        job = jobs_by_id.get(job_id)
        if not job:
            continue  # stale score for a job outside the current filter (or since closed)
        matches.append({
            **score,
            "title": job["title"],
            "company_name": job["company_name"],
            "employment_type": job["employment_type"],
            "is_remote": job["is_remote"],
            "location": job["location"],
            "created_at": job["created_at"],
        })

    if sort == "newest":
        matches.sort(key=lambda m: -m["created_at"])
    else:
        matches.sort(key=lambda m: -m["match_score"])

    return {"student_id": student_id, "count": len(matches), "matches": matches}


@app.get(
    "/student-dashboard/{student_id}/job-matches/{job_id}/suggestions",
    tags=["MatchingEngine9"],
    summary="AI suggestions to improve this student's match with one job",
)
async def get_job_match_suggestions(student_id: str, job_id: str):
    from .patent_marketplace_db import get_student_profile
    from .job_matching_db import get_job_posting
    from .matching_engine_9 import score_student_against_job, generate_match_suggestions

    profile = get_student_profile(student_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No student profile found - create one first")
    job = get_job_posting(job_id, _resolved_db_path())
    if not job:
        raise api_error(ErrorCode.JOB_NOT_FOUND)

    job_match = score_student_against_job(profile, job)
    return generate_match_suggestions(
        student_id, profile, job, job_match, use_claude=True, db_path=_resolved_db_path(),
    )


@app.post(
    "/student-dashboard/{student_id}/job-matches/{job_id}/apply",
    tags=["MatchingEngine9"],
    summary="Apply to a job (idempotent - re-applying returns the existing application)",
)
async def apply_to_job(student_id: str, job_id: str):
    from .job_matching_db import get_job_posting, get_cached_match, create_application
    from .patent_marketplace_db import get_student_profile

    if not get_student_profile(student_id, _resolved_db_path()):
        raise api_error(ErrorCode.MISSING_INPUT, "No student profile found - create one first")
    if not get_job_posting(job_id, _resolved_db_path()):
        raise api_error(ErrorCode.JOB_NOT_FOUND)

    cached = get_cached_match(student_id, job_id, _resolved_db_path())
    match_score = cached["match_score"] if cached else None
    application = create_application(student_id, job_id, match_score, _resolved_db_path())
    return {
        "application_id": application["application_id"],
        "status": application["status"],
        "already_applied": application["already_applied"],
    }


@app.get(
    "/student-dashboard/{student_id}/applications",
    tags=["MatchingEngine9"],
    summary="A student's job applications",
)
async def get_student_applications(student_id: str):
    from .job_matching_db import list_applications_for_student
    applications = list_applications_for_student(student_id, _resolved_db_path())
    return {"student_id": student_id, "count": len(applications), "applications": applications}


# ═══════════════════════════════════════════════════════════════════════════
# MATCHING ENGINE 8 — Research Opportunities: Student <-> Professor
# ═══════════════════════════════════════════════════════════════════════════
# Professors post research opportunities (PhD/Master's positions, research
# internships, RA roles, fellowships, etc.); students get AI-ranked
# opportunities in their dashboard, and professors get AI-ranked candidate
# students per opportunity they posted - a new direction relative to the
# earlier AI Matching Engine 9 (job postings), which only ever scored
# student-first.

openapi_tags.append({
    "name": "MatchingEngine8",
    "description": (
        "AI Matching Engine 8: research opportunities CRUD (professors post "
        "PhD/Master's positions, internships, RA roles, fellowships, etc.), "
        "a multi-factor student <-> opportunity compatibility scorer "
        "(skills/semantic/research-fit/experience/qualifications/keywords), "
        "the Student Dashboard's AI Matching Engine 8 tab with auto-refresh "
        "on resume or opportunity changes, the Professor Dashboard's ranked "
        "candidate-students section with filters and AI insights, resume "
        "preview/download, and Express Interest / Invite actions."
    ),
})


class ResearchOpportunityInput(BaseModel):
    professor_id: str = Field(..., max_length=64)
    professor_name: str = Field(..., max_length=200)
    department: str = Field("", max_length=200)
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=8000)
    opportunity_type: str = Field("research_internship", max_length=50)
    degree_level: str = Field("", max_length=50)
    research_areas: List[str] = Field(default_factory=list, max_length=15)
    required_skills: List[str] = Field(default_factory=list, max_length=30)
    preferred_skills: List[str] = Field(default_factory=list, max_length=30)
    required_qualifications: List[str] = Field(default_factory=list, max_length=15)
    preferred_qualifications: List[str] = Field(default_factory=list, max_length=15)
    min_experience_years: float = Field(0, ge=0, le=40)
    education_requirement: str = Field("", max_length=300)
    publications_expected: bool = False
    keywords: List[str] = Field(default_factory=list, max_length=30)
    domain_tags: List[str] = Field(default_factory=list, max_length=15)
    duration: str = Field("", max_length=100)
    stipend_or_funding: str = Field("", max_length=200)
    location: str = Field("", max_length=200)
    is_remote: bool = False
    university: str = Field("IIT Madras", max_length=200)


@app.post("/research-opportunities", tags=["MatchingEngine8"], summary="Post a research opportunity")
async def create_research_opportunity(payload: ResearchOpportunityInput):
    from .research_opportunity_db import save_opportunity
    opportunity_id = save_opportunity(payload.model_dump(), _resolved_db_path())
    return {"opportunity_id": opportunity_id, "saved": True}


@app.get("/research-opportunities", tags=["MatchingEngine8"], summary="Browse research opportunities")
async def browse_research_opportunities(
    status: Optional[str] = "active",
    opportunity_type: Optional[str] = None,
    degree_level: Optional[str] = None,
    professor_id: Optional[str] = None,
):
    from .research_opportunity_db import list_opportunities
    opportunities = list_opportunities(
        status=status, opportunity_type=opportunity_type, degree_level=degree_level,
        professor_id=professor_id, db_path=_resolved_db_path(),
    )
    return {"count": len(opportunities), "opportunities": opportunities}


@app.get("/research-opportunities/{opportunity_id}", tags=["MatchingEngine8"], summary="Research opportunity detail")
async def get_research_opportunity_detail(opportunity_id: str):
    from .research_opportunity_db import get_opportunity
    opportunity = get_opportunity(opportunity_id, _resolved_db_path())
    if not opportunity:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)
    return opportunity


class ResearchOpportunityUpdateInput(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    opportunity_type: Optional[str] = None
    degree_level: Optional[str] = None
    research_areas: Optional[List[str]] = None
    required_skills: Optional[List[str]] = None
    preferred_skills: Optional[List[str]] = None
    required_qualifications: Optional[List[str]] = None
    preferred_qualifications: Optional[List[str]] = None
    min_experience_years: Optional[float] = None
    education_requirement: Optional[str] = None
    publications_expected: Optional[bool] = None
    keywords: Optional[List[str]] = None
    domain_tags: Optional[List[str]] = None
    duration: Optional[str] = None
    stipend_or_funding: Optional[str] = None
    location: Optional[str] = None
    is_remote: Optional[bool] = None


@app.patch(
    "/research-opportunities/{opportunity_id}", tags=["MatchingEngine8"],
    summary="Update a research opportunity (bumps updated_at, triggering match re-scoring)",
)
async def update_research_opportunity(opportunity_id: str, payload: ResearchOpportunityUpdateInput):
    from .research_opportunity_db import get_opportunity, save_opportunity
    existing = get_opportunity(opportunity_id, _resolved_db_path())
    if not existing:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)
    updates = payload.model_dump(exclude_unset=True)
    merged = {**existing, **updates, "opportunity_id": opportunity_id}
    save_opportunity(merged, _resolved_db_path())
    return get_opportunity(opportunity_id, _resolved_db_path())


@app.delete("/research-opportunities/{opportunity_id}", tags=["MatchingEngine8"], summary="Close a research opportunity")
async def delete_research_opportunity(opportunity_id: str):
    from .research_opportunity_db import close_opportunity
    opportunity = close_opportunity(opportunity_id, _resolved_db_path())
    if not opportunity:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)
    return opportunity


@app.get(
    "/professor/{professor_id}/research-opportunities", tags=["MatchingEngine8"],
    summary="A professor's own posted research opportunities",
)
async def get_professor_research_opportunities(professor_id: str, status: Optional[str] = None):
    from .research_opportunity_db import list_opportunities
    opportunities = list_opportunities(status=status, professor_id=professor_id, db_path=_resolved_db_path())
    return {"professor_id": professor_id, "count": len(opportunities), "opportunities": opportunities}


def _ensure_fresh_opportunity_matches(student_id: str, profile: Dict[str, Any], opportunities: List[Dict[str, Any]]) -> None:
    """Auto-refresh, student-side: mirrors _ensure_fresh_match_scores from
    the AI Matching Engine 9 section above."""
    from .research_opportunity_db import get_cached_opportunity_match, save_opportunity_match
    from .matching_engine_8 import score_student_against_all_opportunities

    profile_version = profile.get("updated_at", 0)
    stale = []
    for opp in opportunities:
        opp_version = opp.get("updated_at", 0)
        cached = get_cached_opportunity_match(student_id, opp["opportunity_id"], _resolved_db_path())
        if not cached or cached["profile_version"] != profile_version or cached["opportunity_version"] != opp_version:
            stale.append(opp)

    if not stale:
        return

    matches = score_student_against_all_opportunities(profile, stale)
    opps_by_id = {o["opportunity_id"]: o for o in stale}
    for match in matches:
        opp = opps_by_id[match.opportunity_id]
        save_opportunity_match(
            student_id, match.opportunity_id, match.to_dict(),
            profile_version, opp.get("updated_at", 0), _resolved_db_path(),
        )


def _ensure_fresh_candidate_scores(opportunity: Dict[str, Any], students: List[Dict[str, Any]]) -> None:
    """Auto-refresh, professor-side: the symmetric direction the AI Resume
    Matching Engine never needed - queries/writes the SAME
    research_opportunity_matches cache table, just from the opportunity
    side (WHERE opportunity_id = ?) instead of the student side."""
    from .research_opportunity_db import get_cached_opportunity_match, save_opportunity_match
    from .matching_engine_8 import score_students_against_opportunity

    opportunity_version = opportunity.get("updated_at", 0)
    stale_students = []
    for student in students:
        student_id = student.get("user_id", "")
        if not student_id:
            continue
        profile_version = student.get("updated_at", 0)
        cached = get_cached_opportunity_match(student_id, opportunity["opportunity_id"], _resolved_db_path())
        if not cached or cached["profile_version"] != profile_version or cached["opportunity_version"] != opportunity_version:
            stale_students.append(student)

    if not stale_students:
        return

    pairs = score_students_against_opportunity(stale_students, opportunity)
    for profile, match in pairs:
        student_id = profile.get("user_id", "")
        save_opportunity_match(
            student_id, match.opportunity_id, match.to_dict(),
            profile.get("updated_at", 0), opportunity_version, _resolved_db_path(),
        )


@app.get(
    "/student-dashboard/{student_id}/opportunity-matches", tags=["MatchingEngine8"],
    summary="AI-ranked research opportunities for a student, auto-refreshed on resume/opportunity changes",
)
async def get_student_opportunity_matches(
    student_id: str,
    sort: str = "match",
    opportunity_type: Optional[str] = None,
    degree_level: Optional[str] = None,
):
    from .patent_marketplace_db import get_student_profile
    from .research_opportunity_db import list_opportunities, list_opportunity_matches_for_student

    profile = get_student_profile(student_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No student profile found - create one first")

    opportunities = list_opportunities(
        status="active", opportunity_type=opportunity_type, degree_level=degree_level,
        db_path=_resolved_db_path(),
    )
    _ensure_fresh_opportunity_matches(student_id, profile, opportunities)

    scores_by_opp = {s["opportunity_id"]: s for s in list_opportunity_matches_for_student(student_id, _resolved_db_path())}
    opps_by_id = {o["opportunity_id"]: o for o in opportunities}

    matches = []
    for opp_id, score in scores_by_opp.items():
        opp = opps_by_id.get(opp_id)
        if not opp:
            continue  # stale score for an opportunity outside the current filter (or since closed)
        matches.append({
            **score,
            "title": opp["title"],
            "professor_name": opp["professor_name"],
            "department": opp["department"],
            "opportunity_type": opp["opportunity_type"],
            "degree_level": opp["degree_level"],
            "duration": opp["duration"],
            "location": opp["location"],
            "is_remote": opp["is_remote"],
            "created_at": opp["created_at"],
        })

    if sort == "newest":
        matches.sort(key=lambda m: -m["created_at"])
    else:
        matches.sort(key=lambda m: -m["match_score"])

    return {"student_id": student_id, "count": len(matches), "matches": matches}


@app.get(
    "/student-dashboard/{student_id}/opportunity-matches/{opportunity_id}/suggestions",
    tags=["MatchingEngine8"],
    summary="AI suggestions to improve this student's match with one opportunity",
)
async def get_opportunity_match_suggestions(student_id: str, opportunity_id: str):
    from .patent_marketplace_db import get_student_profile
    from .research_opportunity_db import get_opportunity
    from .matching_engine_8 import score_student_against_opportunity, generate_match_suggestions

    profile = get_student_profile(student_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No student profile found - create one first")
    opportunity = get_opportunity(opportunity_id, _resolved_db_path())
    if not opportunity:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)

    match = score_student_against_opportunity(profile, opportunity)
    return generate_match_suggestions(
        student_id, profile, opportunity, match, use_claude=True, db_path=_resolved_db_path(),
    )


@app.get(
    "/student-dashboard/{student_id}/opportunity-matches/{opportunity_id}/fit-explanation",
    tags=["MatchingEngine8"],
    summary="AI-generated natural-language explanation of why this student fits (or doesn't) this opportunity",
)
async def get_opportunity_fit_explanation(student_id: str, opportunity_id: str):
    from .patent_marketplace_db import get_student_profile
    from .research_opportunity_db import get_opportunity
    from .matching_engine_8 import score_student_against_opportunity, generate_fit_explanation

    profile = get_student_profile(student_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No student profile found - create one first")
    opportunity = get_opportunity(opportunity_id, _resolved_db_path())
    if not opportunity:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)

    match = score_student_against_opportunity(profile, opportunity)
    return generate_fit_explanation(
        student_id, profile, opportunity, match, use_claude=True, db_path=_resolved_db_path(),
    )


@app.post(
    "/student-dashboard/{student_id}/opportunity-matches/{opportunity_id}/express-interest",
    tags=["MatchingEngine8"],
    summary="Express interest in a research opportunity (idempotent - re-expressing returns the existing interest)",
)
async def express_interest_endpoint(student_id: str, opportunity_id: str, message: str = ""):
    from .research_opportunity_db import get_opportunity, get_cached_opportunity_match, express_interest
    from .patent_marketplace_db import get_student_profile

    if not get_student_profile(student_id, _resolved_db_path()):
        raise api_error(ErrorCode.MISSING_INPUT, "No student profile found - create one first")
    if not get_opportunity(opportunity_id, _resolved_db_path()):
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)

    cached = get_cached_opportunity_match(student_id, opportunity_id, _resolved_db_path())
    match_score = cached["match_score"] if cached else None
    interest = express_interest(student_id, opportunity_id, message, match_score, _resolved_db_path())
    return {
        "interest_id": interest["interest_id"],
        "status": interest["status"],
        "already_interested": interest["already_interested"],
    }


@app.get(
    "/student-dashboard/{student_id}/opportunity-interests", tags=["MatchingEngine8"],
    summary="A student's expressed-interest list",
)
async def get_student_opportunity_interests(student_id: str):
    from .research_opportunity_db import list_interests_for_student
    interests = list_interests_for_student(student_id, _resolved_db_path())
    return {"student_id": student_id, "count": len(interests), "interests": interests}


@app.get(
    "/professor/{professor_id}/research-opportunities/{opportunity_id}/candidates",
    tags=["MatchingEngine8"],
    summary="AI-ranked candidate students for one research opportunity, with filters",
)
async def get_opportunity_candidates(
    professor_id: str,
    opportunity_id: str,
    min_match_score: Optional[float] = None,
    degree_level: Optional[str] = None,
    research_area: Optional[str] = None,
    skill: Optional[str] = None,
    university: Optional[str] = None,
    location: Optional[str] = None,
):
    from .research_opportunity_db import get_opportunity, list_opportunity_matches_for_opportunity
    from .patent_marketplace_db import list_student_profiles

    opportunity = get_opportunity(opportunity_id, _resolved_db_path())
    if not opportunity:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)

    students = list_student_profiles(_resolved_db_path())
    _ensure_fresh_candidate_scores(opportunity, students)

    students_by_id = {s["user_id"]: s for s in students}
    candidates = []
    for score in list_opportunity_matches_for_opportunity(opportunity_id, _resolved_db_path()):
        student = students_by_id.get(score["student_id"])
        if not student:
            continue
        if min_match_score is not None and score["match_score"] < min_match_score:
            continue
        if degree_level and degree_level.lower() not in " ".join(student.get("education") or []).lower():
            continue
        if research_area and not any(
            research_area.lower() in str(a).lower() for a in (student.get("research_areas") or [])
        ):
            continue
        if skill and not any(skill.lower() in str(s).lower() for s in (student.get("skills") or [])):
            continue
        if university and university.lower() not in (student.get("institute") or "").lower():
            continue
        # student_profiles carries no location field today - "location" is
        # accepted for API-shape completeness (matches the requested filter
        # list) but is a deliberate no-op rather than silently mismatching.
        candidates.append({
            **score,
            "student_name": student.get("name", ""),
            "institute": student.get("institute", ""),
            "field_of_study": student.get("field_of_study", ""),
            "skills": student.get("skills", []),
            "education": student.get("education", []),
            "bio": student.get("bio", ""),
            "resume_file_path": student.get("resume_file_path", ""),
        })

    return {"opportunity_id": opportunity_id, "count": len(candidates), "candidates": candidates}


def _suggest_keyword_updates(candidate_scores: List[Dict[str, Any]]) -> List[str]:
    """Pure aggregation, no LLM call: which skills recur as 'missing' across
    near-miss candidates (40-65% match) - a deterministic, always-available
    companion to the Claude-backed fit explanations, satisfying 'suggested
    keywords/qualification updates' without needing an API key."""
    from collections import Counter
    near_miss = [c for c in candidate_scores if 40 <= c["match_score"] < 65]
    counter: Counter = Counter()
    for c in near_miss:
        for skill in c.get("missing_skills", []):
            counter[skill] += 1
    return [skill for skill, _ in counter.most_common(8)]


@app.get(
    "/professor/{professor_id}/research-opportunities/{opportunity_id}/insights",
    tags=["MatchingEngine8"],
    summary="AI insights: top candidates, near-miss students, strength summaries, suggested keyword updates",
)
async def get_opportunity_insights(professor_id: str, opportunity_id: str):
    from .research_opportunity_db import get_opportunity, list_opportunity_matches_for_opportunity
    from .patent_marketplace_db import list_student_profiles, get_student_profile
    from .matching_engine_8 import score_student_against_opportunity, generate_fit_explanation

    opportunity = get_opportunity(opportunity_id, _resolved_db_path())
    if not opportunity:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)

    students = list_student_profiles(_resolved_db_path())
    _ensure_fresh_candidate_scores(opportunity, students)

    scores = list_opportunity_matches_for_opportunity(opportunity_id, _resolved_db_path())
    scores.sort(key=lambda s: -s["match_score"])

    top_candidates = scores[:5]
    near_miss_students = [s for s in scores if 40 <= s["match_score"] < 65][:5]

    strength_summaries: Dict[str, str] = {}
    for score in top_candidates[:3]:
        student = get_student_profile(score["student_id"], _resolved_db_path())
        if not student:
            continue
        match = score_student_against_opportunity(student, opportunity)
        explanation = generate_fit_explanation(
            score["student_id"], student, opportunity, match, use_claude=True, db_path=_resolved_db_path(),
        )
        strength_summaries[score["student_id"]] = explanation.get("summary", "")

    return {
        "opportunity_id": opportunity_id,
        "top_candidates": top_candidates,
        "near_miss_students": near_miss_students,
        "strength_summaries": strength_summaries,
        "suggested_keyword_updates": _suggest_keyword_updates(scores),
    }


class InviteStudentInput(BaseModel):
    student_id: str = Field(..., max_length=64)
    message: str = Field("", max_length=2000)


@app.post(
    "/professor/{professor_id}/research-opportunities/{opportunity_id}/invite",
    tags=["MatchingEngine8"],
    summary="Invite/contact a candidate student for a research opportunity",
)
async def invite_student(professor_id: str, opportunity_id: str, payload: InviteStudentInput):
    from .research_opportunity_db import get_opportunity, get_cached_opportunity_match, create_invitation
    from .patent_marketplace_db import get_student_profile

    opportunity = get_opportunity(opportunity_id, _resolved_db_path())
    if not opportunity:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)
    student = get_student_profile(payload.student_id, _resolved_db_path())
    if not student:
        raise api_error(ErrorCode.BUYER_NOT_FOUND, "No student profile found for this id")

    cached = get_cached_opportunity_match(payload.student_id, opportunity_id, _resolved_db_path())
    invitation_id = create_invitation({
        "opportunity_id": opportunity_id,
        "opportunity_title": opportunity.get("title", ""),
        "professor_id": professor_id,
        "professor_name": opportunity.get("professor_name", ""),
        "student_id": payload.student_id,
        "student_name": student.get("name", ""),
        "match_score": cached["match_score"] if cached else None,
        "score_breakdown": {
            k: cached[k] for k in (
                "skills_score", "semantic_score", "research_fit_score",
                "experience_score", "qualifications_score", "keywords_score",
            )
        } if cached else {},
        "reasons": cached["reasons"] if cached else [],
        "message": payload.message,
    }, _resolved_db_path())
    return {"invitation_id": invitation_id, "status": "sent"}


@app.get(
    "/professor/{professor_id}/invitations-sent", tags=["MatchingEngine8"],
    summary="Invitations a professor has sent to candidate students",
)
async def get_professor_invitations_sent(professor_id: str):
    from .research_opportunity_db import list_invitations_sent
    invitations = list_invitations_sent(professor_id, _resolved_db_path())
    return {"professor_id": professor_id, "count": len(invitations), "invitations": invitations}


@app.get(
    "/student-dashboard/{student_id}/invitations-received", tags=["MatchingEngine8"],
    summary="Invitations a student has received from professors",
)
async def get_student_invitations_received(student_id: str):
    from .research_opportunity_db import list_invitations_received
    invitations = list_invitations_received(student_id, _resolved_db_path())
    return {"student_id": student_id, "count": len(invitations), "invitations": invitations}


# ═══════════════════════════════════════════════════════════════════════════
# Employee Dashboard: Employee -> Patents & Professors (Matching Engine 7)
# ═══════════════════════════════════════════════════════════════════════════

openapi_tags.append({
    "name": "EmployeeDashboard",
    "description": (
        "AI Patent Marketplace for employees: build a profile (optionally "
        "from an uploaded resume), get AI-ranked patents/professors to buy "
        "or license via Engine 7 (shared with the Student Dashboard, called "
        "with buyer_type=\"employee\"), view AI skill-gap analysis and "
        "business/innovation opportunity insights, and track saved/purchased/"
        "licensed patents. Buy/license/save actions, startup-insights, and "
        "notifications reuse the same buyer-type-agnostic endpoints as the "
        "Student Dashboard."
    ),
})


@app.get("/marketplace/profiles/employee/{user_id}", tags=["EmployeeDashboard"], summary="Fetch one employee profile")
async def get_employee_profile_detail(user_id: str):
    from .patent_marketplace_db import get_employee_profile
    profile = get_employee_profile(user_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.BUYER_NOT_FOUND, "No employee profile found for this id")
    return profile


@app.get(
    "/marketplace/profiles/employee/{user_id}/resume/download",
    tags=["EmployeeDashboard"],
    summary="Download/preview an employee's uploaded resume file",
)
async def download_employee_resume(user_id: str):
    from .patent_marketplace_db import get_employee_profile
    from . import resume_storage

    profile = get_employee_profile(user_id, _resolved_db_path())
    if not profile or not profile.get("resume_file_path"):
        raise api_error(ErrorCode.RESUME_NOT_FOUND)

    path = resume_storage.resolve_resume_path(profile["resume_file_path"])
    if not path:
        raise api_error(ErrorCode.RESUME_NOT_FOUND)

    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    download_name = profile.get("resume_filename") or f"resume{path.suffix}"
    return FileResponse(path, filename=download_name, media_type=media_type)


@app.post(
    "/marketplace/profiles/employee/resume",
    tags=["EmployeeDashboard"],
    summary="Upload a resume (PDF/DOCX/TXT) and get AI-suggested profile fields",
    description=(
        "Extracts text from the uploaded file and parses it (Claude, falling "
        "back to rule-based extraction) into suggested profile fields. "
        "Does NOT save the profile - the employee reviews/edits the "
        "suggestions before calling POST /marketplace/profiles/employee."
    ),
)
async def upload_employee_resume(file: UploadFile = File(...), user_id: str = Form("")):
    from .resume_parser import extract_text_from_file, parse_resume, UnsupportedResumeFormat, is_extraction_too_sparse
    from . import resume_storage

    content = await file.read()
    if len(content) > resume_storage.MAX_RESUME_BYTES:
        raise api_error(
            ErrorCode.FILE_TOO_LARGE,
            f"Resume file is too large ({len(content) / 1_000_000:.1f} MB) - the limit is "
            f"{resume_storage.MAX_RESUME_BYTES // 1_000_000} MB.",
        )

    try:
        text = extract_text_from_file(file.filename or "", content)
    except UnsupportedResumeFormat as e:
        raise api_error(ErrorCode.INVALID_REQUEST, str(e))
    except Exception:
        raise api_error(
            ErrorCode.INVALID_REQUEST,
            "Couldn't read this file - it may be corrupted or not a valid PDF/DOCX. Please try "
            "re-exporting it or fill in your profile manually instead.",
        )

    if is_extraction_too_sparse(text):
        raise api_error(
            ErrorCode.INVALID_REQUEST,
            "Couldn't extract readable text from this file (it may be a scanned/image-only "
            "document). Please fill in your profile manually instead.",
        )

    resume_file_path = ""
    try:
        resume_file_path = resume_storage.save_resume_file(user_id or "resume", file.filename or "", content)
    except resume_storage.ResumeFileTooLarge as e:
        raise api_error(ErrorCode.FILE_TOO_LARGE, str(e))
    except resume_storage.UnsupportedResumeFileType:
        pass  # text was still extracted fine (e.g. a .txt upload) - just nothing to preview/download

    parsed = parse_resume(text, use_claude=True)
    result = parsed.to_dict()
    result["resume_filename"] = file.filename or ""
    result["resume_text"] = text[:20000]
    result["resume_file_path"] = resume_file_path
    return result


def _employee_profile_completion(profile: Dict[str, Any]) -> float:
    fields = [
        "name", "company_name", "job_title", "industry", "bio", "skills",
        "interests", "education", "projects", "certifications",
        "career_goals", "preferred_domains",
    ]
    filled = sum(1 for f in fields if profile.get(f))
    return round(100.0 * filled / len(fields), 1)


class EmployeeRecommendationsInput(BaseModel):
    top_k_patents: int = Field(20, ge=1, le=50)
    top_k_professors: int = Field(10, ge=1, le=30)
    patents_per_professor: int = Field(3, ge=1, le=10)


@app.post(
    "/employee-dashboard/{employee_id}/recommendations",
    tags=["EmployeeDashboard"],
    summary="AI-ranked patents + professors for an employee (Engine 7)",
)
async def get_employee_recommendations(employee_id: str, payload: EmployeeRecommendationsInput):
    from .patent_marketplace_db import get_employee_profile
    from .marketplace_db import list_active_listings
    from .matching_engine_7 import match_patents_for_buyer, match_professors_for_buyer

    profile = get_employee_profile(employee_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No employee profile found - create one first")

    listings = list_active_listings(db_path=_resolved_db_path())
    recommended_patents = match_patents_for_buyer(
        "employee", profile, listings, professors_by_id_cache, top_k=payload.top_k_patents,
    )
    recommended_professors = match_professors_for_buyer(
        "employee", profile, listings, professors_by_id_cache,
        top_k=payload.top_k_professors, patents_per_professor=payload.patents_per_professor,
    )
    return {
        "employee_id": employee_id,
        "recommended_patents": [p.to_dict() for p in recommended_patents],
        "recommended_professors": [p.to_dict() for p in recommended_professors],
    }


@app.get(
    "/employee-dashboard/{employee_id}/overview",
    tags=["EmployeeDashboard"],
    summary="Dashboard overview stats for an employee",
)
async def get_employee_overview(employee_id: str):
    from .patent_marketplace_db import get_employee_profile, list_wishlist_items, list_transactions_for_buyer
    from .marketplace_db import list_active_listings
    from .matching_engine_7 import match_patents_for_buyer, match_professors_for_buyer

    profile = get_employee_profile(employee_id, _resolved_db_path())
    completion = _employee_profile_completion(profile) if profile else 0.0

    recommended_patent_count = 0
    recommended_professor_count = 0
    avg_match_score = 0.0
    if profile:
        listings = list_active_listings(db_path=_resolved_db_path())
        patents = match_patents_for_buyer("employee", profile, listings, professors_by_id_cache, top_k=20)
        professors = match_professors_for_buyer("employee", profile, listings, professors_by_id_cache, top_k=10)
        recommended_patent_count = len(patents)
        recommended_professor_count = len(professors)
        if patents:
            avg_match_score = round(sum(p.match_score for p in patents) / len(patents), 1)

    saved_count = len(list_wishlist_items("employee", employee_id, _resolved_db_path()))
    transactions = list_transactions_for_buyer("employee", employee_id, _resolved_db_path())

    notifications_count = 0
    try:
        notif = await tech_transfer_notifications("employee", employee_id)
        notifications_count = notif.get("count", 0)
    except Exception:
        pass

    return {
        "employee_id": employee_id,
        "profile_completion_pct": completion,
        "avg_match_score": avg_match_score,
        "recommended_patent_count": recommended_patent_count,
        "recommended_professor_count": recommended_professor_count,
        "saved_patents_count": saved_count,
        "purchased_licensed_count": len(transactions),
        "notifications_count": notifications_count,
    }


@app.get(
    "/employee-dashboard/{employee_id}/patents/{listing_id}",
    tags=["EmployeeDashboard"],
    summary="Patent detail + this employee's AI compatibility score",
)
async def get_employee_patent_detail(employee_id: str, listing_id: str):
    from .marketplace_db import get_listing
    from .patent_marketplace_db import get_employee_profile
    from .matching_engine_7 import match_patents_for_buyer

    listing = get_listing(listing_id, _resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)

    compatibility = None
    profile = get_employee_profile(employee_id, _resolved_db_path())
    if profile:
        matches = match_patents_for_buyer("employee", profile, [listing], professors_by_id_cache, top_k=1)
        if matches:
            compatibility = matches[0].to_dict()

    return {"listing": listing, "compatibility": compatibility}


@app.get(
    "/employee-dashboard/{employee_id}/skill-gap/{listing_id}",
    tags=["EmployeeDashboard"],
    summary="AI skill-gap analysis for an employee against one patent",
)
async def get_employee_skill_gap(employee_id: str, listing_id: str):
    from .marketplace_db import get_listing
    from .patent_marketplace_db import get_employee_profile
    from .skill_gap_analyzer import analyze_skill_gap

    profile = get_employee_profile(employee_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No employee profile found - create one first")
    listing = get_listing(listing_id, _resolved_db_path())
    if not listing:
        raise api_error(ErrorCode.LISTING_NOT_FOUND)

    result = analyze_skill_gap(employee_id, profile, listing, use_claude=True, db_path=_resolved_db_path())
    return result.to_dict()


@app.get(
    "/employee-dashboard/{employee_id}/purchased-licensed",
    tags=["EmployeeDashboard"],
    summary="An employee's purchased and licensed patents",
)
async def get_employee_transactions(employee_id: str):
    from .patent_marketplace_db import list_transactions_for_buyer
    transactions = list_transactions_for_buyer("employee", employee_id, _resolved_db_path())
    return {"employee_id": employee_id, "count": len(transactions), "transactions": transactions}


# ─── AI Matching Engine 9, mirrored for the Employee Dashboard ────────
# Identical shape to the Student Dashboard's job-matches endpoints above -
# the scorer (matching_engine_9.py) and cache tables (job_matching_db.py) operate
# on an opaque candidate id and a generic profile dict, so employee_profiles
# (which carries the same skills/education/certifications/work_experience
# shape as student_profiles) plugs in without any engine changes, the same
# way matching_engine_7 already shares one core across both buyer types.

@app.get(
    "/employee-dashboard/{employee_id}/job-matches",
    tags=["EmployeeDashboard"],
    summary="AI-ranked job postings for an employee, auto-refreshed on resume/job changes",
)
async def get_employee_job_matches(
    employee_id: str,
    sort: str = "match",
    employment_type: Optional[str] = None,
    is_remote: Optional[bool] = None,
):
    from .patent_marketplace_db import get_employee_profile
    from .job_matching_db import list_job_postings, list_match_scores_for_student

    profile = get_employee_profile(employee_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No employee profile found - create one first")

    jobs = list_job_postings(
        status="active", employment_type=employment_type, is_remote=is_remote,
        db_path=_resolved_db_path(),
    )
    _ensure_fresh_match_scores(employee_id, profile, jobs)

    scores_by_job = {s["job_id"]: s for s in list_match_scores_for_student(employee_id, _resolved_db_path())}
    jobs_by_id = {j["job_id"]: j for j in jobs}

    matches = []
    for job_id, score in scores_by_job.items():
        job = jobs_by_id.get(job_id)
        if not job:
            continue
        matches.append({
            **score,
            "title": job["title"],
            "company_name": job["company_name"],
            "employment_type": job["employment_type"],
            "is_remote": job["is_remote"],
            "location": job["location"],
            "created_at": job["created_at"],
        })

    if sort == "newest":
        matches.sort(key=lambda m: -m["created_at"])
    else:
        matches.sort(key=lambda m: -m["match_score"])

    return {"employee_id": employee_id, "count": len(matches), "matches": matches}


@app.get(
    "/employee-dashboard/{employee_id}/job-matches/{job_id}/suggestions",
    tags=["EmployeeDashboard"],
    summary="AI suggestions to improve this employee's match with one job",
)
async def get_employee_job_match_suggestions(employee_id: str, job_id: str):
    from .patent_marketplace_db import get_employee_profile
    from .job_matching_db import get_job_posting
    from .matching_engine_9 import score_student_against_job, generate_match_suggestions

    profile = get_employee_profile(employee_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No employee profile found - create one first")
    job = get_job_posting(job_id, _resolved_db_path())
    if not job:
        raise api_error(ErrorCode.JOB_NOT_FOUND)

    job_match = score_student_against_job(profile, job)
    return generate_match_suggestions(
        employee_id, profile, job, job_match, use_claude=True, db_path=_resolved_db_path(),
    )


@app.post(
    "/employee-dashboard/{employee_id}/job-matches/{job_id}/apply",
    tags=["EmployeeDashboard"],
    summary="Apply to a job (idempotent - re-applying returns the existing application)",
)
async def apply_to_job_as_employee(employee_id: str, job_id: str):
    from .job_matching_db import get_job_posting, get_cached_match, create_application
    from .patent_marketplace_db import get_employee_profile

    if not get_employee_profile(employee_id, _resolved_db_path()):
        raise api_error(ErrorCode.MISSING_INPUT, "No employee profile found - create one first")
    if not get_job_posting(job_id, _resolved_db_path()):
        raise api_error(ErrorCode.JOB_NOT_FOUND)

    cached = get_cached_match(employee_id, job_id, _resolved_db_path())
    match_score = cached["match_score"] if cached else None
    application = create_application(employee_id, job_id, match_score, _resolved_db_path())
    return {
        "application_id": application["application_id"],
        "status": application["status"],
        "already_applied": application["already_applied"],
    }


@app.get(
    "/employee-dashboard/{employee_id}/applications",
    tags=["EmployeeDashboard"],
    summary="An employee's job applications",
)
async def get_employee_applications(employee_id: str):
    from .job_matching_db import list_applications_for_student
    applications = list_applications_for_student(employee_id, _resolved_db_path())
    return {"employee_id": employee_id, "count": len(applications), "applications": applications}


# ─── Matching Engine 8 (Research Opportunities), mirrored for the Employee ──
# ─── Dashboard ──────────────────────────────────────────────────────────────
# Identical shape to the Student Dashboard's opportunity-matches endpoints
# above - _ensure_fresh_opportunity_matches and research_opportunity_db
# operate on an opaque candidate id and a generic profile dict, and
# matching_engine_8.py's _profile_domain_areas() falls back to
# industry_expertise/innovation_interests when research_areas is absent (the
# employee_profiles shape), so employee_profiles plugs in without any
# engine changes - the same reuse pattern as Matching Engine 7/9 above.

@app.get(
    "/employee-dashboard/{employee_id}/opportunity-matches", tags=["MatchingEngine8"],
    summary="AI-ranked research opportunities for an employee, auto-refreshed on resume/opportunity changes",
)
async def get_employee_opportunity_matches(
    employee_id: str,
    sort: str = "match",
    opportunity_type: Optional[str] = None,
    degree_level: Optional[str] = None,
):
    from .patent_marketplace_db import get_employee_profile
    from .research_opportunity_db import list_opportunities, list_opportunity_matches_for_student

    profile = get_employee_profile(employee_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No employee profile found - create one first")

    opportunities = list_opportunities(
        status="active", opportunity_type=opportunity_type, degree_level=degree_level,
        db_path=_resolved_db_path(),
    )
    _ensure_fresh_opportunity_matches(employee_id, profile, opportunities)

    scores_by_opp = {s["opportunity_id"]: s for s in list_opportunity_matches_for_student(employee_id, _resolved_db_path())}
    opps_by_id = {o["opportunity_id"]: o for o in opportunities}

    matches = []
    for opp_id, score in scores_by_opp.items():
        opp = opps_by_id.get(opp_id)
        if not opp:
            continue
        matches.append({
            **score,
            "title": opp["title"],
            "professor_name": opp["professor_name"],
            "department": opp["department"],
            "opportunity_type": opp["opportunity_type"],
            "degree_level": opp["degree_level"],
            "duration": opp["duration"],
            "location": opp["location"],
            "is_remote": opp["is_remote"],
            "created_at": opp["created_at"],
        })

    if sort == "newest":
        matches.sort(key=lambda m: -m["created_at"])
    else:
        matches.sort(key=lambda m: -m["match_score"])

    return {"employee_id": employee_id, "count": len(matches), "matches": matches}


@app.get(
    "/employee-dashboard/{employee_id}/opportunity-matches/{opportunity_id}/suggestions",
    tags=["MatchingEngine8"],
    summary="AI suggestions to improve this employee's match with one opportunity",
)
async def get_employee_opportunity_match_suggestions(employee_id: str, opportunity_id: str):
    from .patent_marketplace_db import get_employee_profile
    from .research_opportunity_db import get_opportunity
    from .matching_engine_8 import score_student_against_opportunity, generate_match_suggestions

    profile = get_employee_profile(employee_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No employee profile found - create one first")
    opportunity = get_opportunity(opportunity_id, _resolved_db_path())
    if not opportunity:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)

    match = score_student_against_opportunity(profile, opportunity)
    return generate_match_suggestions(
        employee_id, profile, opportunity, match, use_claude=True, db_path=_resolved_db_path(),
    )


@app.get(
    "/employee-dashboard/{employee_id}/opportunity-matches/{opportunity_id}/fit-explanation",
    tags=["MatchingEngine8"],
    summary="AI-generated natural-language explanation of why this employee fits (or doesn't) this opportunity",
)
async def get_employee_opportunity_fit_explanation(employee_id: str, opportunity_id: str):
    from .patent_marketplace_db import get_employee_profile
    from .research_opportunity_db import get_opportunity
    from .matching_engine_8 import score_student_against_opportunity, generate_fit_explanation

    profile = get_employee_profile(employee_id, _resolved_db_path())
    if not profile:
        raise api_error(ErrorCode.MISSING_INPUT, "No employee profile found - create one first")
    opportunity = get_opportunity(opportunity_id, _resolved_db_path())
    if not opportunity:
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)

    match = score_student_against_opportunity(profile, opportunity)
    return generate_fit_explanation(
        employee_id, profile, opportunity, match, use_claude=True, db_path=_resolved_db_path(),
    )


@app.post(
    "/employee-dashboard/{employee_id}/opportunity-matches/{opportunity_id}/express-interest",
    tags=["MatchingEngine8"],
    summary="Express interest in a research opportunity as an employee (idempotent)",
)
async def express_interest_as_employee(employee_id: str, opportunity_id: str, message: str = ""):
    from .research_opportunity_db import get_opportunity, get_cached_opportunity_match, express_interest
    from .patent_marketplace_db import get_employee_profile

    if not get_employee_profile(employee_id, _resolved_db_path()):
        raise api_error(ErrorCode.MISSING_INPUT, "No employee profile found - create one first")
    if not get_opportunity(opportunity_id, _resolved_db_path()):
        raise api_error(ErrorCode.OPPORTUNITY_NOT_FOUND)

    cached = get_cached_opportunity_match(employee_id, opportunity_id, _resolved_db_path())
    match_score = cached["match_score"] if cached else None
    interest = express_interest(employee_id, opportunity_id, message, match_score, _resolved_db_path())
    return {
        "interest_id": interest["interest_id"],
        "status": interest["status"],
        "already_interested": interest["already_interested"],
    }


@app.get(
    "/employee-dashboard/{employee_id}/opportunity-interests", tags=["MatchingEngine8"],
    summary="An employee's expressed-interest list",
)
async def get_employee_opportunity_interests(employee_id: str):
    from .research_opportunity_db import list_interests_for_student
    interests = list_interests_for_student(employee_id, _resolved_db_path())
    return {"employee_id": employee_id, "count": len(interests), "interests": interests}


# ═══════════════════════════════════════════════════════════════════════════
# TECHNOLOGY TRANSFER HUB — unified negotiation threads, requests, history,
# analytics, notifications, spanning patent_offers + listing_inquiries
# ═══════════════════════════════════════════════════════════════════════════

openapi_tags.append({
    "name": "TechnologyTransfer",
    "description": (
        "Unified Technology Transfer hub: chat-style negotiation threads on "
        "any offer/inquiry, buyer-posted 'Technology Requests' matched "
        "against active listings, cross-type transaction history, "
        "activity analytics, and in-app notifications. Consolidates the "
        "seller-side (Engine 5) and buyer-side (Engine 6) flows into one "
        "surface reachable from every registered user type's dashboard."
    ),
})


class SendMessageInput(BaseModel):
    sender_role: str = Field(..., max_length=32)
    sender_id: str = Field(..., max_length=64)
    sender_name: str = Field("", max_length=200)
    body: str = Field("", max_length=4000)
    counter_price: Optional[float] = Field(None, ge=0)
    counter_terms: Dict[str, Any] = Field(default_factory=dict)


@app.post(
    "/technology-transfer/threads/{thread_type}/{thread_id}/messages",
    tags=["TechnologyTransfer"],
    summary="Send a negotiation message (with optional counter-offer terms)",
)
async def send_negotiation_message(thread_type: str, thread_id: str, payload: SendMessageInput):
    if thread_type not in ("offer", "inquiry"):
        raise api_error(ErrorCode.INVALID_THREAD_TYPE)
    from .patent_marketplace_db import send_message
    message_id = send_message(
        thread_type, thread_id, payload.sender_role, payload.sender_id,
        payload.sender_name, payload.body,
        counter_price=payload.counter_price,
        counter_terms=payload.counter_terms or None,
        db_path=_resolved_db_path(),
    )
    return {"message_id": message_id}


@app.get(
    "/technology-transfer/threads/{thread_type}/{thread_id}/messages",
    tags=["TechnologyTransfer"],
    summary="Read a negotiation thread",
)
async def get_negotiation_messages(thread_type: str, thread_id: str):
    if thread_type not in ("offer", "inquiry"):
        raise api_error(ErrorCode.INVALID_THREAD_TYPE)
    from .patent_marketplace_db import list_messages
    messages = list_messages(thread_type, thread_id, _resolved_db_path())
    return {"thread_type": thread_type, "thread_id": thread_id, "count": len(messages), "messages": messages}


class TechRequestInput(BaseModel):
    requester_type: str = Field(..., max_length=32)
    requester_id: str = Field(..., max_length=64)
    requester_name: str = Field("", max_length=200)
    title: str = Field(..., max_length=200)
    description: str = Field("", max_length=4000)
    keywords: List[str] = Field(default_factory=list, max_length=30)


@app.post("/technology-transfer/requests", tags=["TechnologyTransfer"], summary="Post a Technology Request (\"I need X\")")
async def create_tech_request(payload: TechRequestInput):
    if payload.requester_type not in ("company", "student", "employee", "professor", "institute"):
        raise api_error(ErrorCode.INVALID_TARGET_TYPE)
    from .patent_marketplace_db import create_technology_request
    request_id = create_technology_request(payload.model_dump(), _resolved_db_path())
    return {"request_id": request_id, "status": "open"}


@app.get("/technology-transfer/requests", tags=["TechnologyTransfer"], summary="Browse open Technology Requests")
async def browse_tech_requests(status: Optional[str] = "open"):
    from .patent_marketplace_db import list_technology_requests
    requests = list_technology_requests(status, _resolved_db_path())
    return {"count": len(requests), "requests": requests}


@app.get("/technology-transfer/requests/{request_id}", tags=["TechnologyTransfer"], summary="Fetch one Technology Request")
async def get_tech_request(request_id: str):
    from .patent_marketplace_db import get_technology_request
    req = get_technology_request(request_id, _resolved_db_path())
    if not req:
        raise api_error(ErrorCode.TECH_REQUEST_NOT_FOUND)
    return req


@app.get(
    "/technology-transfer/requests/{request_id}/matches",
    tags=["TechnologyTransfer"],
    summary="AI-match a Technology Request against active listings",
)
async def match_tech_request(request_id: str, top_k: int = 10):
    from .patent_marketplace_db import get_technology_request
    req = get_technology_request(request_id, _resolved_db_path())
    if not req:
        raise api_error(ErrorCode.TECH_REQUEST_NOT_FOUND)

    from .marketplace_db import list_active_listings
    from .matching_engine_5 import match_technology_request_to_listings
    listings = list_active_listings(db_path=_resolved_db_path())
    matches = match_technology_request_to_listings(
        req["title"], req["description"], req["keywords"], listings,
        professor_lookup=professors_by_id_cache, top_k=top_k,
    )
    return {"request_id": request_id, "count": len(matches), "matches": [m.to_dict() for m in matches]}


class CloseTechRequestInput(BaseModel):
    status: str = Field(..., max_length=16)


@app.post("/technology-transfer/requests/{request_id}/close", tags=["TechnologyTransfer"], summary="Close/fulfill a Technology Request")
async def close_tech_request(request_id: str, payload: CloseTechRequestInput):
    if payload.status not in ("fulfilled", "closed"):
        raise api_error(ErrorCode.INVALID_REQUEST, "status must be fulfilled or closed")
    from .patent_marketplace_db import close_technology_request
    req = close_technology_request(request_id, payload.status, _resolved_db_path())
    if not req:
        raise api_error(ErrorCode.TECH_REQUEST_NOT_FOUND)
    return req


@app.get(
    "/technology-transfer/history",
    tags=["TechnologyTransfer"],
    summary="Unified transaction/negotiation history for one role (both sent and received, offers and inquiries)",
)
async def tech_transfer_history(role_type: str, role_id: str):
    from .patent_marketplace_db import (
        list_offers_sent, list_offers_received,
        list_listing_inquiries_for_buyer, list_listing_inquiries_for_professor,
    )
    db_path = _resolved_db_path()
    items: List[Dict[str, Any]] = []
    if role_type == "professor":
        for o in list_offers_sent(role_id, db_path):
            items.append({**o, "kind": "patent_offer", "direction": "sent"})
        for i in list_listing_inquiries_for_professor(role_id, db_path):
            items.append({**i, "kind": "listing_inquiry", "direction": "received"})
    for o in list_offers_received(role_type, role_id, db_path):
        items.append({**o, "kind": "patent_offer", "direction": "received"})
    for i in list_listing_inquiries_for_buyer(role_type, role_id, db_path):
        items.append({**i, "kind": "listing_inquiry", "direction": "sent"})
    items.sort(key=lambda x: -x["created_at"])
    return {"role_type": role_type, "role_id": role_id, "count": len(items), "history": items}


@app.get(
    "/technology-transfer/analytics",
    tags=["TechnologyTransfer"],
    summary="Buying/selling/licensing activity analytics for one role",
)
async def tech_transfer_analytics(role_type: str, role_id: str):
    from .patent_marketplace_db import (
        list_offers_sent, list_offers_received,
        list_listing_inquiries_for_buyer, list_listing_inquiries_for_professor,
    )
    db_path = _resolved_db_path()
    offers_sent = list_offers_sent(role_id, db_path) if role_type == "professor" else []
    inquiries_received = list_listing_inquiries_for_professor(role_id, db_path) if role_type == "professor" else []
    offers_received = list_offers_received(role_type, role_id, db_path)
    inquiries_sent = list_listing_inquiries_for_buyer(role_type, role_id, db_path)

    all_items = offers_sent + inquiries_received + offers_received + inquiries_sent
    accepted = [x for x in all_items if x["status"] == "accepted"]
    declined = [x for x in all_items if x["status"] == "declined"]
    pending = [x for x in all_items if x["status"] in ("sent", "negotiating", "viewed")]

    listings_for_sale = 0
    if role_type == "professor":
        from .marketplace_db import list_listings_for_professor
        listings_for_sale = len(list_listings_for_professor(role_id, db_path))

    return {
        "role_type": role_type,
        "role_id": role_id,
        "listings_for_sale": listings_for_sale,
        "offers_sent": len(offers_sent),
        "offers_received": len(offers_received),
        "inquiries_sent": len(inquiries_sent),
        "inquiries_received": len(inquiries_received),
        "accepted_count": len(accepted),
        "declined_count": len(declined),
        "pending_count": len(pending),
    }


@app.get(
    "/technology-transfer/notifications",
    tags=["TechnologyTransfer"],
    summary="In-app notifications: pending negotiations + newly matching listings (last 7 days)",
)
async def tech_transfer_notifications(role_type: str, role_id: str):
    import time as _time
    if role_type not in ("company", "student", "employee", "professor", "institute"):
        raise api_error(ErrorCode.INVALID_TARGET_TYPE)
    db_path = _resolved_db_path()
    cutoff = _time.time() - 7 * 86400
    notifications: List[Dict[str, Any]] = []

    if role_type == "professor":
        from .patent_marketplace_db import list_listing_inquiries_for_professor
        for i in list_listing_inquiries_for_professor(role_id, db_path):
            if i["status"] == "sent":
                notifications.append({
                    "type": "listing_inquiry",
                    "message": f"New inquiry on \"{i['listing_title']}\" from {i['buyer_name'] or i['buyer_id']}",
                    "created_at": i["created_at"],
                })

    from .patent_marketplace_db import list_offers_received
    for o in list_offers_received(role_type, role_id, db_path):
        if o["status"] == "sent":
            notifications.append({
                "type": "patent_offer",
                "message": f"New patent offer: \"{o['patent_title']}\" from {o['professor_name']}",
                "created_at": o["created_at"],
            })

    buyer_profile = _get_buyer_profile(role_type, role_id)
    if buyer_profile:
        from .marketplace_db import list_active_listings
        from .matching_engine_5 import match_buyer_to_listings
        recent_listings = [
            l for l in list_active_listings(db_path=db_path)
            if (l.get("activated_at") or 0) >= cutoff
        ]
        if recent_listings:
            matches = match_buyer_to_listings(
                role_type, buyer_profile, recent_listings, top_k=5,
                professor_lookup=professors_by_id_cache,
            )
            for m in matches:
                if m.score >= 20:
                    notifications.append({
                        "type": "new_listing_match",
                        "message": f"New match: \"{m.target_name}\" (score {round(m.score)})",
                        "created_at": None,
                    })

    notifications.sort(key=lambda n: -(n["created_at"] or 0))
    return {"role_type": role_type, "role_id": role_id, "count": len(notifications), "notifications": notifications}


# ─── Detailed health ───────────────────────────────────────────────────────

@app.get("/health/deep", tags=["Health"], summary="Component-by-component health report")
async def health_deep():
    import shutil, os as _os
    db_path = _resolved_db_path()
    components: dict = {}

    components["database"] = {
        "status": "ok" if Path(db_path).exists() else "missing",
        "path": db_path,
    }
    components["engine"] = {
        "status": "ok" if engine else "not_initialized",
        "professor_count": len(engine.professors) if engine else 0,
    }
    components["embeddings"] = {
        "status": "ok" if (engine and engine.embedding_engine and engine.embedding_engine.is_ready) else "disabled",
    }
    components["anthropic"] = {
        "status": "configured" if os.environ.get("ANTHROPIC_API_KEY") else "missing",
    }
    try:
        usage = shutil.disk_usage(str(Path(__file__).parent.parent))
        components["disk"] = {
            "status": "ok" if usage.free > 1_000_000_000 else "low",
            "free_gb": round(usage.free / 1e9, 1),
            "total_gb": round(usage.total / 1e9, 1),
        }
    except Exception as e:
        components["disk"] = {"status": "error", "error": str(e)}
    try:
        import resource  # POSIX only
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        components["memory"] = {"status": "ok", "rss_mb": round(rss_kb / 1024, 1)}
    except Exception:
        components["memory"] = {"status": "unknown"}

    overall = "ok" if all(c.get("status") in ("ok", "configured", "disabled") for c in components.values()) else "degraded"
    return {"status": overall, "components": components}
