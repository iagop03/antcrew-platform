"""012 — add api_key.key_prefix for O(1) bcrypt lookup.

Revision ID: 012
Revises: 011
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {c["name"] for c in sa.inspect(conn).get_columns("api_key")}
    if "key_prefix" not in cols:
        op.add_column("api_key", sa.Column("key_prefix", sa.String(), nullable=True))
        op.create_index("ix_api_key_key_prefix", "api_key", ["key_prefix"])


def downgrade() -> None:
    op.drop_index("ix_api_key_key_prefix", table_name="api_key")
    op.drop_column("api_key", "key_prefix")
