"""Create custom_agent_def table (workspace-scoped TemplateAgent definitions)"""
import sqlalchemy as sa
from alembic import op

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_agent_def",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False, index=True),
        sa.Column("agent_type", sa.Text(), nullable=False, index=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False, server_default="#7c3aed"),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("role_description", sa.Text(), nullable=True),
        sa.Column("phase", sa.Text(), nullable=False, server_default="build"),
        sa.Column("glyph", sa.Text(), nullable=False, server_default="✦"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "agent_type", name="uq_custom_agent_ws_type"),
    )


def downgrade() -> None:
    op.drop_table("custom_agent_def")
