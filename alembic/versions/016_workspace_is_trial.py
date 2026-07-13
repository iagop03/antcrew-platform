"""016 workspace is_trial — free-trial credit state field

Revision ID: 016
Revises: 015
Create Date: 2026-07-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workspace") as batch_op:
        # Existing workspaces default to False (not trial) — avoids changing multiplier for paying users.
        # New workspaces created by the ORM get True from the Python field default.
        batch_op.add_column(
            sa.Column("is_trial", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("workspace") as batch_op:
        batch_op.drop_column("is_trial")