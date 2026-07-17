"""
In-process vocabulary used by spell-correction and autocomplete. Rebuilt by
the same refresh() cycle as the BM25/vector indexes after ingestion.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import UserProfile
from .text_utils import build_vocabulary_tokens


class SearchVocabulary:
    def __init__(self):
        self.tokens: set[str] = set()
        # (display_text, type, role) triples for autocomplete
        self.entries: list[tuple[str, str, Optional[str]]] = []

    def refresh(self, db: Session) -> None:
        rows = db.execute(
            select(
                UserProfile.name, UserProfile.role, UserProfile.organization,
                UserProfile.skills, UserProfile.research_areas,
            )
        ).all()

        tokens: set[str] = set()
        entries: list[tuple[str, str, Optional[str]]] = []
        seen: set[tuple[str, str]] = set()

        def add(text: str, kind: str, role: Optional[str] = None):
            if not text:
                return
            key = (text.lower(), kind)
            if key in seen:
                return
            seen.add(key)
            entries.append((text, kind, role))

        for name, role, org, skills, research_areas in rows:
            role_val = role.value if hasattr(role, "value") else str(role)
            add(name, "name", role_val)
            add(org, "organization")
            for s in (skills or []):
                add(s, "skill")
            for r in (research_areas or []):
                add(r, "research_area")
            tokens |= build_vocabulary_tokens({
                "name": name, "organization": org,
                "skills": skills, "research_areas": research_areas,
            })

        self.tokens = tokens
        self.entries = entries

    def autocomplete(self, prefix: str, limit: int = 10) -> list[tuple[str, str, Optional[str]]]:
        prefix_lower = prefix.lower().strip()
        if len(prefix_lower) < 2:
            return []
        starts, contains = [], []
        for text, kind, role in self.entries:
            low = text.lower()
            if low.startswith(prefix_lower):
                starts.append((text, kind, role))
            elif prefix_lower in low:
                contains.append((text, kind, role))
            if len(starts) >= limit:
                break
        return (starts + contains)[:limit]


_vocab: Optional[SearchVocabulary] = None


def get_vocabulary() -> SearchVocabulary:
    global _vocab
    if _vocab is None:
        _vocab = SearchVocabulary()
    return _vocab
