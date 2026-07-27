"""Add proxy_url and proxy_token_enc to workspace (proxy LLM mode)"""
import sqlalchemy as sa
from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column("proxy_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "workspace",
        sa.Column("proxy_token_enc", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace", "proxy_token_enc")
    op.drop_column("workspace", "proxy_url")
