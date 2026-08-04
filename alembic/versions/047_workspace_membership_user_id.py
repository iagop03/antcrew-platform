"""Add user_id (nullable) to workspace_membership with backfill via api_key.

Revision ID: 047
Revises: 046
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add nullable user_id column with FK to user table
    op.add_column(
        "workspace_membership",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
    )

    # 2. Backfill: propagate user_id from the associated api_key row.
    #    api_key.user_id is populated at registration time (migration 022+); rows for
    #    anonymous or service keys without a linked user remain NULL and will be
    #    cleaned up in migration 048 before the NOT NULL constraint is applied.
    op.execute(
        """
        UPDATE workspace_membership
        SET user_id = ak.user_id
        FROM api_key ak
        WHERE workspace_membership.api_key_id = ak.id
          AND ak.user_id IS NOT NULL
        """
    )

    # 3. Create index for fast user-scoped membership lookups
    op.create_index(
        "ix_workspace_membership_user_id", "workspace_membership", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_membership_user_id", table_name="workspace_membership")
    op.drop_column("workspace_membership", "user_id")
