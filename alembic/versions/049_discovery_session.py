"""Add discovery_session table.

Revision ID: 049
Revises: 048
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_session",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspace.id"), nullable=True),
        sa.Column("model", sa.String(), nullable=False, server_default="claude"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("context_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("current_question", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_discovery_session_session_id", "discovery_session", ["session_id"], unique=True
    )
    op.create_index(
        "ix_discovery_session_workspace_id", "discovery_session", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_discovery_session_workspace_id", table_name="discovery_session")
    op.drop_index("ix_discovery_session_session_id", table_name="discovery_session")
    op.drop_table("discovery_session")
