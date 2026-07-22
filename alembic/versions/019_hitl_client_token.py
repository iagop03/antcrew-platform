"""add client_token to hitl_review for no-auth public review links

Revision ID: 019
Revises: 018
Create Date: 2026-07-22
"""
from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hitl_review", sa.Column("client_token", sa.Text(), nullable=True))
    op.create_index("ix_hitl_review_client_token", "hitl_review", ["client_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_hitl_review_client_token", table_name="hitl_review")
    op.drop_column("hitl_review", "client_token")
