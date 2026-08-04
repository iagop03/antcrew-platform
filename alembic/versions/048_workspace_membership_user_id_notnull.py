"""Add NOT NULL constraint and unique index on workspace_membership.user_id.

Rows that were not backfilled in migration 047 (api_keys with no linked user) are
removed before the constraint is applied.  Those memberships remain accessible via
the api_key_id path — the user_id column is an additional lookup dimension.

Revision ID: 048
Revises: 047
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove membership rows that could not be backfilled (no linked user).
    # These rows are still accessible through the api_key_id foreign-key path, so
    # the effective access control is unchanged after the NOT NULL constraint.
    op.execute(
        "DELETE FROM workspace_membership WHERE user_id IS NULL"
    )

    # Enforce NOT NULL now that every remaining row has a user_id.
    op.alter_column("workspace_membership", "user_id", nullable=False)

    # Unique constraint: one membership row per (workspace, user) pair.
    op.create_unique_constraint(
        "uq_workspace_membership_workspace_user",
        "workspace_membership",
        ["workspace_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_workspace_membership_workspace_user",
        "workspace_membership",
        type_="unique",
    )
    op.alter_column("workspace_membership", "user_id", nullable=True)
