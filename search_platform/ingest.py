"""
Ingestion pipeline: clean/normalize -> extract searchable text -> embed ->
upsert (Postgres = structured data + fallback vector storage) -> refresh the
in-process BM25 / vector / vocabulary indexes.

Two entry points:
  - upsert_profile: single-profile incremental indexing (POST /index-user,
    PUT /update-user)
  - bulk_ingest: batch load (initial seed / re-index), batches the embedding
    model call across all profiles instead of one-by-one for throughput.
"""
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from .bm25_index import get_bm25_index
from .embeddings import encode_documents
from .models import UserProfile
from .schemas import UserProfileIn
from .text_utils import build_searchable_text, compute_profile_completion
from .vector_store import get_vector_repository
from .vocabulary import get_vocabulary


def _profile_dict(profile_in: UserProfileIn) -> dict:
    d = profile_in.model_dump(mode="json")
    d["projects"] = [p if isinstance(p, dict) else p for p in d.get("projects", [])]
    d["experience"] = [e if isinstance(e, dict) else e for e in d.get("experience", [])]
    return d


def refresh_indexes(db: Session) -> None:
    """Reload BM25 / vector / vocabulary in-process indexes from Postgres."""
    get_bm25_index().refresh(db)
    get_vector_repository().refresh(db)
    get_vocabulary().refresh(db)


def upsert_profile(db: Session, profile_in: UserProfileIn, user_id: Optional[uuid.UUID] = None) -> UserProfile:
    d = _profile_dict(profile_in)
    d["role"] = profile_in.role.value
    text = build_searchable_text(d)
    completion = compute_profile_completion(d)
    embedding = encode_documents([text])[0].tolist()

    if user_id is not None:
        row = db.get(UserProfile, user_id)
        if row is None:
            raise ValueError(f"User {user_id} not found")
    else:
        row = UserProfile(id=uuid.uuid4())
        db.add(row)

    for field in (
        "name", "headline", "bio", "organization", "department", "job_title", "location",
        "skills", "research_areas", "interests", "publications", "patents", "education",
        "keywords", "tags", "languages", "recent_posts", "github", "linkedin", "website",
        "followers", "connections", "activity_score",
    ):
        setattr(row, field, getattr(profile_in, field))
    row.role = profile_in.role
    row.projects = [p.model_dump() for p in profile_in.projects]
    row.experience = [e.model_dump() for e in profile_in.experience]
    row.searchable_text = text
    row.profile_completion = completion
    row.embedding = embedding

    db.commit()
    db.refresh(row)
    refresh_indexes(db)
    return row


def bulk_ingest(db: Session, profiles: list[UserProfileIn], batch_size: int = 64) -> int:
    dicts = []
    for p in profiles:
        d = _profile_dict(p)
        d["role"] = p.role.value
        dicts.append(d)

    texts = [build_searchable_text(d) for d in dicts]
    embeddings = []
    for i in range(0, len(texts), batch_size):
        embeddings.extend(encode_documents(texts[i:i + batch_size]).tolist())

    count = 0
    for p, d, text, emb in zip(profiles, dicts, texts, embeddings):
        row = UserProfile(
            id=uuid.uuid4(),
            name=p.name, role=p.role, headline=p.headline, bio=p.bio,
            organization=p.organization, department=p.department, job_title=p.job_title,
            location=p.location, skills=p.skills, research_areas=p.research_areas,
            interests=p.interests, projects=[pr.model_dump() for pr in p.projects],
            publications=p.publications, patents=p.patents,
            experience=[e.model_dump() for e in p.experience], education=p.education,
            keywords=p.keywords, tags=p.tags, languages=p.languages,
            recent_posts=p.recent_posts, github=p.github, linkedin=p.linkedin,
            website=p.website, followers=p.followers, connections=p.connections,
            activity_score=p.activity_score,
            searchable_text=text, profile_completion=compute_profile_completion(d), embedding=emb,
        )
        db.add(row)
        count += 1
    db.commit()
    refresh_indexes(db)
    return count
