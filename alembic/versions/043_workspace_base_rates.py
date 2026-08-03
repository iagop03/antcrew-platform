"""Snapshot billing base rates onto each workspace at creation time.

Adds base_managed_mult, base_byok_mult, base_proxy_mult to workspace.
Backfills existing rows with platform_config values (or hardcoded defaults).

Revision ID: 043
Revises: 042
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace", sa.Column("base_managed_mult", sa.Float(), nullable=True))
    op.add_column("workspace", sa.Column("base_byok_mult", sa.Float(), nullable=True))
    op.add_column("workspace", sa.Column("base_proxy_mult", sa.Float(), nullable=True))

    bind = op.get_bind()
    # Read current platform defaults (may not exist yet on fresh installs — use hardcoded fallback)
    row = bind.execute(sa.text(
        "SELECT managed_cost_multiplier, byok_service_multiplier, proxy_service_multiplier "
        "FROM platform_config WHERE id = 1"
    )).fetchone()
    managed = float(row[0]) if row else 3.0
    byok = float(row[1]) if row else 0.4
    proxy = float(row[2]) if row else 0.7

    bind.execute(sa.text(
        "UPDATE workspace SET base_managed_mult = :m, base_byok_mult = :b, base_proxy_mult = :p"
    ), {"m": managed, "b": byok, "p": proxy})


def downgrade() -> None:
    op.drop_column("workspace", "base_proxy_mult")
    op.drop_column("workspace", "base_byok_mult")
    op.drop_column("workspace", "base_managed_mult")
