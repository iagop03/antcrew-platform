"""add hitl column to run_template

Revision ID: 003
Revises: 002
Create Date: 2026-07-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("run_template") as batch:
        batch.add_column(sa.Column("hitl", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("run_template") as batch:
        batch.drop_column("hitl")
