"""add run_id to eval_run (links eval to stub Run for dashboard + cost tracking)

Revision ID: 007
Revises: 006
Create Date: 2026-07-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c["name"] for c in inspector.get_columns("eval_run")]
    if "run_id" not in cols:
        with op.batch_alter_table("eval_run") as batch:
            batch.add_column(sa.Column("run_id", sa.String(), nullable=True, index=True))


def downgrade() -> None:
    with op.batch_alter_table("eval_run") as batch:
        batch.drop_column("run_id")
