"""
Final ranking-signal combination. Cross-encoder relevance dominates (it's
the most precise signal we have); the rest are secondary tie-breakers /
quality signals — high weight here would let a popular-but-irrelevant
profile outrank a perfectly on-topic one.
"""
import math
from datetime import datetime

WEIGHTS = {
    "rerank": 0.40,
    "semantic": 0.15,
    "keyword": 0.10,
    "skills_match": 0.15,
    "popularity": 0.07,
    "activity": 0.05,
    "completeness": 0.03,
    "freshness": 0.05,
}


def _minmax(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-9:
        return {k: 0.5 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def freshness_score(updated_at: datetime, half_life_days: float = 180.0) -> float:
    if updated_at is None:
        return 0.0
    now = datetime.utcnow()
    age_days = max((now - updated_at).total_seconds() / 86400, 0.0)
    return math.exp(-age_days / half_life_days)  # 1.0 fresh -> ~0.37 at one half-life


def skills_match_score(query_tokens: set[str], skills: list[str], research_areas: list[str]) -> tuple[float, list[str], list[str]]:
    combined = {s.lower() for s in (skills or [])}
    research_lower = {r.lower() for r in (research_areas or [])}
    matched_skills = [s for s in (skills or []) if any(t in s.lower() or s.lower() in t for t in query_tokens)]
    matched_research = [r for r in (research_areas or []) if any(t in r.lower() or r.lower() in t for t in query_tokens)]
    denom = max(len(combined | research_lower), 1)
    score = min((len(matched_skills) + len(matched_research)) / denom * 2.0, 1.0)
    return score, matched_skills, matched_research


def combine_scores(candidates: list[dict]) -> list[dict]:
    """
    candidates: list of dicts with keys rerank, semantic, keyword,
    skills_match, followers, connections, activity_score, profile_completion,
    updated_at. Mutates in place, adding 'matching_score', sorted descending.
    """
    if not candidates:
        return candidates

    popularity_raw = {
        c["id"]: math.log1p(c.get("followers", 0) + c.get("connections", 0)) for c in candidates
    }
    popularity_norm = _minmax(popularity_raw)

    for c in candidates:
        fresh = freshness_score(c.get("updated_at"))
        score = (
            WEIGHTS["rerank"] * c.get("rerank", 0.0)
            + WEIGHTS["semantic"] * c.get("semantic", 0.0)
            + WEIGHTS["keyword"] * c.get("keyword", 0.0)
            + WEIGHTS["skills_match"] * c.get("skills_match", 0.0)
            + WEIGHTS["popularity"] * popularity_norm.get(c["id"], 0.0)
            + WEIGHTS["activity"] * c.get("activity_score", 0.0)
            + WEIGHTS["completeness"] * c.get("profile_completion", 0.0)
            + WEIGHTS["freshness"] * fresh
        )
        c["matching_score"] = round(float(score), 4)
        c["_freshness"] = fresh

    candidates.sort(key=lambda c: c["matching_score"], reverse=True)
    return candidates
