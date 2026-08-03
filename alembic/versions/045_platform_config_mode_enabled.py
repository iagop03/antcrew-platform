"""Add managed/byok/proxy enabled flags to platform_config.

Revision ID: 045
Revises: 044
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("platform_config", sa.Column("managed_enabled", sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("platform_config", sa.Column("byok_enabled",    sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("platform_config", sa.Column("proxy_enabled",   sa.Boolean(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("platform_config", "proxy_enabled")
    op.drop_column("platform_config", "byok_enabled")
    op.drop_column("platform_config", "managed_enabled")
