"""drop workspace.budget_exceeded (computed from total_cost_usd in API layer)

Revision ID: 006
Revises: 005
Create Date: 2026-07-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    cols = [c["name"] for c in inspector.get_columns("workspace")]
    if "budget_exceeded" in cols:
        with op.batch_alter_table("workspace") as batch:
            batch.drop_column("budget_exceeded")


def downgrade() -> None:
    with op.batch_alter_table("workspace") as batch:
        batch.add_column(
            sa.Column("budget_exceeded", sa.Boolean(), nullable=False, server_default="0")
        )
