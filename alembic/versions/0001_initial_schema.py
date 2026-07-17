"""initial schema with pgvector

Revision ID: 0001
Revises:
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Required extension. Ignore failure if running on a database that doesn't
    # support the extension (e.g. cloud-managed Postgres without it).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("company_name", sa.String(200)),
        sa.Column("role", sa.String(32), nullable=False, server_default="company_user"),
        sa.Column("api_key", sa.String(128), nullable=False, unique=True),
        sa.Column("tier", sa.String(32), nullable=False, server_default="free"),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_api_key", "users", ["api_key"], unique=True)
    op.create_index("ix_users_created_at", "users", ["created_at"])

    op.create_table(
        "company_requests",
        sa.Column("company_id", sa.String(64), primary_key=True),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("industry", sa.String(200)),
        sa.Column("technical_area", sa.JSON()),
        sa.Column("required_expertise", sa.JSON()),
        sa.Column("technology_stack", sa.JSON()),
        sa.Column("project_description", sa.Text()),
        sa.Column("challenges", sa.Text()),
        sa.Column("collaboration_type", sa.String(100)),
        sa.Column("location_preference", sa.String(100)),
        sa.Column("research_level", sa.String(50)),
        sa.Column("budget_tier", sa.String(50)),
        sa.Column("timeline_months", sa.Integer()),
        sa.Column("raw_text", sa.Text()),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("ix_company_requests_created_at", "company_requests", ["created_at"])

    op.create_table(
        "professor_profiles",
        sa.Column("professor_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("department", sa.String(200)),
        sa.Column("biography", sa.Text()),
        sa.Column("research_areas", sa.JSON()),
        sa.Column("publications", sa.JSON()),
        sa.Column("patents", sa.JSON()),
        sa.Column("raw_profile", sa.JSON()),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    # pgvector column - has to be created with raw SQL so alembic doesn't choke
    # on the unknown column type during autogenerate.
    op.execute("ALTER TABLE professor_profiles ADD COLUMN embedding vector(384)")
    op.create_index("ix_professor_profiles_name", "professor_profiles", ["name"])
    op.create_index("ix_professor_profiles_department", "professor_profiles", ["department"])
    op.execute("CREATE INDEX ix_professor_profiles_embedding_cosine "
               "ON professor_profiles USING hnsw (embedding vector_cosine_ops)")

    op.create_table(
        "match_results",
        sa.Column("match_id", sa.String(64), primary_key=True),
        sa.Column("company_id", sa.String(64), sa.ForeignKey("company_requests.company_id")),
        sa.Column("company_name", sa.String(200)),
        sa.Column("top_score", sa.Float()),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("parsed_tags", sa.JSON()),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("ix_match_results_company_id", "match_results", ["company_id"])
    op.create_index("ix_match_results_created_at", "match_results", ["created_at"])

    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.String(64), sa.ForeignKey("match_results.match_id")),
        sa.Column("professor_id", sa.String(64)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("ix_feedback_match_id", "feedback", ["match_id"])
    op.create_index("ix_feedback_professor_id", "feedback", ["professor_id"])
    op.create_index("ix_feedback_created_at", "feedback", ["created_at"])

    op.create_table(
        "match_explanations",
        sa.Column("cache_key", sa.String(128), primary_key=True),
        sa.Column("professor_id", sa.String(64)),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("ix_match_explanations_professor_id", "match_explanations", ["professor_id"])

    op.create_table(
        "weight_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("improvement_score", sa.Float()),
        sa.Column("feedback_count", sa.Integer()),
        sa.Column("applied_at", sa.Float, nullable=False),
        sa.Column("note", sa.String(200)),
    )
    op.create_index("ix_weight_history_applied_at", "weight_history", ["applied_at"])

    op.create_table(
        "deal_assessments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.String(64), sa.ForeignKey("match_results.match_id")),
        sa.Column("professor_id", sa.String(64)),
        sa.Column("success_probability", sa.Float()),
        sa.Column("confidence_level", sa.String(32)),
        sa.Column("band", sa.String(32)),
        sa.Column("assessment", sa.JSON()),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("ix_deal_assessments_match_id", "deal_assessments", ["match_id"])
    op.create_index("ix_deal_assessments_professor_id", "deal_assessments", ["professor_id"])


def downgrade() -> None:
    for tbl in (
        "deal_assessments", "weight_history", "match_explanations", "feedback",
        "match_results", "professor_profiles", "company_requests", "users",
    ):
        op.drop_table(tbl)
