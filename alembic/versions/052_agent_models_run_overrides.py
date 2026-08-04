"""Add agent_models to workspace; add model_overrides to run.

Revision ID: 052
Revises: 051
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column("agent_models", sa.JSON(), nullable=True),
    )
    op.add_column(
        "run",
        sa.Column("model_overrides", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run", "model_overrides")
    op.drop_column("workspace", "agent_models")
