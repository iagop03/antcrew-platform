"""Add TOTP MFA columns to user table."""
import sqlalchemy as sa
from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("totp_secret", sa.String(), nullable=True))
    op.add_column("user", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("user", "mfa_enabled")
    op.drop_column("user", "totp_secret")
