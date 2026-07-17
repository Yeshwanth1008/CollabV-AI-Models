"""
CollabV AI - Matching Engine 8: Research Opportunities (Student <-> Professor)
=================================================================================
Given a student profile and a professor-posted research opportunity (PhD/
Master's position, research internship, RA role, fellowship, etc.), computes
a multi-factor compatibility score - the research-opportunity analog of
Matching Engine 7 (student/employee -> patents/professors) and of the
job-matching engine's matching_engine_9.py, but for a new bidirectional direction:
students see ranked opportunities (like matching_engine_9), AND professors see
ranked candidate students per opportunity they posted (score_students_
against_opportunity - a direction matching_engine_9.py never needed since jobs
are only ever browsed student-first).

Reuses genuinely-identical primitives from matching_engine_9.py (Job
Postings) directly (_skills_component, _degree_level/_DEGREE_LEVELS,
_keywords_component, _confidence, _norm_set) rather than re-deriving them,
and the shared embedder singleton from matching_engine_5.py (loaded once
per process; embeddings are computed FRESH per call, never id-cached, since
both a resume and an opportunity posting are mutable and a stale cached
vector would defeat the auto-refresh-on-edit requirement).

Two AI-generated outputs, both Claude-first / rule-based-fallback / cached
via marketplace_db's marketplace_explanations table (same table matching_engine_9.py
already uses for job_gap: keys, different key prefix here - no new cache
table needed):
  - generate_match_suggestions()  - bullet-list gap-closing tips (clone of
    matching_engine_9.generate_match_suggestions)
  - generate_fit_explanation()    - a natural-language PARAGRAPH explaining
    fit, cloned from explainer.py's MatchExplainer shape (the only existing
    paragraph-generator in the codebase; its prompt is hardcoded to
    professor/company fields so it's cloned here, not called directly)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .matching_engine_5 import _get_shared_embedder
from .matching_engine_9 import _norm_set, _skills_component, _degree_level, _keywords_component, _confidence
from . import marketplace_db as mdb

_WEIGHTS = {
    "skills": 0.30,
    "semantic": 0.20,
    "research_fit": 0.20,
    "experience": 0.15,
    "qualifications": 0.10,
    "keywords": 0.05,
}


@dataclass
class ResearchMatch:
    opportunity_id: str
    title: str
    professor_name: str
    department: str
    opportunity_type: str
    degree_level: str
    match_score: float
    semantic_score: float
    skills_score: float
    research_fit_score: float
    experience_score: float
    qualifications_score: float
    keywords_score: float
    confidence: str
    matching_skills: List[str] = field(default_factory=list)
    missing_skills: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    duration: str = ""
    location: str = ""
    is_remote: bool = False
    created_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "title": self.title,
            "professor_name": self.professor_name,
            "department": self.department,
            "opportunity_type": self.opportunity_type,
            "degree_level": self.degree_level,
            "match_score": self.match_score,
            "semantic_score": self.semantic_score,
            "skills_score": self.skills_score,
            "research_fit_score": self.research_fit_score,
            "experience_score": self.experience_score,
            "qualifications_score": self.qualifications_score,
            "keywords_score": self.keywords_score,
            "confidence": self.confidence,
            "matching_skills": self.matching_skills,
            "missing_skills": self.missing_skills,
            "reasons": self.reasons,
            "duration": self.duration,
            "location": self.location,
            "is_remote": self.is_remote,
            "created_at": self.created_at,
        }


# ─── Scoring components ─────────────────────────────────────────────────────

def _profile_domain_areas(profile: Dict[str, Any]) -> List[str]:
    """Student profiles carry research_areas; employee profiles don't - they
    carry industry_expertise/innovation_interests instead. Falls back to the
    latter so employees aren't unfairly scored as having zero research-area
    overlap on every opportunity, same unification idea as
    skill_gap_analyzer.py's _profile_summary (student research_areas vs.
    employee industry_expertise, one implementation covers both)."""
    areas = profile.get("research_areas")
    if areas:
        return areas
    return (profile.get("industry_expertise") or []) + (profile.get("innovation_interests") or [])


def _research_fit_component(
    student_research_areas: Optional[List[str]], student_education: Optional[List[str]],
    opportunity_research_areas: Optional[List[str]], degree_level: str, education_requirement: str,
) -> float:
    """Blends research-area overlap with a degree-level comparison - PhD >
    Master's > Bachelor's, reusing matching_engine_9's _degree_level ranking since
    the "does this person's academic level clear the bar" question is
    identical whether the bar is a job's education_requirement or an
    opportunity's degree_level."""
    student_areas = _norm_set(student_research_areas)
    opp_areas = _norm_set(opportunity_research_areas)
    if opp_areas:
        matched = {a for a in opp_areas if a in student_areas or any(a in s or s in a for s in student_areas if len(s) > 2)}
        area_score = 100.0 * len(matched) / len(opp_areas)
    else:
        area_score = 50.0

    required_level = _degree_level(degree_level) or _degree_level(education_requirement)
    if required_level == 0:
        degree_score = 50.0
    else:
        student_level = max((_degree_level(e) for e in (student_education or [])), default=0)
        if student_level >= required_level:
            degree_score = 100.0
        elif student_level == 0:
            degree_score = 20.0
        else:
            degree_score = 100.0 * student_level / required_level

    return round(0.5 * area_score + 0.5 * degree_score, 1)


def _research_experience_component(
    publications: Optional[List[str]], projects: Optional[List[str]],
    work_experience: Optional[List[str]], internships: Optional[List[str]],
    publications_expected: bool,
) -> float:
    """Same 'count entries ~= years' heuristic as matching_engine_9's
    _experience_component, but credits publications more heavily - a
    published paper signals more research maturity than a generic
    internship line, and an opportunity that explicitly expects
    publications should weight them more."""
    pub_count = len(publications or [])
    project_count = len(projects or [])
    other_count = len(work_experience or []) + len(internships or [])

    pub_weight = 3.0 if publications_expected else 2.0
    score = pub_count * pub_weight * 12 + project_count * 8 + other_count * 6
    return round(min(100.0, score), 1)


def _qualifications_component(
    certifications: Optional[List[str]], education: Optional[List[str]], achievements_soft_skills: Optional[List[str]],
    required_qualifications: Optional[List[str]], preferred_qualifications: Optional[List[str]],
) -> float:
    blob = " | ".join(str(x).lower() for x in (
        (certifications or []) + (education or []) + (achievements_soft_skills or [])
    ))
    required = [q for q in (required_qualifications or []) if str(q).strip()]
    preferred = [q for q in (preferred_qualifications or []) if str(q).strip()]

    def _match_ratio(quals: List[str]) -> float:
        if not quals:
            return 100.0
        matched = [q for q in quals if str(q).strip().lower() in blob]
        return 100.0 * len(matched) / len(quals)

    if not required and not preferred:
        return 50.0
    return round(0.7 * _match_ratio(required) + 0.3 * _match_ratio(preferred), 1)


def _build_match_reasons(
    matching_skills: List[str], semantic_score: float, research_fit_score: float, experience_score: float,
) -> List[str]:
    reasons: List[str] = []
    if matching_skills:
        reasons.append(f"Matches {len(matching_skills)} required/preferred skill(s): {', '.join(matching_skills[:5])}")
    if research_fit_score >= 75:
        reasons.append("Strong alignment with this opportunity's research areas and academic level")
    elif research_fit_score >= 45:
        reasons.append("Partial alignment with this opportunity's research areas or academic level")
    if semantic_score >= 60:
        reasons.append("Strong overall profile-to-opportunity semantic fit")
    elif semantic_score >= 35:
        reasons.append("Moderate overall profile-to-opportunity semantic fit")
    if experience_score >= 70:
        reasons.append("Solid research experience/publication record for this kind of opportunity")
    if not reasons:
        reasons.append("Limited overlap with this opportunity's stated requirements")
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
        " ".join(profile.get("publications") or []),
        " ".join(_profile_domain_areas(profile)),
        " ".join(profile.get("achievements_soft_skills") or []),
        (profile.get("resume_text") or "")[:4000],
    ]
    return "\n".join(p for p in parts if p)


def _opportunity_text(opportunity: Dict[str, Any]) -> str:
    parts = [
        opportunity.get("title", ""),
        opportunity.get("description", ""),
        " ".join(opportunity.get("required_skills") or []),
        " ".join(opportunity.get("preferred_skills") or []),
        " ".join(opportunity.get("research_areas") or []),
        " ".join(opportunity.get("keywords") or []),
    ]
    return "\n".join(p for p in parts if p)


# ─── Public scoring API ─────────────────────────────────────────────────────

def score_student_against_all_opportunities(profile: Dict[str, Any], opportunities: List[Dict[str, Any]]) -> List[ResearchMatch]:
    if not opportunities:
        return []

    resume_text = _resume_text(profile)
    opp_texts = [_opportunity_text(o) for o in opportunities]

    semantic_scores = [0.0] * len(opportunities)
    embed_ready = False
    try:
        embedder = _get_shared_embedder()
        if embedder.is_ready and resume_text.strip():
            resume_vec = embedder.encode(resume_text)
            opp_vecs = embedder.encode_batch(opp_texts)
            sims = (opp_vecs @ resume_vec.T).flatten()
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
    results: List[ResearchMatch] = []
    for opportunity, semantic_score in zip(opportunities, semantic_scores):
        skills_score, matching_skills, missing_skills = _skills_component(
            resume_skills, opportunity.get("required_skills"), opportunity.get("preferred_skills"),
        )
        research_fit_score = _research_fit_component(
            _profile_domain_areas(profile), profile.get("education"),
            opportunity.get("research_areas"), opportunity.get("degree_level", ""),
            opportunity.get("education_requirement", ""),
        )
        experience_score = _research_experience_component(
            profile.get("publications"), profile.get("projects"),
            profile.get("work_experience"), profile.get("internships"),
            bool(opportunity.get("publications_expected")),
        )
        qualifications_score = _qualifications_component(
            profile.get("certifications"), profile.get("education"), profile.get("achievements_soft_skills"),
            opportunity.get("required_qualifications"), opportunity.get("preferred_qualifications"),
        )
        keywords_score = _keywords_component(
            profile.get("preferred_domains"), profile.get("achievements_soft_skills"),
            opportunity.get("keywords"), opportunity.get("domain_tags"),
        )

        component_scores = {
            "skills": skills_score,
            "semantic": semantic_score,
            "research_fit": research_fit_score,
            "experience": experience_score,
            "qualifications": qualifications_score,
            "keywords": keywords_score,
        }
        match_score = round(sum(component_scores[k] * w for k, w in weights.items()), 1)
        confidence = _confidence(match_score, skills_score)
        reasons = _build_match_reasons(
            matching_skills, semantic_score if embed_ready else 0.0, research_fit_score, experience_score,
        )

        results.append(ResearchMatch(
            opportunity_id=opportunity.get("opportunity_id", ""),
            title=opportunity.get("title", ""),
            professor_name=opportunity.get("professor_name", ""),
            department=opportunity.get("department", ""),
            opportunity_type=opportunity.get("opportunity_type", ""),
            degree_level=opportunity.get("degree_level", ""),
            match_score=match_score,
            semantic_score=round(semantic_score, 1) if embed_ready else 0.0,
            skills_score=round(skills_score, 1),
            research_fit_score=round(research_fit_score, 1),
            experience_score=round(experience_score, 1),
            qualifications_score=round(qualifications_score, 1),
            keywords_score=round(keywords_score, 1),
            confidence=confidence,
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            reasons=reasons,
            duration=opportunity.get("duration", ""),
            location=opportunity.get("location", ""),
            is_remote=bool(opportunity.get("is_remote")),
            created_at=opportunity.get("created_at", 0.0),
        ))

    results.sort(key=lambda m: -m.match_score)
    return results


def score_student_against_opportunity(profile: Dict[str, Any], opportunity: Dict[str, Any]) -> Optional[ResearchMatch]:
    results = score_student_against_all_opportunities(profile, [opportunity])
    return results[0] if results else None


def score_students_against_opportunity(students: List[Dict[str, Any]], opportunity: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The professor-facing direction matching_engine_9.py never needed: rank many
    student profiles against one opportunity. Returns (student_profile,
    ResearchMatch) pairs sorted by match_score - each profile is scored
    independently (batched semantic pass per-student would need a different
    embedding layout since here the OPPORTUNITY text is fixed and the
    RESUME texts vary; batching that direction isn't worth the complexity
    at this scale, so this loops score_student_against_opportunity)."""
    pairs = []
    for profile in students:
        match = score_student_against_opportunity(profile, opportunity)
        if match:
            pairs.append((profile, match))
    pairs.sort(key=lambda p: -p[1].match_score)
    return pairs


# ─── AI suggestions to improve match (bullet list, Claude-first/rule-fallback) ──

OPPORTUNITY_SUGGESTIONS_PROMPT = """You are a career advisor helping a student improve their fit for a specific research opportunity.

Student profile:
{profile}

Research opportunity:
{opportunity}

Current match:
{match}

Return ONLY valid JSON with these fields:
{{
  "skills_to_learn": ["3-6 specific skills that would improve this match"],
  "resume_suggestions": ["2-4 concrete ways to improve the resume/profile for this kind of opportunity"],
  "recommended_courses_certs": ["3-5 course or certification names/topics, general enough to search for"]
}}"""


def _profile_summary_for_opportunity(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "skills": profile.get("skills", []),
        "education": profile.get("education", []),
        "research_areas": _profile_domain_areas(profile),
        "publications": profile.get("publications", []),
        "projects": profile.get("projects", []),
    }


def _opportunity_suggestions_with_claude(profile: Dict[str, Any], opportunity: Dict[str, Any], match: ResearchMatch) -> Optional[Dict[str, Any]]:
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
                "content": OPPORTUNITY_SUGGESTIONS_PROMPT.format(
                    profile=json.dumps(_profile_summary_for_opportunity(profile)),
                    opportunity=json.dumps({
                        "title": opportunity.get("title", ""),
                        "description": (opportunity.get("description") or "")[:1500],
                        "required_skills": opportunity.get("required_skills", []),
                        "preferred_skills": opportunity.get("preferred_skills", []),
                        "research_areas": opportunity.get("research_areas", []),
                    }),
                    match=json.dumps({
                        "match_score": match.match_score,
                        "missing_skills": match.missing_skills,
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


def _opportunity_suggestions_rule_based(match: ResearchMatch) -> Dict[str, Any]:
    missing = match.missing_skills
    return {
        "skills_to_learn": missing or ["No major skill gaps identified from this opportunity's stated requirements"],
        "resume_suggestions": (
            [f"Highlight any hands-on experience with {s}, if you have it" for s in missing[:3]]
            or ["Your profile already covers this opportunity's key requirements well"]
        ),
        "recommended_courses_certs": [f"{s} Fundamentals" for s in missing[:4]] or ["No specific gaps to target right now"],
        "source": "rule",
    }


def generate_match_suggestions(
    student_id: str, profile: Dict[str, Any], opportunity: Dict[str, Any], match: ResearchMatch,
    use_claude: bool = True, db_path: Optional[str] = None,
) -> Dict[str, Any]:
    opportunity_id = opportunity.get("opportunity_id", "")
    profile_version = profile.get("updated_at", 0)
    opportunity_version = opportunity.get("updated_at", 0)
    cache_key = f"opp_gap:{student_id}:{opportunity_id}:{profile_version}:{opportunity_version}"

    cached = mdb.get_explanation(cache_key, db_path)
    if cached:
        result = dict(cached["explanation"])
        result["source"] = "cache"
        return result

    result = None
    if use_claude:
        result = _opportunity_suggestions_with_claude(profile, opportunity, match)
    if result is None:
        result = _opportunity_suggestions_rule_based(match)

    mdb.save_explanation(cache_key, "opp_gap", student_id, opportunity_id, result, db_path)
    return result


# ─── Fit explanation (paragraph, cloned from explainer.py's MatchExplainer shape) ──

FIT_EXPLANATION_PROMPT = """You are CollabV AI, an academic research-opportunity matching platform. \
You write concise, honest explanations for why a specific student is a good (or partial) fit for a \
specific professor's research opportunity. Cite actual skills, research areas, or experience. \
Acknowledge gaps honestly. Keep the summary to 2-3 sentences. Return STRICT JSON only.

Student profile:
{profile}

Research opportunity:
{opportunity}

Match scores:
{scores}

Return ONLY valid JSON with these fields:
{{
  "summary": "2-3 sentences explaining why this student is/isn't a strong fit",
  "key_strengths": ["3 bullet points - specific, citing real skills/research/experience"],
  "potential_gaps": ["1-3 honest gaps or weaknesses"]
}}"""


def _fit_explanation_with_claude(profile: Dict[str, Any], opportunity: Dict[str, Any], match: ResearchMatch) -> Optional[Dict[str, Any]]:
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": FIT_EXPLANATION_PROMPT.format(
                    profile=json.dumps(_profile_summary_for_opportunity(profile)),
                    opportunity=json.dumps({
                        "title": opportunity.get("title", ""),
                        "description": (opportunity.get("description") or "")[:1500],
                        "required_skills": opportunity.get("required_skills", []),
                        "research_areas": opportunity.get("research_areas", []),
                        "degree_level": opportunity.get("degree_level", ""),
                    }),
                    scores=json.dumps(match.to_dict()),
                ),
            }],
        )
        content = response.content[0].text
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            return None
        data = json.loads(json_match.group())
        return {
            "summary": str(data.get("summary", "")).strip(),
            "key_strengths": [str(s).strip() for s in data.get("key_strengths", [])][:5],
            "potential_gaps": [str(s).strip() for s in data.get("potential_gaps", [])][:4],
            "source": "claude",
        }
    except Exception:
        return None


def _fit_explanation_rule_based(profile: Dict[str, Any], opportunity: Dict[str, Any], match: ResearchMatch) -> Dict[str, Any]:
    name = profile.get("name") or "This student"
    title = opportunity.get("title", "this opportunity")
    summary = f"{name} scores {match.match_score:.0f}/100 for {title}. "
    if match.matching_skills:
        summary += f"Their background in {', '.join(match.matching_skills[:3])} lines up well with what's needed. "
    else:
        summary += f"Their strongest dimension here is {'research fit' if match.research_fit_score >= match.skills_score else 'skills overlap'}. "
    if match.missing_skills:
        summary += f"They would benefit from more exposure to {', '.join(match.missing_skills[:2])}."

    strengths = []
    if match.matching_skills:
        strengths.append(f"Skills overlap: {', '.join(match.matching_skills[:4])}")
    domain_areas = _profile_domain_areas(profile)
    if domain_areas:
        strengths.append(f"Research/domain interests: {', '.join(domain_areas[:3])}")
    if profile.get("publications"):
        strengths.append(f"{len(profile['publications'])} publication(s) on record")
    if not strengths:
        strengths.append("General academic background aligns loosely with this opportunity")

    gaps = []
    if match.missing_skills:
        gaps.append(f"Missing skills: {', '.join(match.missing_skills[:4])}")
    if match.match_score < 55:
        gaps.append("Overall match is moderate - worth a closer look at the full profile before deciding")

    return {
        "summary": summary.strip(),
        "key_strengths": strengths[:4],
        "potential_gaps": gaps[:3],
        "source": "rule",
    }


def generate_fit_explanation(
    student_id: str, profile: Dict[str, Any], opportunity: Dict[str, Any], match: ResearchMatch,
    use_claude: bool = True, db_path: Optional[str] = None,
) -> Dict[str, Any]:
    opportunity_id = opportunity.get("opportunity_id", "")
    profile_version = profile.get("updated_at", 0)
    opportunity_version = opportunity.get("updated_at", 0)
    cache_key = f"opp_fit:{student_id}:{opportunity_id}:{profile_version}:{opportunity_version}"

    cached = mdb.get_explanation(cache_key, db_path)
    if cached:
        result = dict(cached["explanation"])
        result["source"] = "cache"
        return result

    result = None
    if use_claude:
        result = _fit_explanation_with_claude(profile, opportunity, match)
    if result is None:
        result = _fit_explanation_rule_based(profile, opportunity, match)

    mdb.save_explanation(cache_key, "opp_fit", student_id, opportunity_id, result, db_path)
    return result


__all__ = [
    "ResearchMatch",
    "score_student_against_opportunity", "score_student_against_all_opportunities",
    "score_students_against_opportunity",
    "generate_match_suggestions", "generate_fit_explanation",
]
