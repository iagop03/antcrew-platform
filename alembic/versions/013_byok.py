"""Add BYOK: llm_key_mode to workspace + llm_provider_key table"""
import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column("llm_key_mode", sa.Text(), nullable=False, server_default="managed"),
    )
    op.create_table(
        "llm_provider_key",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("key_enc", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "provider", name="uq_llm_key_ws_provider"),
    )
    op.create_index("ix_llm_provider_key_workspace_id", "llm_provider_key", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_provider_key_workspace_id", "llm_provider_key")
    op.drop_table("llm_provider_key")
    op.drop_column("workspace", "llm_key_mode")
