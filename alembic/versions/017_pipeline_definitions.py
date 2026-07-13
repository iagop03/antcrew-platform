"""add pipeline_def table

Revision ID: 017
Revises: 016
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_def",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_def_workspace_id", "pipeline_def", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_def_workspace_id", table_name="pipeline_def")
    op.drop_table("pipeline_def")
