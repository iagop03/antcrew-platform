"""011 — add api_key.email + hitl_audit_entry table.

Revision ID: 011
Revises: 010
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # api_key.email
    existing_cols = {c["name"] for c in sa.inspect(conn).get_columns("api_key")}
    if "email" not in existing_cols:
        op.add_column("api_key", sa.Column("email", sa.String(), nullable=True))

    # hitl_audit_entry
    tables = {t for t in sa.inspect(conn).get_table_names()}
    if "hitl_audit_entry" not in tables:
        op.create_table(
            "hitl_audit_entry",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("review_id", sa.String(), nullable=False),
            sa.Column("actor_label", sa.String(), nullable=True),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("note", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_hitl_audit_review_id", "hitl_audit_entry", ["review_id"])


def downgrade() -> None:
    op.drop_index("ix_hitl_audit_review_id", table_name="hitl_audit_entry")
    op.drop_table("hitl_audit_entry")
    op.drop_column("api_key", "email")
