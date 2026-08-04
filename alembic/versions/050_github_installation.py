"""Add github_installation table.

Revision ID: 050
Revises: 049
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_installation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspace.id"), nullable=False),
        sa.Column("installation_id", sa.Integer(), nullable=False),
        sa.Column("account_login", sa.String(), nullable=False),
        sa.Column("account_type", sa.String(), nullable=False, server_default="Organization"),
        sa.Column("repo_allowlist", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_github_installation_workspace_id", "github_installation", ["workspace_id"]
    )
    op.create_index(
        "ix_github_installation_installation_id", "github_installation", ["installation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_github_installation_installation_id", table_name="github_installation")
    op.drop_index("ix_github_installation_workspace_id", table_name="github_installation")
    op.drop_table("github_installation")
