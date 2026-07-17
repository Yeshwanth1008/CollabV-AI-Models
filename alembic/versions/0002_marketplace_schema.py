"""marketplace schema (patent listings, buyer profiles, proposals, inquiries, events)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-16

Adds the patent-buyer marketplace schema. Pure additive — no changes to
existing tables. `users.role` already accepts free-form String(32), so the
new roles ('student_user', 'buyer_user') don't need a column change.

Lifecycle states for patent_listings (recorded as TEXT, not an enum):
    draft           -> created by inventor, NOT publicly visible
    pending_approval-> inventor submitted for admin review
    active          -> approved by admin, publicly browsable
    paused          -> temporarily off-market (inventor or admin)
    sold            -> transaction completed (terminal)
    withdrawn       -> inventor removed (terminal)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── patent_listings ──────────────────────────────────────────────────
    op.create_table(
        "patent_listings",
        sa.Column("listing_id", sa.String(64), primary_key=True),
        sa.Column("professor_id", sa.String(64), sa.ForeignKey("professor_profiles.professor_id"), nullable=False),
        sa.Column("patent_number", sa.String(64)),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text()),
        sa.Column("claims_text", sa.Text()),
        sa.Column("inventor_names", sa.JSON()),
        sa.Column("granted_date", sa.String(32)),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("licensing_terms", sa.JSON()),
        sa.Column("asking_price_inr", sa.Float()),
        sa.Column("domain_tags", sa.JSON()),
        sa.Column("industry_tags", sa.JSON()),
        sa.Column(
            "abstract_source",
            sa.String(32),
            server_default="unknown",
            comment="how the abstract was obtained: inventor / google_patents / iitm_feed / none",
        ),
        sa.Column(
            "activated_at",
            sa.Float(),
            comment="when the inventor activated this listing (consent event timestamp)",
        ),
        sa.Column(
            "approved_at",
            sa.Float(),
            comment="when an admin approved this listing",
        ),
        sa.Column(
            "approved_by_user_id",
            sa.String(64),
            sa.ForeignKey("users.id"),
        ),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.execute("ALTER TABLE patent_listings ADD COLUMN embedding vector(384)")
    op.create_index("ix_patent_listings_professor_id", "patent_listings", ["professor_id"])
    op.create_index("ix_patent_listings_status", "patent_listings", ["status"])
    op.create_index("ix_patent_listings_created_at", "patent_listings", ["created_at"])
    # Partial index — only the *publicly visible* listings need the vector index,
    # which keeps the HNSW build cheap. Drafts don't get embeddings indexed.
    op.execute(
        "CREATE INDEX ix_patent_listings_embedding_cosine "
        "ON patent_listings USING hnsw (embedding vector_cosine_ops) "
        "WHERE status = 'active'"
    )

    # ─── buyer_profiles ───────────────────────────────────────────────────
    op.create_table(
        "buyer_profiles",
        sa.Column("buyer_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("org_name", sa.String(200), nullable=False),
        sa.Column("org_type", sa.String(32)),
        sa.Column("industry", sa.String(200)),
        sa.Column("industries_of_interest", sa.JSON()),
        sa.Column("technical_areas", sa.JSON()),
        sa.Column("use_cases", sa.Text()),
        sa.Column("tech_maturity_preference", sa.String(32)),
        sa.Column("budget_band", sa.String(32)),
        sa.Column("geographic_scope", sa.JSON()),
        sa.Column("seller_preferences", sa.JSON()),
        sa.Column(
            "is_synthetic",
            sa.Boolean(),
            server_default=sa.text("false"),
            comment="seeded from 100_Companies_Collaboration_Schema.xlsx for offline eval",
        ),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.execute("ALTER TABLE buyer_profiles ADD COLUMN embedding vector(384)")
    op.create_index("ix_buyer_profiles_user_id", "buyer_profiles", ["user_id"], unique=True)
    op.create_index("ix_buyer_profiles_org_type", "buyer_profiles", ["org_type"])
    op.create_index("ix_buyer_profiles_industry", "buyer_profiles", ["industry"])
    op.create_index("ix_buyer_profiles_is_synthetic", "buyer_profiles", ["is_synthetic"])
    op.execute(
        "CREATE INDEX ix_buyer_profiles_embedding_cosine "
        "ON buyer_profiles USING hnsw (embedding vector_cosine_ops)"
    )

    # ─── marketplace_proposals ────────────────────────────────────────────
    op.create_table(
        "marketplace_proposals",
        sa.Column("proposal_id", sa.String(64), primary_key=True),
        sa.Column("listing_id", sa.String(64), sa.ForeignKey("patent_listings.listing_id"), nullable=False),
        sa.Column("buyer_id", sa.String(64), sa.ForeignKey("buyer_profiles.buyer_id"), nullable=False),
        sa.Column("inventor_id", sa.String(64), sa.ForeignKey("professor_profiles.professor_id"), nullable=False),
        sa.Column("proposal_text", sa.Text()),
        sa.Column("match_score", sa.Float()),
        sa.Column("score_breakdown", sa.JSON()),
        sa.Column("explanation", sa.JSON()),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="sent",
            comment="sent / viewed / replied / accepted / declined / expired",
        ),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("responded_at", sa.Float()),
    )
    op.create_index("ix_marketplace_proposals_listing_id", "marketplace_proposals", ["listing_id"])
    op.create_index("ix_marketplace_proposals_buyer_id", "marketplace_proposals", ["buyer_id"])
    op.create_index("ix_marketplace_proposals_inventor_id", "marketplace_proposals", ["inventor_id"])
    op.create_index("ix_marketplace_proposals_status", "marketplace_proposals", ["status"])
    op.create_index("ix_marketplace_proposals_created_at", "marketplace_proposals", ["created_at"])

    # ─── marketplace_inquiries ────────────────────────────────────────────
    op.create_table(
        "marketplace_inquiries",
        sa.Column("inquiry_id", sa.String(64), primary_key=True),
        sa.Column("listing_id", sa.String(64), sa.ForeignKey("patent_listings.listing_id"), nullable=False),
        sa.Column("buyer_id", sa.String(64), sa.ForeignKey("buyer_profiles.buyer_id")),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="new",
            comment="new / acknowledged / accepted / declined",
        ),
        sa.Column("match_score_at_inquiry", sa.Float()),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("responded_at", sa.Float()),
    )
    op.create_index("ix_marketplace_inquiries_listing_id", "marketplace_inquiries", ["listing_id"])
    op.create_index("ix_marketplace_inquiries_buyer_id", "marketplace_inquiries", ["buyer_id"])
    op.create_index("ix_marketplace_inquiries_user_id", "marketplace_inquiries", ["user_id"])
    op.create_index("ix_marketplace_inquiries_status", "marketplace_inquiries", ["status"])

    # ─── marketplace_events ───────────────────────────────────────────────
    op.create_table(
        "marketplace_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_type",
            sa.String(32),
            nullable=False,
            comment="view / click / save / dismiss / inquire / propose / accept / reject",
        ),
        sa.Column("actor_user_id", sa.String(64), sa.ForeignKey("users.id")),
        sa.Column(
            "actor_role",
            sa.String(32),
            comment="buyer / inventor / student / guest",
        ),
        sa.Column("subject_listing_id", sa.String(64), sa.ForeignKey("patent_listings.listing_id")),
        sa.Column("subject_buyer_id", sa.String(64), sa.ForeignKey("buyer_profiles.buyer_id")),
        sa.Column("match_score_at_event", sa.Float()),
        sa.Column("position_in_ranking", sa.Integer()),
        sa.Column(
            "query_hash",
            sa.String(64),
            comment="groups events from one recommendation list (LTR group key)",
        ),
        sa.Column("payload", sa.JSON()),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_marketplace_events_actor_user_id", "marketplace_events", ["actor_user_id"])
    op.create_index("ix_marketplace_events_subject_listing_id", "marketplace_events", ["subject_listing_id"])
    op.create_index("ix_marketplace_events_subject_buyer_id", "marketplace_events", ["subject_buyer_id"])
    op.create_index("ix_marketplace_events_event_type", "marketplace_events", ["event_type"])
    op.create_index("ix_marketplace_events_query_hash", "marketplace_events", ["query_hash"])
    op.create_index("ix_marketplace_events_created_at", "marketplace_events", ["created_at"])

    # ─── marketplace_explanations (LLM cache) ─────────────────────────────
    op.create_table(
        "marketplace_explanations",
        sa.Column("cache_key", sa.String(128), primary_key=True),
        sa.Column(
            "mode",
            sa.String(32),
            nullable=False,
            comment="buyers_for_patent / patents_for_buyer",
        ),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("explanation_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_marketplace_explanations_subject_target", "marketplace_explanations", ["subject_id", "target_id"])
    op.create_index("ix_marketplace_explanations_mode", "marketplace_explanations", ["mode"])


def downgrade() -> None:
    for tbl in (
        "marketplace_explanations",
        "marketplace_events",
        "marketplace_inquiries",
        "marketplace_proposals",
        "buyer_profiles",
        "patent_listings",
    ):
        op.drop_table(tbl)
