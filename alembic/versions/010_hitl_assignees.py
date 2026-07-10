"""010 — add hitl_review_assignee table for multi-reviewer HITL.

Revision ID: 010
Revises: 009
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {t for t in sa.inspect(conn).get_table_names()}
    if "hitl_review_assignee" not in tables:
        op.create_table(
            "hitl_review_assignee",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("review_id", sa.String(), nullable=False),
            sa.Column("assignee_label", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_hitl_review_assignee_review_id", "hitl_review_assignee", ["review_id"])
        op.create_index("ix_hitl_review_assignee_label", "hitl_review_assignee", ["assignee_label"])


def downgrade() -> None:
    op.drop_index("ix_hitl_review_assignee_label", table_name="hitl_review_assignee")
    op.drop_index("ix_hitl_review_assignee_review_id", table_name="hitl_review_assignee")
    op.drop_table("hitl_review_assignee")
