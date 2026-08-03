"""Add discount_days / max_participants to campaign; add campaign_enrollment table.

Revision ID: 046
Revises: 045
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaign", sa.Column("discount_days", sa.Integer(), nullable=True))
    op.add_column("campaign", sa.Column("max_participants", sa.Integer(), nullable=True))

    op.create_table(
        "campaign_enrollment",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaign.id"), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspace.id"), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(), nullable=False),
        sa.Column("discount_ends_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_campaign_enrollment_campaign_id", "campaign_enrollment", ["campaign_id"])
    op.create_index("ix_campaign_enrollment_workspace_id", "campaign_enrollment", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_campaign_enrollment_workspace_id", "campaign_enrollment")
    op.drop_index("ix_campaign_enrollment_campaign_id", "campaign_enrollment")
    op.drop_table("campaign_enrollment")
    op.drop_column("campaign", "max_participants")
    op.drop_column("campaign", "discount_days")
