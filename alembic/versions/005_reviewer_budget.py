"""add assigned_to to hitl_review and max_cost_usd to workspace

Revision ID: 005
Revises: 004
Create Date: 2026-07-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("hitl_review") as batch:
        batch.add_column(sa.Column("assigned_to", sa.String(), nullable=True))

    with op.batch_alter_table("workspace") as batch:
        batch.add_column(sa.Column("max_cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("hitl_review") as batch:
        batch.drop_column("assigned_to")

    with op.batch_alter_table("workspace") as batch:
        batch.drop_column("max_cost_usd")
