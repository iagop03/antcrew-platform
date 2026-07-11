"""015 llm provider base url — add base_url column to llm_provider_key

Revision ID: 015
Revises: 014
Create Date: 2026-07-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_provider_key") as batch_op:
        batch_op.add_column(sa.Column("base_url", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("llm_provider_key") as batch_op:
        batch_op.drop_column("base_url")