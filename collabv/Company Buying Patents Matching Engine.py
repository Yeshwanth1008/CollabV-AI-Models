"""
CollabV AI - Matching Engine 4: Company (Profile + Problem Statement) -> Professors & Patents
==================================================================================================
Given a company, rank BOTH:
  - Professor Profiles  (NEW - reuses Model 1's MatchingEngine.match(), the
    platform's original and most mature company<->professor scoring
    pipeline: 3-tier keyword+dense-embedding scoring + patent score +
    readiness score + innovation score)
  - Patents              (existing - scores every scraped patent directly,
    via patent_scorer.PatentScorer.score_relevance)

...against a COMBINED company query built from two sources:
  - Company Problem Statement (the 50-item compendium - existing)
  - Company Profile (NEW - registered company data: industry, tech stack,
    strategic goals, etc. - see patent_marketplace_db.company_profiles)

Priority when building the combined query (see build_combined_request):
  - both profile and problem statement available -> merged (preferred)
  - only one available -> that one alone
  - neither -> raises ValueError (caller should not have gotten this far)

This means a company gets relevant recommendations even if it has never
posted a problem statement, as long as it has a registered profile.

Run standalone:
    python -m collabv.matching_engine_4 --problem PS-01
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .patent_scorer import PatentScorer
from .matching_engine_5 import ProblemStatement, load_problem_statements, patent_id, _build_reasons
from . import patent_problem_db as ppdb
from . import patent_marketplace_db as pmdb


# ─── Company Profile adapter + combined-request builder ────────────────────

def _company_profile_as_request(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a company_profiles row as a company_request-like dict (same
    convention as ProblemStatement.as_request() / matching_engine_5's
    per-type adapters) so it can feed both PatentScorer and Model 1."""
    return {
        "project_description": " ".join(filter(None, [
            profile.get("description", ""),
            profile.get("business_objectives", ""),
            profile.get("strategic_goals", ""),
        ])),
        "challenges": profile.get("innovation_challenges", ""),
        "industry": profile.get("industry", "") or profile.get("business_domain", ""),
        "technical_area": (profile.get("focus_areas") or []) + (profile.get("research_interests") or []),
        "required_expertise": (profile.get("technologies_used") or []) + (profile.get("keywords") or []),
        "tech_stack": profile.get("tech_stack") or [],
        "collaboration_type": ", ".join(profile.get("preferred_collaboration_areas") or []),
    }


def _merge_requests(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Merge two request-shaped dicts: concatenate list fields (de-duped),
    join text fields with a space."""
    def _merge_list(x, y):
        return list(dict.fromkeys([*(x or []), *(y or [])]))

    def _merge_text(x, y):
        return " ".join(filter(None, [x, y]))

    return {
        "project_description": _merge_text(a.get("project_description"), b.get("project_description")),
        "challenges": _merge_text(a.get("challenges"), b.get("challenges")),
        "industry": a.get("industry") or b.get("industry") or "",
        "technical_area": _merge_list(a.get("technical_area"), b.get("technical_area")),
        "required_expertise": _merge_list(a.get("required_expertise"), b.get("required_expertise")),
        "tech_stack": _merge_list(a.get("tech_stack"), b.get("tech_stack")),
        "collaboration_type": a.get("collaboration_type") or b.get("collaboration_type") or "",
    }


def build_combined_request(
    company_id: Optional[str] = None,
    problem_statement_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Combine a company's registered profile and/or a selected problem
    statement into ONE request dict, per the priority rule: both (merged,
    preferred) > profile only > problem statement only. Raises ValueError
    if neither source is available."""
    profile_request: Optional[Dict[str, Any]] = None
    if company_id:
        profile = pmdb.get_company_profile(company_id, db_path)
        if profile:
            profile_request = _company_profile_as_request(profile)

    problem_request: Optional[Dict[str, Any]] = None
    if problem_statement_id:
        problems = {p.id: p for p in load_problem_statements(db_path)}
        problem = problems.get(problem_statement_id)
        if problem:
            problem_request = problem.as_request()

    if profile_request and problem_request:
        return _merge_requests(profile_request, problem_request)
    if profile_request:
        return profile_request
    if problem_request:
        return problem_request
    raise ValueError("Neither a company profile nor a problem statement was found - nothing to match on")


# ─── Recommended Patents (existing direction, generalized to any request) ──

@dataclass
class ProblemPatentMatch:
    patent_id: str
    patent_title: str
    patent_number: str
    professor_id: str
    professor_name: str
    department: str
    score: float
    matching_keywords: List[str] = field(default_factory=list)
    matching_domains: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patent_id": self.patent_id,
            "patent_title": self.patent_title,
            "patent_number": self.patent_number,
            "professor_id": self.professor_id,
            "professor_name": self.professor_name,
            "department": self.department,
            "score": self.score,
            "matching_keywords": self.matching_keywords,
            "matching_domains": self.matching_domains,
            "reasons": self.reasons,
        }


class ProblemToPatentEngine:
    """Matching Engine 4 (patents side). Scores a combined company request
    against every patent held by every professor, ranking individual
    patents (not portfolios) with professor attribution."""

    def __init__(self, professors: List[Dict[str, Any]], db_path: Optional[str] = None):
        self.professors = professors
        self.scorer = PatentScorer()
        self.db_path = db_path

    def match_request(self, request: Dict[str, Any], top_k: Optional[int] = None) -> List[ProblemPatentMatch]:
        results: List[ProblemPatentMatch] = []

        for prof in self.professors:
            patents = prof.get("patents") or []
            if not patents:
                continue
            professor_id = str(prof.get("professor_id", ""))
            professor_name = str(prof.get("name", ""))
            department = str(prof.get("department", ""))

            for patent in patents:
                rel = self.scorer.score_relevance({"patents": [patent]}, request)
                if rel.relevance_score <= 0:
                    continue
                results.append(ProblemPatentMatch(
                    patent_id=patent_id(patent, professor_id),
                    patent_title=str(patent.get("title", "")),
                    patent_number=str(patent.get("patent_number", "")),
                    professor_id=professor_id,
                    professor_name=professor_name,
                    department=department,
                    score=rel.relevance_score,
                    matching_keywords=rel.matching_keywords,
                    matching_domains=rel.matching_domains,
                    reasons=_build_reasons(rel.matching_domains, rel.matching_keywords),
                ))

        results.sort(key=lambda r: -r.score)
        return results[:top_k] if top_k else results

    def match(self, problem: ProblemStatement, top_k: Optional[int] = None) -> List[ProblemPatentMatch]:
        """Back-compat: score against a single ProblemStatement only."""
        return self.match_request(problem.as_request(), top_k=top_k)


def run_problem_to_patent_match(
    problem_statement_id: str,
    professors: List[Dict[str, Any]],
    top_k: int = 10,
    persist: bool = True,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run Engine 4 for one problem statement and, if persist=True, write the
    top_k matches into patent_smart_matches so the Company Dashboard's
    "Patent Smart Matches" section can read a stable, pre-ranked list."""
    problems = {p.id: p for p in load_problem_statements(db_path)}
    problem = problems.get(problem_statement_id)
    if problem is None:
        raise ValueError(f"Unknown problem_statement_id: {problem_statement_id}")

    engine = ProblemToPatentEngine(professors, db_path)
    results = engine.match(problem, top_k=top_k)

    if persist:
        ppdb.save_smart_matches(
            direction=ppdb.DIRECTION_PROBLEM_TO_PATENT,
            source_id=problem_statement_id,
            model_version="matching_engine_4",
            matches=[
                {
                    "patent_id": r.patent_id,
                    "patent_number": r.patent_number,
                    "patent_title": r.patent_title,
                    "professor_id": r.professor_id,
                    "professor_name": r.professor_name,
                    "department": r.department,
                    "problem_statement_id": problem_statement_id,
                    "match_score": r.score,
                    "score_breakdown": {
                        "matching_keywords": r.matching_keywords,
                        "matching_domains": r.matching_domains,
                    },
                    "reasons": r.reasons,
                }
                for r in results
            ],
            db_path=db_path,
        )

    return [r.to_dict() for r in results]


# ─── Recommended Professors (NEW direction) ────────────────────────────────

def _confidence_from_score(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _suggested_collaboration_type(match_result: Any) -> str:
    """Heuristic label from Model 1's MatchResult sub-scores - not a new
    model, just a readable name for what the score composition implies."""
    if getattr(match_result, "patent_score", 0) >= 50:
        return "Patent Licensing"
    if getattr(match_result, "readiness_score", 0) >= 60:
        return "Research Collaboration"
    if getattr(match_result, "score", 0) >= 70:
        return "Innovation Partnership"
    return "Consulting"


def _matching_patents_for_professor(
    scorer: PatentScorer,
    professor: Dict[str, Any],
    request: Dict[str, Any],
    top_k: int = 3,
) -> List[Dict[str, Any]]:
    """Score one professor's own patents against the request and return the
    top_k most relevant. Used to nest patents under their recommended
    professor (context/collaboration hooks) instead of a separate top-level
    'Recommended Patents' list, so a professor+patent never appears twice."""
    professor_id = str(professor.get("professor_id", ""))
    scored: List[Dict[str, Any]] = []
    for patent in professor.get("patents") or []:
        rel = scorer.score_relevance({"patents": [patent]}, request)
        if rel.relevance_score <= 0:
            continue
        scored.append({
            "patent_id": patent_id(patent, professor_id),
            "patent_title": str(patent.get("title", "")),
            "patent_number": str(patent.get("patent_number", "")),
            "status": patent.get("status", ""),
            "score": round(rel.relevance_score, 1),
            "matching_keywords": rel.matching_keywords,
            "matching_domains": rel.matching_domains,
            "reasons": _build_reasons(rel.matching_domains, rel.matching_keywords),
        })
    scored.sort(key=lambda p: -p["score"])
    return scored[:top_k]


def match_company_to_professors(
    engine: Any,  # collabv.matching_engine.MatchingEngine - avoids a hard import cycle
    request: Dict[str, Any],
    company_id: str,
    company_name: str,
    professors_by_id: Dict[str, Dict[str, Any]],
    top_k: int = 10,
    patents_per_professor: int = 3,
) -> List[Dict[str, Any]]:
    """Rank professor profiles against a combined company request, reusing
    Model 1 (MatchingEngine.match) - the platform's existing, most mature
    company<->professor scoring pipeline (3-tier keyword+dense embeddings +
    patent score + readiness score + innovation score) - rather than
    building a parallel scorer. Enriches each MatchResult with a confidence
    bucket, a suggested collaboration type, profile fields the frontend
    needs (research areas, expertise, publications), and that professor's
    own patents re-scored against the request (nested, not a separate list)."""
    from .matching_engine import CompanyRequest

    company_request = CompanyRequest(
        company_id=company_id,
        company_name=company_name,
        technical_area=request.get("technical_area", []),
        industry=request.get("industry", ""),
        tech_stack=request.get("tech_stack", []),
        required_expertise=request.get("required_expertise", []),
        project_description=request.get("project_description", ""),
        challenges=request.get("challenges", ""),
        collaboration_type=request.get("collaboration_type", ""),
    )
    match_results = engine.match(company_request, top_k=top_k)
    scorer = PatentScorer()

    out: List[Dict[str, Any]] = []
    for mr in match_results:
        prof = professors_by_id.get(str(mr.professor_id), {})
        out.append({
            "professor_id": mr.professor_id,
            "professor_name": mr.professor_name,
            "institution": prof.get("institute") or "IIT Madras",
            "department": mr.department,
            "research_areas": (prof.get("research_areas") or [])[:8],
            "expertise": (prof.get("technical_expertise") or [])[:8],
            "skills": (prof.get("nlp_tags") or [])[:8],
            "publications": (prof.get("publications") or [])[:5],
            "score": round(mr.score, 1),
            "confidence": _confidence_from_score(mr.score),
            "reasons": mr.reasons[:5],
            "suggested_collaboration_type": _suggested_collaboration_type(mr),
            "matching_patents": _matching_patents_for_professor(
                scorer, prof, request, top_k=patents_per_professor,
            ),
        })
    return out


def _main() -> None:
    parser = argparse.ArgumentParser(description="CollabV AI Matching Engine 4: Company -> Professors & Patents")
    parser.add_argument("--problem", required=True, help="problem_statement_id, e.g. PS-01")
    parser.add_argument("--professors-file", default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()

    prof_path = args.professors_file
    if not prof_path:
        for candidate in ("iitm_professors_with_patents.json", "iitm_professors_nlp.json"):
            p = Path(__file__).parent.parent / candidate
            if p.exists():
                prof_path = str(p)
                break
    with open(prof_path, encoding="utf-8") as f:
        professors = json.load(f)

    results = run_problem_to_patent_match(
        args.problem, professors, top_k=args.top_k, persist=not args.no_persist,
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    _main()


__all__ = [
    "ProblemPatentMatch", "ProblemToPatentEngine", "run_problem_to_patent_match",
    "build_combined_request", "match_company_to_professors",
]
