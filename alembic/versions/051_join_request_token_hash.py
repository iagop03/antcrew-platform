"""Add token_hash to workspace_join_request; allow token nullable.

Revision ID: 051
Revises: 050
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_join_request",
        sa.Column("token_hash", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_workspace_join_request_token_hash",
        "workspace_join_request",
        ["token_hash"],
        unique=True,
    )
    # Allow token to be NULL so new rows can omit the plaintext token.
    op.alter_column(
        "workspace_join_request",
        "token",
        nullable=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_join_request_token_hash",
        table_name="workspace_join_request",
    )
    op.drop_column("workspace_join_request", "token_hash")
    op.alter_column(
        "workspace_join_request",
        "token",
        nullable=False,
    )
