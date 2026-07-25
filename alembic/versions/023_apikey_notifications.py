"""add slack_user_id and telegram_chat_id to api_key

Revision ID: 023
Revises: 022
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_key", sa.Column("slack_user_id", sa.Text(), nullable=True))
    op.add_column("api_key", sa.Column("telegram_chat_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_key", "telegram_chat_id")
    op.drop_column("api_key", "slack_user_id")
