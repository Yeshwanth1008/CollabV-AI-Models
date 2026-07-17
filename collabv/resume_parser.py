"""
CollabV AI - Student Resume Parser
====================================
Converts an uploaded resume (PDF/DOCX/TXT) into structured student-profile
suggestions. Primary: Claude API. Fallback: rule-based extraction, reusing
need_parser.py's domain/tech-stack keyword patterns (a resume and a company
need description both benefit from the same "what technical domains does
this text touch" classification).

Mirrors need_parser.py's exact shape: parse_with_claude() -> Optional[...],
falling back to parse_rule_based() if the LLM path is unavailable or fails.
Results are suggestions only - the frontend pre-fills a review form the
student edits before saving, never auto-saved.
"""

from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .need_parser import DOMAIN_PATTERNS, TECH_STACK_PATTERNS, _match_patterns

_MIN_EXTRACTED_CHARS = 50  # below this, treat extraction as failed (scanned/image-only PDF, no OCR available)


@dataclass
class ParsedResume:
    skills: List[str] = field(default_factory=list)
    education: List[str] = field(default_factory=list)
    projects: List[str] = field(default_factory=list)
    publications: List[str] = field(default_factory=list)
    certifications: List[str] = field(default_factory=list)
    internships: List[str] = field(default_factory=list)
    work_experience: List[str] = field(default_factory=list)
    research_interests: List[str] = field(default_factory=list)
    career_goals: str = ""
    preferred_domains: List[str] = field(default_factory=list)
    achievements_soft_skills: List[str] = field(default_factory=list)
    extraction_quality: str = "ok"  # "ok" | "low_text"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skills": self.skills,
            "education": self.education,
            "projects": self.projects,
            "publications": self.publications,
            "certifications": self.certifications,
            "internships": self.internships,
            "work_experience": self.work_experience,
            "research_interests": self.research_interests,
            "career_goals": self.career_goals,
            "preferred_domains": self.preferred_domains,
            "achievements_soft_skills": self.achievements_soft_skills,
            "extraction_quality": self.extraction_quality,
        }


# ─── File text extraction ───────────────────────────────────────────────────

class UnsupportedResumeFormat(ValueError):
    pass


def extract_text_from_file(filename: str, content: bytes) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "pdf":
        import io
        import pdfplumber
        parts: List[str] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        return "\n".join(parts)

    if ext == "docx":
        import io
        import docx
        document = docx.Document(io.BytesIO(content))
        return "\n".join(p.text for p in document.paragraphs)

    if ext == "txt":
        return content.decode("utf-8", errors="ignore")

    if ext == "doc":
        raise UnsupportedResumeFormat(
            "Legacy .doc files aren't supported - please save as PDF or DOCX and re-upload.",
        )

    raise UnsupportedResumeFormat(
        f"Unsupported file type '.{ext}' - please upload a PDF, DOCX, or TXT resume.",
    )


def is_extraction_too_sparse(text: str) -> bool:
    return len((text or "").strip()) < _MIN_EXTRACTED_CHARS


# ─── Claude parser ──────────────────────────────────────────────────────────

RESUME_CLAUDE_PROMPT = """You are an expert at parsing student resumes for an academic \
patent marketplace platform, where students discover and license professor-owned patents.

Given this resume text, extract structured fields as JSON:

<resume>
{text}
</resume>

Return ONLY valid JSON with these fields:
{{
  "skills": ["list of 5-15 technical skills, e.g. Python, robotics, deep learning, circuit design"],
  "education": ["list of degree lines, e.g. 'B.Tech Computer Science, IIT Madras, 2022-2026'"],
  "projects": ["list of 2-6 short project descriptions"],
  "publications": ["list of any papers/publications mentioned, empty if none"],
  "certifications": ["list of certifications/courses completed"],
  "internships": ["list of internship roles/companies"],
  "work_experience": ["list of work experience entries"],
  "research_interests": ["list of 2-6 research interest areas"],
  "career_goals": "a 1-2 sentence summary of the student's apparent career direction",
  "preferred_domains": ["list of 2-5 technology domains this student would be suited to, e.g. Robotics, Materials Science, Biotechnology"],
  "achievements_soft_skills": ["list of 3-8 notable achievements, leadership roles, soft skills, or domain keywords not captured above, e.g. 'Led 5-person hackathon team', 'Fluent in stakeholder communication', 'Published technical blog on distributed systems'"]
}}"""


def parse_resume_with_claude(text: str) -> Optional[ParsedResume]:
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1536,
            messages=[{
                "role": "user",
                "content": RESUME_CLAUDE_PROMPT.format(text=text[:15000]),
            }],
        )

        content = response.content[0].text
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            return None
        data = json.loads(json_match.group())

        return ParsedResume(
            skills=data.get("skills", []),
            education=data.get("education", []),
            projects=data.get("projects", []),
            publications=data.get("publications", []),
            certifications=data.get("certifications", []),
            internships=data.get("internships", []),
            work_experience=data.get("work_experience", []),
            research_interests=data.get("research_interests", []),
            career_goals=data.get("career_goals", ""),
            preferred_domains=data.get("preferred_domains", []),
            achievements_soft_skills=data.get("achievements_soft_skills", []),
        )
    except Exception:
        return None


# ─── Rule-based fallback ────────────────────────────────────────────────────

_EDUCATION_PATTERN = re.compile(
    r"(b\.?\s?tech|m\.?\s?tech|b\.?\s?e\b|b\.?\s?sc|m\.?\s?sc|ph\.?\s?d|bachelor|master|doctorate|"
    r"m\.?\s?s\b)[^\n]{0,120}", re.IGNORECASE,
)
_CERTIFICATION_PATTERN = re.compile(r"[^\n]*\b(certif\w*|course completion)\b[^\n]*", re.IGNORECASE)
_PUBLICATION_PATTERN = re.compile(r"[^\n]*\b(ieee|springer|elsevier|journal|conference paper|published)\b[^\n]*", re.IGNORECASE)
_INTERNSHIP_PATTERN = re.compile(r"[^\n]*\bintern(ship)?\b[^\n]*", re.IGNORECASE)
_WORK_PATTERN = re.compile(r"[^\n]*\b(work experience|employed|engineer at|developer at|worked at)\b[^\n]*", re.IGNORECASE)
_PROJECT_PATTERN = re.compile(r"[^\n]*\bproject[^\n]*", re.IGNORECASE)
_ACHIEVEMENT_PATTERN = re.compile(
    r"[^\n]*\b(award|led|leadership|mentor(ed)?|hackathon|volunteer(ed)?|captain|"
    r"organi[sz]ed|winner|achievement|published.*blog|communication)\b[^\n]*", re.IGNORECASE,
)


def _lines_matching(text: str, pattern: re.Pattern, limit: int = 8) -> List[str]:
    seen: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 200:
            continue
        if pattern.search(line) and line not in seen:
            seen.append(line)
        if len(seen) >= limit:
            break
    return seen


def parse_resume_rule_based(text: str) -> ParsedResume:
    domains = _match_patterns(text, DOMAIN_PATTERNS)
    tech_stack = _match_patterns(text, TECH_STACK_PATTERNS)

    return ParsedResume(
        skills=tech_stack + [d for d in domains if d not in tech_stack][:5],
        education=_lines_matching(text, _EDUCATION_PATTERN, limit=4),
        projects=_lines_matching(text, _PROJECT_PATTERN, limit=6),
        publications=_lines_matching(text, _PUBLICATION_PATTERN, limit=5),
        certifications=_lines_matching(text, _CERTIFICATION_PATTERN, limit=6),
        internships=_lines_matching(text, _INTERNSHIP_PATTERN, limit=4),
        work_experience=_lines_matching(text, _WORK_PATTERN, limit=4),
        research_interests=domains[:5],
        career_goals="",
        preferred_domains=domains[:5],
        achievements_soft_skills=_lines_matching(text, _ACHIEVEMENT_PATTERN, limit=6),
    )


# ─── Public API ──────────────────────────────────────────────────────────────

def parse_resume(text: str, use_claude: bool = True) -> ParsedResume:
    if is_extraction_too_sparse(text):
        return ParsedResume(extraction_quality="low_text")

    if use_claude:
        result = parse_resume_with_claude(text)
        if result:
            return result

    return parse_resume_rule_based(text)


__all__ = [
    "ParsedResume", "extract_text_from_file", "is_extraction_too_sparse",
    "parse_resume", "parse_resume_with_claude", "parse_resume_rule_based",
    "UnsupportedResumeFormat",
]
