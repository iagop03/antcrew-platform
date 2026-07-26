"""Add display_name to user table

Revision ID: 029
Revises: 028
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("display_name", sa.String(), nullable=True))


def downgrade():
    op.drop_column("user", "display_name")
