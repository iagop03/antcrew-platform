"""add owner_user_id to workspace

Revision ID: 024
Revises: 023
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspace", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_index("ix_workspace_owner_user_id", "workspace", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_owner_user_id", table_name="workspace")
    op.drop_column("workspace", "owner_user_id")
