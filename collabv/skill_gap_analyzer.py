"""
CollabV AI - Skill Gap Analyzer (Student + Employee)
==========================================
Given a student OR employee profile and a patent listing they're interested
in, generates: missing skills, recommended courses, suggested
certifications, recommended research papers, suggested projects/training,
and a readiness score. Shared by both the Student and Employee Dashboards -
the two profile shapes overlap enough (skills/interests/certifications/
projects, plus student's research_areas or employee's industry_expertise/
innovation_interests) that one implementation covers both rather than
duplicating this module per buyer type.

Same Claude-first/rule-based-fallback shape as need_parser.py/resume_parser.py.
Cached via marketplace_db.py's marketplace_explanations table (previously
defined but unused anywhere) - keyed so that editing the buyer's profile
naturally invalidates any stale cached analysis (see analyze_skill_gap's
cache_key, which folds in the profile's updated_at).
"""

from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import marketplace_db as mdb


@dataclass
class SkillGapAnalysis:
    missing_skills: List[str] = field(default_factory=list)
    recommended_courses: List[str] = field(default_factory=list)
    suggested_certifications: List[str] = field(default_factory=list)
    recommended_papers: List[str] = field(default_factory=list)
    suggested_projects: List[str] = field(default_factory=list)
    readiness_score: float = 0.0
    source: str = "rule"  # "claude" | "rule" | "cache"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "missing_skills": self.missing_skills,
            "recommended_courses": self.recommended_courses,
            "suggested_certifications": self.suggested_certifications,
            "recommended_papers": self.recommended_papers,
            "suggested_projects": self.suggested_projects,
            "readiness_score": self.readiness_score,
            "source": self.source,
        }


SKILL_GAP_PROMPT = """You are a career advisor helping someone assess their \
readiness to work with a specific patented technology, for research, licensing, or startup use.

Profile:
{profile}

Patent:
{patent}

Return ONLY valid JSON with these fields:
{{
  "missing_skills": ["3-6 specific skills they would need to develop"],
  "recommended_courses": ["3-5 course/training names or topics, general enough to search for"],
  "suggested_certifications": ["2-4 relevant certifications"],
  "recommended_papers": ["2-4 research paper topics or search terms related to this patent's domain"],
  "suggested_projects": ["2-4 hands-on project ideas that would build the missing skills"],
  "readiness_score": integer 0-100, how ready they are today to work with this technology
}}"""


def _profile_summary(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Unifies the student_profiles and employee_profiles shapes into one
    summary for the Claude prompt / rule-based fallback - each dashboard's
    profile has a different field for "domain background" (student:
    research_areas/field_of_study, employee: industry_expertise/job_title),
    so both are read here and whichever is absent just contributes nothing."""
    return {
        "skills": profile.get("skills", []),
        "domain_expertise": (profile.get("research_areas") or []) + (profile.get("industry_expertise") or []),
        "background": profile.get("field_of_study") or profile.get("job_title") or "",
        "certifications": profile.get("certifications", []),
        "projects": profile.get("projects", []),
    }


def _analyze_with_claude(profile: Dict[str, Any], listing: Dict[str, Any]) -> Optional[SkillGapAnalysis]:
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        client = anthropic.Anthropic(api_key=api_key)
        profile_summary = json.dumps(_profile_summary(profile))
        patent_summary = json.dumps({
            "title": listing.get("title", ""),
            "abstract": listing.get("abstract", ""),
            "domain_tags": listing.get("domain_tags", []),
        })

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": SKILL_GAP_PROMPT.format(profile=profile_summary, patent=patent_summary),
            }],
        )

        content = response.content[0].text
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            return None
        data = json.loads(json_match.group())

        return SkillGapAnalysis(
            missing_skills=data.get("missing_skills", []),
            recommended_courses=data.get("recommended_courses", []),
            suggested_certifications=data.get("suggested_certifications", []),
            recommended_papers=data.get("recommended_papers", []),
            suggested_projects=data.get("suggested_projects", []),
            readiness_score=float(data.get("readiness_score", 0)),
            source="claude",
        )
    except Exception:
        return None


def _analyze_rule_based(profile: Dict[str, Any], listing: Dict[str, Any]) -> SkillGapAnalysis:
    """Deterministic fallback: compare domain tags against the profile's
    skills/research areas/industry expertise; anything in the patent's
    domain not already covered is a 'missing skill'."""
    profile_terms = {
        str(s).lower() for s in (
            (profile.get("skills") or [])
            + (profile.get("research_areas") or [])
            + (profile.get("interests") or [])
            + (profile.get("industry_expertise") or [])
            + (profile.get("innovation_interests") or [])
        )
    }
    domain_tags = [str(d) for d in (listing.get("domain_tags") or [])]
    missing = [d for d in domain_tags if d.lower() not in profile_terms]

    overlap = len(domain_tags) - len(missing)
    readiness = round(100.0 * overlap / len(domain_tags), 1) if domain_tags else 50.0

    return SkillGapAnalysis(
        missing_skills=missing or ["No specific gaps identified from listed domains"],
        recommended_courses=[f"Introduction to {d.replace('_', ' ').title()}" for d in missing[:4]],
        suggested_certifications=[f"{d.replace('_', ' ').title()} Fundamentals Certificate" for d in missing[:3]],
        recommended_papers=[f"Recent advances in {d.replace('_', ' ')}" for d in domain_tags[:3]],
        suggested_projects=[f"Build a small {d.replace('_', ' ')} prototype" for d in missing[:3]],
        readiness_score=readiness,
        source="rule",
    )


def analyze_skill_gap(
    buyer_id: str,
    profile: Dict[str, Any],
    listing: Dict[str, Any],
    use_claude: bool = True,
    db_path: Optional[str] = None,
) -> SkillGapAnalysis:
    listing_id = listing.get("listing_id", "")
    profile_version = profile.get("updated_at", 0)
    cache_key = f"skill_gap:{buyer_id}:{listing_id}:{profile_version}"

    cached = mdb.get_explanation(cache_key, db_path)
    if cached:
        result = SkillGapAnalysis(**cached["explanation"])
        result.source = "cache"
        return result

    result = None
    if use_claude:
        result = _analyze_with_claude(profile, listing)
    if result is None:
        result = _analyze_rule_based(profile, listing)

    mdb.save_explanation(cache_key, "skill_gap", buyer_id, listing_id, result.to_dict(), db_path)
    return result


__all__ = ["SkillGapAnalysis", "analyze_skill_gap"]
