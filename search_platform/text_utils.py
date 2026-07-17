"""
Cleaning, normalization, and searchable-text extraction for the ingestion
pipeline. Turns a structured profile into the flat text embeddings and BM25
are built from, and computes profile completion.
"""
import re

WHITESPACE_RE = re.compile(r"\s+")
COMPLETION_FIELDS = [
    "headline", "bio", "organization", "department", "location",
    "skills", "research_areas", "interests", "education",
]


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("–", "-").replace("—", "-")
    text = WHITESPACE_RE.sub(" ", text).strip()
    return text


def flatten(value) -> str:
    """Flatten a str / list[str] / list[dict] field into space-joined text."""
    if not value:
        return ""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.extend(str(v) for v in item.values() if v)
        return clean_text(" ".join(parts))
    return clean_text(str(value))


def build_searchable_text(profile: dict) -> str:
    """
    Concatenate every field a query could plausibly match against.
    This string feeds both the BM25 index and the embedding model, so it's
    weighted toward identity + skills/research (repeated) since those are
    the fields most searches target.
    """
    name = profile.get("name", "")
    headline = profile.get("headline", "")
    parts = [
        name, name,             # light boost — name matches should surface
        profile.get("role", ""),
        headline, headline,
        profile.get("bio", ""),
        profile.get("organization", ""),
        profile.get("department", ""),
        profile.get("job_title", ""),
        profile.get("location", ""),
        flatten(profile.get("skills")), flatten(profile.get("skills")),
        flatten(profile.get("research_areas")), flatten(profile.get("research_areas")),
        flatten(profile.get("interests")),
        flatten(profile.get("projects")),
        flatten(profile.get("publications")),
        flatten(profile.get("patents")),
        flatten(profile.get("experience")),
        flatten(profile.get("education")),
        flatten(profile.get("keywords")),
        flatten(profile.get("tags")),
    ]
    return clean_text(" ".join(str(p) for p in parts if p))


def compute_profile_completion(profile: dict) -> float:
    """Fraction of the profile-quality signal fields that are populated."""
    filled = 0
    for field in COMPLETION_FIELDS:
        val = profile.get(field)
        if isinstance(val, str) and val.strip():
            filled += 1
        elif isinstance(val, list) and len(val) > 0:
            filled += 1
    return round(filled / len(COMPLETION_FIELDS), 3)


def build_vocabulary_tokens(profile: dict) -> set[str]:
    """Tokens used to seed spell-correction / autocomplete vocabularies."""
    text = build_searchable_text(profile).lower()
    return set(re.findall(r"[a-z0-9][a-z0-9+#.\-]*", text))
