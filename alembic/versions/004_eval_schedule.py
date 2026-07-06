"""add eval_schedule table

Revision ID: 004
Revises: 003
Create Date: 2026-07-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_schedule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("request", sa.String(), nullable=False),
        sa.Column("interval_hours", sa.Float(), nullable=False, server_default="24"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("expect_min_tickets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expect_min_code_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_eval_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_schedule_name", "eval_schedule", ["name"])


def downgrade() -> None:
    op.drop_index("ix_eval_schedule_name", "eval_schedule")
    op.drop_table("eval_schedule")
