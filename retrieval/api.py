"""
FastAPI server for Faculty Information Retrieval System.
Backend-only service — no UI. Endpoints: /search, /profile, /compare,
/department, /stats, /health. Meant to be called by the CollabV website's
own frontend, or explored via /docs.
"""

import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

from .search_engine import HybridSearchEngine
from .profile_builder import ProfileBuilder
from .cache import ProfileCache

# ── App setup ────────────────────────────────────────────────────────────

app = FastAPI(title="Faculty Information Retrieval System", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Singletons ───────────────────────────────────────────────────────────

search_engine = HybridSearchEngine()
profile_builder = ProfileBuilder()
cache = ProfileCache()

# Stats tracking
_stats = {
    "total_searches": 0,
    "total_profile_fetches": 0,
    "search_times_ms": [],
    "profile_times_ms": [],
    "search_counts": defaultdict(int),
    "start_time": time.time(),
}

BASE_DIR = Path(__file__).resolve().parent


# ── Request models ───────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    professors: List[str]


# ── Endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def service_info():
    """Backend-only service info — no UI is served here. See /docs."""
    return {
        "service": "Faculty Information Retrieval System",
        "version": app.version,
        "docs": "/docs",
        "endpoints": ["/search", "/autocomplete", "/profile", "/profile_by_index",
                      "/compare", "/department", "/departments", "/stats", "/health"],
    }


@app.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    dept: Optional[str] = Query(None, description="Department filter"),
    limit: int = Query(5, ge=1, le=20, description="Max results"),
):
    """
    Hybrid search: BM25 + TF-IDF + name matching.
    Response < 200ms (index only, no API calls).
    """
    start = time.perf_counter()
    results = search_engine.search(query=q, dept_filter=dept, top_k=limit)
    elapsed = (time.perf_counter() - start) * 1000

    _stats["total_searches"] += 1
    _stats["search_times_ms"].append(elapsed)
    _stats["search_counts"][q.lower()] += 1

    results["server_time_ms"] = round(elapsed, 2)
    return results


@app.get("/autocomplete")
async def autocomplete(
    q: str = Query(..., min_length=2, description="Prefix to autocomplete"),
    limit: int = Query(10, ge=1, le=20),
):
    """Name autocomplete suggestions."""
    return search_engine.autocomplete(q, limit=limit)


@app.get("/profile")
async def get_profile(
    name: str = Query(..., description="Professor name"),
    dept: Optional[str] = Query(None, description="Department hint for disambiguation"),
):
    """
    Full enriched profile with API data.
    Checks cache first (< 50ms). Live fetch if not cached (< 2s).
    """
    start = time.perf_counter()

    # Check cache first
    cached = cache.get(name)
    if cached:
        elapsed = (time.perf_counter() - start) * 1000
        cached["_cache_status"] = "hit"
        cached["_response_time_ms"] = round(elapsed, 2)
        _stats["total_profile_fetches"] += 1
        _stats["profile_times_ms"].append(elapsed)
        return cached

    # Search for the professor
    search_results = search_engine.search(query=name, dept_filter=dept, top_k=3)
    results = search_results.get("results", [])
    if not results:
        raise HTTPException(404, f"Professor '{name}' not found")

    top = results[0]
    if top["confidence"] < 0.3:
        raise HTTPException(404, f"No confident match for '{name}'")

    # Check for disambiguation
    if len(results) >= 2 and results[0]["confidence"] - results[1]["confidence"] < 0.1:
        # Return disambiguation info instead
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "disambiguation_needed": True,
            "message": search_results.get("disambiguation", "Multiple matches found"),
            "candidates": results[:5],
            "_response_time_ms": round(elapsed, 2),
        }

    # Build enriched profile
    professor = search_engine.get_professor(top["index"])
    profile = await profile_builder.build_profile_async(professor)

    # Cache it
    cache.save(name, profile)

    elapsed = (time.perf_counter() - start) * 1000
    profile["_cache_status"] = "miss"
    profile["_response_time_ms"] = round(elapsed, 2)

    _stats["total_profile_fetches"] += 1
    _stats["profile_times_ms"].append(elapsed)

    return profile


@app.get("/profile_by_index")
async def get_profile_by_index(index: int = Query(..., ge=0)):
    """Get full profile by professor index (for disambiguation clicks)."""
    professor = search_engine.get_professor(index)
    if not professor:
        raise HTTPException(404, "Professor not found")

    name = professor.get("name", "")
    cached = cache.get(name)
    if cached:
        cached["_cache_status"] = "hit"
        return cached

    profile = await profile_builder.build_profile_async(professor)
    cache.save(name, profile)
    profile["_cache_status"] = "miss"
    return profile


@app.post("/compare")
async def compare_professors(req: CompareRequest):
    """
    Side-by-side comparison of 2-5 professors.
    Returns table data with per-row winners highlighted.
    """
    if len(req.professors) < 2 or len(req.professors) > 5:
        raise HTTPException(400, "Provide 2-5 professor names")

    profiles = []
    for name in req.professors:
        # Try cache first
        cached = cache.get(name)
        if cached:
            profiles.append(cached)
            continue

        # Search and build
        search_results = search_engine.search(query=name, top_k=1)
        results = search_results.get("results", [])
        if not results:
            profiles.append({"name": name, "error": "Not found"})
            continue

        professor = search_engine.get_professor(results[0]["index"])
        profile = await profile_builder.build_profile_async(professor)
        cache.save(name, profile)
        profiles.append(profile)

    # Build comparison table
    comparison = []
    for p in profiles:
        comparison.append({
            "name": p.get("name", p.get("error", "Unknown")),
            "department": p.get("department", ""),
            "designation": p.get("designation", ""),
            "total_publications": p.get("total_publications", 0),
            "total_citations": p.get("total_citations", 0),
            "h_index": p.get("h_index", 0),
            "top_domain": max(p.get("domain_scores", {"N/A": 0}), key=p.get("domain_scores", {"N/A": 0}).get) if p.get("domain_scores") else "N/A",
            "research_areas": p.get("research_areas", [])[:3],
            "collaboration_readiness_score": p.get("collaboration_readiness_score", 0),
        })

    # Find winners per metric
    metrics = ["total_publications", "total_citations", "h_index", "collaboration_readiness_score"]
    winners = {}
    for m in metrics:
        vals = [(i, c.get(m, 0)) for i, c in enumerate(comparison)]
        best = max(vals, key=lambda x: x[1])
        winners[m] = best[0]

    return {
        "professors": comparison,
        "winners": winners,
    }


@app.get("/department")
async def department_list(
    name: str = Query(..., description="Department name"),
    sort: str = Query("name", description="Sort by: name, citations, publications, h_index"),
    seniority: str = Query("all", description="Filter: all, Professor, Associate, Assistant"),
):
    """All professors in a department with sorting and filtering."""
    professors = search_engine.get_department_professors(
        department=name,
        sort_by=sort,
        seniority=seniority,
    )
    return {
        "department": name,
        "count": len(professors),
        "sort": sort,
        "seniority_filter": seniority,
        "professors": professors,
    }


@app.get("/departments")
async def all_departments():
    """List all departments."""
    return {"departments": search_engine.get_all_departments()}


@app.get("/stats")
async def stats():
    """System statistics."""
    cache_stats = cache.stats()
    uptime = time.time() - _stats["start_time"]

    avg_search = (
        sum(_stats["search_times_ms"]) / len(_stats["search_times_ms"])
        if _stats["search_times_ms"] else 0
    )
    avg_profile = (
        sum(_stats["profile_times_ms"]) / len(_stats["profile_times_ms"])
        if _stats["profile_times_ms"] else 0
    )

    # Top 5 most searched
    top_searched = sorted(
        _stats["search_counts"].items(), key=lambda x: x[1], reverse=True
    )[:5]

    return {
        "total_professors_indexed": len(search_engine.professors) if search_engine._loaded else 0,
        "total_searches": _stats["total_searches"],
        "total_profile_fetches": _stats["total_profile_fetches"],
        "avg_search_time_ms": round(avg_search, 2),
        "avg_profile_time_ms": round(avg_profile, 2),
        "cache": cache_stats,
        "top_searched": [{"query": q, "count": c} for q, c in top_searched],
        "uptime_seconds": round(uptime, 0),
    }


@app.get("/health")
async def health():
    """System health check."""
    search_engine.load_indexes()
    return {
        "status": "healthy",
        "professors_loaded": len(search_engine.professors),
        "indexes_ready": search_engine._loaded,
        "cache_entries": cache.stats()["total_cached"],
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.on_event("startup")
async def startup():
    """Pre-load indexes on startup."""
    search_engine.load_indexes()
    print(f"Indexes loaded: {len(search_engine.professors)} professors ready")
