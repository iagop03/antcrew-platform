"""add user, user_session tables and api_key.user_id

Revision ID: 022
Revises: 021
Create Date: 2026-07-25
"""
from alembic import op
import sqlalchemy as sa

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)

    op.create_table(
        "user_session",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.Text(), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("api_key_id", sa.Integer(), sa.ForeignKey("api_key.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_user_session_token", "user_session", ["token"], unique=True)
    op.create_index("ix_user_session_user_id", "user_session", ["user_id"])

    op.add_column("api_key", sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("api_key", "user_id")
    op.drop_index("ix_user_session_user_id", table_name="user_session")
    op.drop_index("ix_user_session_token", table_name="user_session")
    op.drop_table("user_session")
    op.drop_index("ix_user_email", table_name="user")
    op.drop_table("user")
