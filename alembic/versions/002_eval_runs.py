"""add eval_run table

Revision ID: 002
Revises: 001
Create Date: 2026-06-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("eval_id", sa.String(), nullable=False, unique=True),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("request", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("model", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("elapsed_ms", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_eval_run_eval_id", "eval_run", ["eval_id"])


def downgrade() -> None:
    op.drop_table("eval_run")
