"""Add platform_config table with billing rate defaults.

Revision ID: 042
Revises: 041
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_config",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("managed_cost_multiplier", sa.Float(), nullable=False, server_default="3.0"),
        sa.Column("byok_service_multiplier", sa.Float(), nullable=False, server_default="0.4"),
        sa.Column("proxy_service_multiplier", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    # Insert the singleton row with defaults
    op.execute("INSERT INTO platform_config (id, managed_cost_multiplier, byok_service_multiplier, proxy_service_multiplier) VALUES (1, 3.0, 0.4, 0.7)")


def downgrade() -> None:
    op.drop_table("platform_config")
