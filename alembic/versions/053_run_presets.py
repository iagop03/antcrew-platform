"""Add run_preset table for per-workspace pipeline model configurations.

Revision ID: 053
Revises: 052
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_preset",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("model_overrides", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_preset_workspace_id", "run_preset", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_run_preset_workspace_id", table_name="run_preset")
    op.drop_table("run_preset")
