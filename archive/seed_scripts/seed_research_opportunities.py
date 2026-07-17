"""
Seed sample research opportunities for AI Matching Engine 8.

Reads iitm_professors_nlp.json DIRECTLY (same convention as
generate_synthetic_patents.py - no dependency on a running server), picks
~18 real professors with non-empty research_areas, and generates one
research opportunity each, cycling through the 12 requested opportunity
types and varied degree levels, so the Student Dashboard's "AI Matching
Engine 8" tab and the Professor Dashboard's ranked-candidates section have
real, demoable data immediately - in addition to (not instead of) the
"Post a Research Opportunity" form professors can also use.

Idempotent: deletes any previously-seeded rows (opportunity_id LIKE
'ROPP-SEED-%') before inserting, so re-running this script is safe.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from collabv.research_opportunity_db import (  # noqa: E402
    DEFAULT_DB_PATH, OPPORTUNITY_TYPES, init_research_opportunity_tables, save_opportunity,
)
from collabv.need_parser import DOMAIN_PATTERNS, TECH_STACK_PATTERNS, _match_patterns  # noqa: E402

DEGREE_LEVELS = ["undergraduate", "masters", "phd", "postdoc"]

_DEGREE_BY_TYPE = {
    "research_internship": "undergraduate",
    "masters": "masters",
    "phd": "phd",
    "postdoctoral": "postdoc",
    "research_assistant": "undergraduate",
    "thesis_dissertation": "masters",
    "lab_position": "undergraduate",
    "collaborative_project": "masters",
    "visiting_researcher": "phd",
    "fellowship": "phd",
    "summer_winter_program": "undergraduate",
    "other": "masters",
}

_TITLE_TEMPLATES = {
    "research_internship": "Summer Research Internship in {area}",
    "masters": "Master's Research Position in {area}",
    "phd": "PhD Position in {area}",
    "postdoctoral": "Postdoctoral Research Fellow - {area}",
    "research_assistant": "Research Assistant - {area} Lab",
    "thesis_dissertation": "Master's Thesis Opportunity in {area}",
    "lab_position": "Lab Position - {area} Research Group",
    "collaborative_project": "Collaborative Research Project in {area}",
    "visiting_researcher": "Visiting Researcher Position - {area}",
    "fellowship": "Research Fellowship in {area}",
    "summer_winter_program": "Winter Research Program in {area}",
    "other": "Research Opportunity in {area}",
}

_CERT_POOL = [
    "Research Methods Certificate", "Academic Writing Certificate",
    "Data Analysis Certification", "Lab Safety Training",
]


def _pick_area(professor: dict) -> str:
    areas = [a for a in (professor.get("research_areas") or []) if len(str(a)) > 2]
    return areas[0] if areas else professor.get("department", "Engineering").replace("Department of ", "")


def main() -> None:
    with open(ROOT / "iitm_professors_nlp.json", encoding="utf-8") as f:
        professors = json.load(f)

    candidates = [p for p in professors if p.get("research_areas")]
    selected = candidates[::max(1, len(candidates) // 18)][:18]

    init_research_opportunity_tables(DEFAULT_DB_PATH)

    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.execute("DELETE FROM research_opportunities WHERE opportunity_id LIKE 'ROPP-SEED-%'")
    conn.commit()
    conn.close()

    created = []
    for i, prof in enumerate(selected):
        opportunity_type = OPPORTUNITY_TYPES[i % len(OPPORTUNITY_TYPES)]
        degree_level = _DEGREE_BY_TYPE[opportunity_type]
        area = _pick_area(prof)
        research_areas = [str(a) for a in (prof.get("research_areas") or [])][:5]
        department = prof.get("department", "")
        title = _TITLE_TEMPLATES[opportunity_type].format(area=area)

        description = (
            f"Join Dr. {prof.get('name', 'the PI')}'s research group in {department} at IIT Madras, "
            f"working on {', '.join(research_areas[:3]) or area}. This {opportunity_type.replace('_', ' ')} "
            f"involves hands-on research, regular mentorship, and the opportunity to contribute to "
            f"publications in this area. We're looking for a motivated candidate with a strong "
            f"foundation relevant to {area} and genuine interest in academic research."
        )

        required_skills = _match_patterns(description, TECH_STACK_PATTERNS)
        domain_tags = sorted(set(_match_patterns(description, DOMAIN_PATTERNS) + research_areas[:3]))
        preferred_skills = ["Technical Writing", "Data Analysis"]

        publications_expected = opportunity_type in ("phd", "postdoctoral", "fellowship", "visiting_researcher")
        min_experience_years = {"undergraduate": 0, "masters": 0.5, "phd": 1, "postdoc": 3}[degree_level]

        opportunity_id = f"ROPP-SEED-{i:03d}"
        save_opportunity({
            "opportunity_id": opportunity_id,
            "professor_id": prof.get("professor_id", ""),
            "professor_name": prof.get("name", ""),
            "department": department,
            "title": title,
            "description": description,
            "opportunity_type": opportunity_type,
            "degree_level": degree_level,
            "research_areas": research_areas,
            "required_skills": required_skills,
            "preferred_skills": preferred_skills,
            "required_qualifications": [f"Background in {area}"],
            "preferred_qualifications": [_CERT_POOL[i % len(_CERT_POOL)]],
            "min_experience_years": min_experience_years,
            "education_requirement": f"{degree_level.title()} student or equivalent",
            "publications_expected": publications_expected,
            "keywords": domain_tags,
            "domain_tags": domain_tags,
            "duration": {"undergraduate": "3 months", "masters": "1-2 years", "phd": "4-5 years", "postdoc": "2 years"}[degree_level],
            "stipend_or_funding": "Institute fellowship" if degree_level in ("phd", "postdoc") else "Stipend as per institute norms",
            "location": "Chennai",
            "is_remote": i % 5 == 0,
            "university": "IIT Madras",
            "status": "active",
        }, DEFAULT_DB_PATH)
        created.append((opportunity_id, title, prof.get("name", ""), opportunity_type, degree_level))

    print(f"Seeded {len(created)} research opportunities:\n")
    print(f"{'opportunity_id':<18} {'title':<45} {'professor':<22} {'type':<20} degree")
    for opp_id, title, name, otype, degree in created:
        print(f"{opp_id:<18} {title:<45} {name:<22} {otype:<20} {degree}")

    type_counts = {}
    for _, _, _, otype, _ in created:
        type_counts[otype] = type_counts.get(otype, 0) + 1
    print(f"\n{len(created)} total across {len(type_counts)} opportunity types")


if __name__ == "__main__":
    main()
