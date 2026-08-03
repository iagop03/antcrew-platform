"""Add model column to run table for LLM model analytics.

Revision ID: 044
Revises: 043
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run", sa.Column("model", sa.String(), nullable=True))
    op.create_index("ix_run_model", "run", ["model"])


def downgrade() -> None:
    op.drop_index("ix_run_model", "run")
    op.drop_column("run", "model")
