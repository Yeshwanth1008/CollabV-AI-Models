"""
SQLAlchemy ORM models for the multi-role profile store.

Design choice: heterogeneous role-specific fields (a Company doesn't have
"publications", a Student doesn't have "followers" the same way a Company
has "hiring roles") are modeled as JSONB rather than a dozen role-specific
join tables. This keeps one queryable, filterable table across every role,
which is what a unified search index needs. Structural integrity for
role-specific forms belongs in the application layer (Pydantic schemas),
not the storage layer.

`embedding` is a plain float array so this works on stock Postgres. Once
pgvector is installed, migrate the column to `vector(EMBEDDING_DIM)` and
flip VECTOR_BACKEND=pgvector in .env — see search_platform/vector_store.py.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import ARRAY, DateTime, Enum, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .config import get_settings
from .db import Base

# Column type for `embedding` tracks VECTOR_BACKEND so the ORM keeps writing
# valid values through migrate_to_pgvector.py's column-type swap — not just
# the read path in vector_store.py. See INSTALL_PGVECTOR.md.
if get_settings().vector_backend == "pgvector":
    from pgvector.sqlalchemy import Vector

    _EMBEDDING_TYPE = Vector(get_settings().embedding_dim)
else:
    _EMBEDDING_TYPE = ARRAY(Float)


class UserRole(str, enum.Enum):
    student = "student"
    professor = "professor"
    researcher = "researcher"
    employee = "employee"
    company = "company"
    startup = "startup"
    institute = "institute"
    alumni = "alumni"
    mentor = "mentor"


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False)
    headline: Mapped[str] = mapped_column(String(300), default="")
    bio: Mapped[str] = mapped_column(Text, default="")

    organization: Mapped[str] = mapped_column(String(200), default="")  # company / institute name
    department: Mapped[str] = mapped_column(String(200), default="")
    job_title: Mapped[str] = mapped_column(String(200), default="")
    location: Mapped[str] = mapped_column(String(200), default="")

    skills: Mapped[list] = mapped_column(JSONB, default=list)
    research_areas: Mapped[list] = mapped_column(JSONB, default=list)
    interests: Mapped[list] = mapped_column(JSONB, default=list)
    projects: Mapped[list] = mapped_column(JSONB, default=list)          # [{name, description, tech:[..]}]
    publications: Mapped[list] = mapped_column(JSONB, default=list)      # [str]
    patents: Mapped[list] = mapped_column(JSONB, default=list)           # [str]
    experience: Mapped[list] = mapped_column(JSONB, default=list)        # [{title, org, years}]
    education: Mapped[list] = mapped_column(JSONB, default=list)         # [str]
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    languages: Mapped[list] = mapped_column(JSONB, default=list)
    recent_posts: Mapped[list] = mapped_column(JSONB, default=list)      # [str]

    github: Mapped[str] = mapped_column(String(300), default="")
    linkedin: Mapped[str] = mapped_column(String(300), default="")
    website: Mapped[str] = mapped_column(String(300), default="")

    activity_score: Mapped[float] = mapped_column(Float, default=0.0)     # 0..1
    followers: Mapped[int] = mapped_column(Integer, default=0)
    connections: Mapped[int] = mapped_column(Integer, default=0)
    profile_completion: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1

    # Derived at ingest time — the flattened text embeddings/BM25 are built from.
    searchable_text: Mapped[str] = mapped_column(Text, default="")
    # Fallback vector storage (works on stock Postgres). Superseded by a
    # native `vector` column when VECTOR_BACKEND=pgvector.
    embedding: Mapped[list] = mapped_column(_EMBEDDING_TYPE, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_user_profiles_role", "role"),
        Index("ix_user_profiles_organization", "organization"),
        Index("ix_user_profiles_department", "department"),
        Index("ix_user_profiles_location", "location"),
        Index("ix_user_profiles_skills_gin", "skills", postgresql_using="gin"),
        Index("ix_user_profiles_research_areas_gin", "research_areas", postgresql_using="gin"),
        Index("ix_user_profiles_tags_gin", "tags", postgresql_using="gin"),
    )
