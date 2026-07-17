"""abstract_status + indian_patent_number on patent_listings

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-29

Two additive columns:
  - abstract_status (TEXT, default 'none'): tracks where the abstract came
    from. Values: 'none' (no abstract yet), 'pasted' (inventor entered it
    via the UI), 'fetched' (auto-pulled from an upstream source). Stored
    explicitly so the no-abstract state is visible in data, not just
    inferred from NULL abstract column.
  - indian_patent_number (TEXT): canonical legal patent identifier
    extracted from ip.iitm.ac.in TTO pages (e.g. 'IN 567476'). Not used by
    the matching engine; it's metadata that makes listings credible to
    buyers and is the key for future commercial-API lookups.

Both columns are nullable. Default 'none' on abstract_status keeps prior
listings consistent without a separate backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "patent_listings",
        sa.Column(
            "abstract_status",
            sa.String(32),
            server_default="none",
            comment="none / pasted / fetched - where the abstract came from",
        ),
    )
    op.add_column(
        "patent_listings",
        sa.Column(
            "indian_patent_number",
            sa.String(64),
            comment="canonical Indian patent number (e.g. 'IN 567476') from TTO page",
        ),
    )
    op.create_index(
        "ix_patent_listings_indian_patent_number",
        "patent_listings",
        ["indian_patent_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_patent_listings_indian_patent_number", table_name="patent_listings")
    op.drop_column("patent_listings", "indian_patent_number")
    op.drop_column("patent_listings", "abstract_status")
