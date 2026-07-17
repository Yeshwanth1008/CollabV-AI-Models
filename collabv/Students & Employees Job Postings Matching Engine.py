"""
CollabV AI - Matching Engine 9: Job Postings (Student/Employee <-> Company)
==============================================================================
Given a student/employee profile (built manually or pre-filled from an
uploaded resume via resume_parser.py) and a company job posting, computes a
multi-factor compatibility score:

    Skills match            30%
    Semantic similarity      25%  (resume text <-> job description, dense embeddings)
    Experience match         15%
    Education match          10%
    Certifications match     10%
    Keywords/domain match    10%

Sibling to matching_engine_7.py / matching_engine_8.py / skill_gap_analyzer.py:
reuses the shared embedder singleton from matching_engine_5.py
(_get_shared_embedder) so the sentence-transformers model is loaded once per
process, not per request. Unlike matching_engine_5's _cached_embeddings,
resume/job text is embedded fresh on every scoring call rather than cached
by id - both a candidate's profile and a job posting can be edited at any
time, and a stale cached vector would silently defeat the auto-refresh-on-
edit requirement this engine exists to satisfy. Re-encoding a handful of
short texts is cheap; what's expensive (and correctly cached) is the model
load itself.

If the embedder isn't ready, the semantic weight is redistributed
proportionally across the other five factors - same fallback idea as
matching_engine_5._score_pool's embed_ready flag.

"AI-generated suggestions to improve match" (skills to learn, resume
suggestions, recommended courses/certs) follows the same Claude-first /
rule-based-fallback shape as skill_gap_analyzer.py, cached via
marketplace_db's marketplace_explanations table with a cache key that folds
in both the profile's and the job's updated_at - editing either naturally
invalidates the cached suggestions.

A handful of generic scoring primitives here (_norm_set, _skills_component,
_degree_level/_DEGREE_LEVELS, _keywords_component, _confidence) are reused
directly by matching_engine_8.py (Research Opportunities) rather than
re-derived, since "does this skill set overlap" or "does this degree clear
the bar" is identical regardless of whether the posting is a job or a
research opportunity.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .matching_engine_5 import _get_shared_embedder
from . import marketplace_db as mdb

_WEIGHTS = {
    "skills": 0.30,
    "semantic": 0.25,
    "experience": 0.15,
    "education": 0.10,
    "certifications": 0.10,
    "keywords": 0.10,
}


@dataclass
class JobMatch:
    job_id: str
    title: str
    company_name: str
    match_score: float
    semantic_score: float
    skills_score: float
    experience_score: float
    education_score: float
    certifications_score: float
    keywords_score: float
    confidence: str
    matching_skills: List[str] = field(default_factory=list)
    missing_skills: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    employment_type: str = ""
    is_remote: bool = False
    location: str = ""
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "company_name": self.company_name,
            "match_score": self.match_score,
            "semantic_score": self.semantic_score,
            "skills_score": self.skills_score,
            "experience_score": self.experience_score,
            "education_score": self.education_score,
            "certifications_score": self.certifications_score,
            "keywords_score": self.keywords_score,
            "confidence": self.confidence,
            "matching_skills": self.matching_skills,
            "missing_skills": self.missing_skills,
            "reasons": self.reasons,
            "employment_type": self.employment_type,
            "is_remote": self.is_remote,
            "location": self.location,
            "created_at": self.created_at,
        }


# ─── Scoring components ─────────────────────────────────────────────────────

def _norm_set(items: Optional[List[Any]]) -> set:
    return {str(s).strip().lower() for s in (items or []) if str(s).strip()}


def _skills_component(
    resume_skills: Optional[List[str]],
    required_skills: Optional[List[str]],
    preferred_skills: Optional[List[str]],
):
    resume_set = _norm_set(resume_skills)
    req_lookup = {s.strip().lower(): s.strip() for s in (required_skills or []) if s.strip()}
    pref_lookup = {s.strip().lower(): s.strip() for s in (preferred_skills or []) if s.strip()}

    def _is_matched(skill_lower: str) -> bool:
        if skill_lower in resume_set:
            return True
        return any(skill_lower in r or r in skill_lower for r in resume_set if len(r) > 2)

    matched_req = {k: v for k, v in req_lookup.items() if _is_matched(k)}
    matched_pref = {k: v for k, v in pref_lookup.items() if _is_matched(k)}

    required_score = 100.0 * len(matched_req) / len(req_lookup) if req_lookup else 100.0
    preferred_score = 100.0 * len(matched_pref) / len(pref_lookup) if pref_lookup else 100.0
    score = (0.7 * required_score + 0.3 * preferred_score) if (req_lookup or pref_lookup) else 50.0

    matching_skills = sorted({*matched_req.values(), *matched_pref.values()})
    missing_skills = sorted(v for k, v in req_lookup.items() if k not in matched_req)
    return round(score, 1), matching_skills, missing_skills


def _experience_component(
    work_experience: Optional[List[str]], internships: Optional[List[str]], min_experience_years: float,
) -> float:
    """Resumes rarely carry structured start/end dates, so this is a
    deliberately simple heuristic: each work/internship entry is treated as
    roughly 0.75 years of experience. Good enough to rank candidates
    relative to each other; not a precise tenure calculation."""
    min_years = float(min_experience_years or 0)
    if min_years <= 0:
        return 100.0
    entries = len(work_experience or []) + len(internships or [])
    estimated_years = entries * 0.75
    if estimated_years >= min_years:
        return 100.0
    return round(100.0 * estimated_years / min_years, 1)


_DEGREE_LEVELS = [
    (3, [r"ph\.?\s?d", r"doctorate"]),
    (2, [r"m\.?\s?tech", r"m\.?\s?sc", r"m\.?\s?s\b", r"master"]),
    (1, [r"b\.?\s?tech", r"b\.?\s?e\b", r"b\.?\s?sc", r"bachelor"]),
]


def _degree_level(text: str) -> int:
    text_lower = (text or "").lower()
    for level, patterns in _DEGREE_LEVELS:
        if any(re.search(p, text_lower) for p in patterns):
            return level
    return 0


def _education_component(education: Optional[List[str]], education_requirement: str) -> float:
    if not education_requirement:
        return 100.0
    required_level = _degree_level(education_requirement)
    resume_level = max((_degree_level(e) for e in (education or [])), default=0)
    if required_level == 0:
        req_terms = set(re.findall(r"[a-z]{4,}", education_requirement.lower()))
        resume_terms = set(re.findall(r"[a-z]{4,}", " ".join(education or []).lower()))
        overlap = req_terms & resume_terms
        return round(100.0 * len(overlap) / len(req_terms), 1) if req_terms else 50.0
    if resume_level >= required_level:
        return 100.0
    if resume_level == 0:
        return 20.0
    return round(100.0 * resume_level / required_level, 1)


def _certifications_component(certifications: Optional[List[str]], certifications_preferred: Optional[List[str]]) -> float:
    if not certifications_preferred:
        return 100.0
    resume_blob = " | ".join(str(c).lower() for c in (certifications or []))
    matched = [c for c in certifications_preferred if str(c).strip().lower() in resume_blob]
    return round(100.0 * len(matched) / len(certifications_preferred), 1)


def _keywords_component(
    preferred_domains: Optional[List[str]], achievements_soft_skills: Optional[List[str]],
    keywords: Optional[List[str]], domain_tags: Optional[List[str]],
) -> float:
    student_terms = _norm_set((preferred_domains or []) + (achievements_soft_skills or []))
    job_terms = _norm_set((keywords or []) + (domain_tags or []))
    if not job_terms:
        return 50.0
    matched = {
        t for t in job_terms
        if t in student_terms or any(t in s or s in t for s in student_terms if len(s) > 2)
    }
    return round(100.0 * len(matched) / len(job_terms), 1)


def _confidence(match_score: float, skills_score: float) -> str:
    if match_score >= 75 and skills_score >= 60:
        return "high"
    if match_score >= 55 or skills_score >= 50:
        return "medium"
    return "low"


def _build_job_reasons(
    matching_skills: List[str], semantic_score: float, experience_score: float, education_score: float,
) -> List[str]:
    reasons: List[str] = []
    if matching_skills:
        reasons.append(f"Matches {len(matching_skills)} required/preferred skill(s): {', '.join(matching_skills[:5])}")
    if semantic_score >= 60:
        reasons.append("Strong overall profile-to-role semantic fit")
    elif semantic_score >= 35:
        reasons.append("Moderate overall profile-to-role semantic fit")
    if experience_score >= 90:
        reasons.append("Meets the experience requirement")
    elif experience_score < 50:
        reasons.append("Experience level is below what this role typically expects")
    if education_score >= 90:
        reasons.append("Meets the education requirement")
    if not reasons:
        reasons.append("Limited overlap with this role's stated requirements")
    return reasons


def _resume_text(profile: Dict[str, Any]) -> str:
    parts = [
        profile.get("bio", ""),
        profile.get("career_goals", ""),
        " ".join(profile.get("skills") or []),
        " ".join(profile.get("projects") or []),
        " ".join(profile.get("work_experience") or []),
        " ".join(profile.get("internships") or []),
        " ".join(profile.get("education") or []),
        " ".join(profile.get("certifications") or []),
        " ".join(profile.get("research_areas") or []),
        " ".join(profile.get("achievements_soft_skills") or []),
        (profile.get("resume_text") or "")[:4000],
    ]
    return "\n".join(p for p in parts if p)


def _job_text(job: Dict[str, Any]) -> str:
    parts = [
        job.get("title", ""),
        job.get("description", ""),
        " ".join(job.get("required_skills") or []),
        " ".join(job.get("preferred_skills") or []),
        " ".join(job.get("keywords") or []),
    ]
    return "\n".join(p for p in parts if p)


# ─── Public scoring API ─────────────────────────────────────────────────────

def score_student_against_all_jobs(profile: Dict[str, Any], jobs: List[Dict[str, Any]]) -> List[JobMatch]:
    if not jobs:
        return []

    resume_text = _resume_text(profile)
    job_texts = [_job_text(j) for j in jobs]

    semantic_scores = [0.0] * len(jobs)
    embed_ready = False
    try:
        embedder = _get_shared_embedder()
        if embedder.is_ready and resume_text.strip():
            resume_vec = embedder.encode(resume_text)
            job_vecs = embedder.encode_batch(job_texts)
            sims = (job_vecs @ resume_vec.T).flatten()
            semantic_scores = [max(0.0, min(100.0, float(s) * 100)) for s in sims]
            embed_ready = True
    except Exception:
        embed_ready = False

    weights = dict(_WEIGHTS)
    if not embed_ready:
        semantic_w = weights.pop("semantic")
        remaining_total = sum(weights.values())
        weights = {k: v + (v / remaining_total) * semantic_w for k, v in weights.items()}

    resume_skills = profile.get("skills") or []
    results: List[JobMatch] = []
    for job, semantic_score in zip(jobs, semantic_scores):
        skills_score, matching_skills, missing_skills = _skills_component(
            resume_skills, job.get("required_skills"), job.get("preferred_skills"),
        )
        experience_score = _experience_component(
            profile.get("work_experience"), profile.get("internships"), job.get("min_experience_years", 0),
        )
        education_score = _education_component(profile.get("education"), job.get("education_requirement", ""))
        certifications_score = _certifications_component(profile.get("certifications"), job.get("certifications_preferred"))
        keywords_score = _keywords_component(
            profile.get("preferred_domains"), profile.get("achievements_soft_skills"),
            job.get("keywords"), job.get("domain_tags"),
        )

        component_scores = {
            "skills": skills_score,
            "semantic": semantic_score,
            "experience": experience_score,
            "education": education_score,
            "certifications": certifications_score,
            "keywords": keywords_score,
        }
        match_score = round(sum(component_scores[k] * w for k, w in weights.items()), 1)
        confidence = _confidence(match_score, skills_score)
        reasons = _build_job_reasons(
            matching_skills, semantic_score if embed_ready else 0.0, experience_score, education_score,
        )

        results.append(JobMatch(
            job_id=job.get("job_id", ""),
            title=job.get("title", ""),
            company_name=job.get("company_name", ""),
            match_score=match_score,
            semantic_score=round(semantic_score, 1) if embed_ready else 0.0,
            skills_score=round(skills_score, 1),
            experience_score=round(experience_score, 1),
            education_score=round(education_score, 1),
            certifications_score=round(certifications_score, 1),
            keywords_score=round(keywords_score, 1),
            confidence=confidence,
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            reasons=reasons,
            employment_type=job.get("employment_type", ""),
            is_remote=bool(job.get("is_remote")),
            location=job.get("location", ""),
            created_at=job.get("created_at", 0.0),
        ))

    results.sort(key=lambda m: -m.match_score)
    return results


def score_student_against_job(profile: Dict[str, Any], job: Dict[str, Any]) -> Optional[JobMatch]:
    results = score_student_against_all_jobs(profile, [job])
    return results[0] if results else None


# ─── AI suggestions to improve match (Claude-first, rule-based fallback) ───

JOB_SUGGESTIONS_PROMPT = """You are a career advisor helping a student improve their fit for a specific job posting.

Student profile:
{profile}

Job posting:
{job}

Current match:
{match}

Return ONLY valid JSON with these fields:
{{
  "skills_to_learn": ["3-6 specific skills that would improve this match"],
  "resume_suggestions": ["2-4 concrete ways to improve the resume itself for this kind of role"],
  "recommended_courses_certs": ["3-5 course or certification names/topics, general enough to search for"]
}}"""


def _profile_summary_for_job(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "skills": profile.get("skills", []),
        "education": profile.get("education", []),
        "projects": profile.get("projects", []),
        "certifications": profile.get("certifications", []),
        "work_experience": profile.get("work_experience", []),
    }


def _suggestions_with_claude(profile: Dict[str, Any], job: Dict[str, Any], job_match: JobMatch) -> Optional[Dict[str, Any]]:
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": JOB_SUGGESTIONS_PROMPT.format(
                    profile=json.dumps(_profile_summary_for_job(profile)),
                    job=json.dumps({
                        "title": job.get("title", ""),
                        "description": (job.get("description") or "")[:1500],
                        "required_skills": job.get("required_skills", []),
                        "preferred_skills": job.get("preferred_skills", []),
                    }),
                    match=json.dumps({
                        "match_score": job_match.match_score,
                        "missing_skills": job_match.missing_skills,
                    }),
                ),
            }],
        )
        content = response.content[0].text
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            return None
        data = json.loads(json_match.group())
        return {
            "skills_to_learn": data.get("skills_to_learn", []),
            "resume_suggestions": data.get("resume_suggestions", []),
            "recommended_courses_certs": data.get("recommended_courses_certs", []),
            "source": "claude",
        }
    except Exception:
        return None


def _suggestions_rule_based(job_match: JobMatch) -> Dict[str, Any]:
    missing = job_match.missing_skills
    return {
        "skills_to_learn": missing or ["No major skill gaps identified from this job's stated requirements"],
        "resume_suggestions": (
            [f"Highlight any hands-on experience with {s}, if you have it" for s in missing[:3]]
            or ["Your resume already covers this role's key requirements well"]
        ),
        "recommended_courses_certs": [f"{s} Fundamentals" for s in missing[:4]] or ["No specific gaps to target right now"],
        "source": "rule",
    }


def generate_match_suggestions(
    student_id: str, profile: Dict[str, Any], job: Dict[str, Any], job_match: JobMatch,
    use_claude: bool = True, db_path: Optional[str] = None,
) -> Dict[str, Any]:
    job_id = job.get("job_id", "")
    profile_version = profile.get("updated_at", 0)
    job_version = job.get("updated_at", 0)
    cache_key = f"job_gap:{student_id}:{job_id}:{profile_version}:{job_version}"

    cached = mdb.get_explanation(cache_key, db_path)
    if cached:
        result = dict(cached["explanation"])
        result["source"] = "cache"
        return result

    result = None
    if use_claude:
        result = _suggestions_with_claude(profile, job, job_match)
    if result is None:
        result = _suggestions_rule_based(job_match)

    mdb.save_explanation(cache_key, "job_gap", student_id, job_id, result, db_path)
    return result


__all__ = [
    "JobMatch", "score_student_against_job", "score_student_against_all_jobs",
    "generate_match_suggestions",
]
