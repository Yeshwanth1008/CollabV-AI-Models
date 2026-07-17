"""Pydantic request/response contracts for the search platform API."""
import uuid
from typing import Optional

from pydantic import BaseModel, Field

from .models import UserRole


class Project(BaseModel):
    name: str
    description: str = ""
    tech: list[str] = Field(default_factory=list)


class ExperienceEntry(BaseModel):
    title: str
    org: str = ""
    years: str = ""


class UserProfileIn(BaseModel):
    """Payload for POST /index-user and PUT /update-user."""
    name: str
    role: UserRole
    headline: str = ""
    bio: str = ""
    organization: str = ""
    department: str = ""
    job_title: str = ""
    location: str = ""
    skills: list[str] = Field(default_factory=list)
    research_areas: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    publications: list[str] = Field(default_factory=list)
    patents: list[str] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    recent_posts: list[str] = Field(default_factory=list)
    github: str = ""
    linkedin: str = ""
    website: str = ""
    followers: int = 0
    connections: int = 0
    activity_score: float = 0.0


class UserProfileOut(UserProfileIn):
    id: uuid.UUID
    profile_completion: float


class SearchFilters(BaseModel):
    role: Optional[list[UserRole]] = None
    organization: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    skills: Optional[list[str]] = None
    min_activity_score: Optional[float] = None


class SearchRequest(BaseModel):
    query: str
    filters: Optional[SearchFilters] = None
    limit: int = 10
    offset: int = 0
    explain: bool = True


class MatchExplanation(BaseModel):
    matched_skills: list[str] = Field(default_factory=list)
    matched_research_areas: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    semantic_similarity: float = 0.0
    keyword_score: float = 0.0
    rerank_score: float = 0.0
    summary: str = ""


class SearchResultItem(BaseModel):
    id: uuid.UUID
    name: str
    role: UserRole
    organization: str
    headline: str
    skills: list[str]
    research_areas: list[str]
    matching_score: float
    explanation: MatchExplanation
    highlighted_headline: str = ""


class SearchResponse(BaseModel):
    query: str
    corrected_query: Optional[str] = None
    expanded_terms: list[str] = Field(default_factory=list)
    total_candidates: int
    results: list[SearchResultItem]
    search_time_ms: float


class AutocompleteSuggestion(BaseModel):
    text: str
    type: str  # "name" | "skill" | "research_area" | "organization"
    role: Optional[UserRole] = None


class RecommendRequest(BaseModel):
    user_id: uuid.UUID
    limit: int = 10


class SimilarUsersRequest(BaseModel):
    user_id: uuid.UUID
    limit: int = 10
    same_role_only: bool = False


class ExplainRequest(BaseModel):
    query: str
    user_id: uuid.UUID
