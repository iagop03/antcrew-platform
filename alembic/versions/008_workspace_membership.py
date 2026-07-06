"""008 — add workspace_membership table for multi-workspace API key scoping.

Revision ID: 008
Revises: 007
Create Date: 2026-01-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = [t for t in inspector.get_table_names()]
    if "workspace_membership" in existing:
        return
    op.create_table(
        "workspace_membership",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column("api_key_id", sa.Integer(), nullable=False, index=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("workspace_membership")
