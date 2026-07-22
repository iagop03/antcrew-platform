"""add client_label to run for per-client budget breakdown

Revision ID: 018
Revises: 017
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run", sa.Column("client_label", sa.Text(), nullable=True))
    op.create_index("ix_run_client_label", "run", ["client_label"])


def downgrade() -> None:
    op.drop_index("ix_run_client_label", table_name="run")
    op.drop_column("run", "client_label")
