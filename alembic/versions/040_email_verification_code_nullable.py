"""Make email_verification.code nullable (plaintext no longer stored, only code_hash)

Revision ID: 040
Revises: 039
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("email_verification") as batch:
            batch.alter_column("code", existing_type=sa.String(), nullable=True)
    else:
        op.alter_column("email_verification", "code", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Restore NOT NULL — only safe if no NULL rows exist
    if dialect == "sqlite":
        with op.batch_alter_table("email_verification") as batch:
            batch.alter_column("code", existing_type=sa.String(), nullable=False)
    else:
        op.alter_column("email_verification", "code", existing_type=sa.String(), nullable=False)
