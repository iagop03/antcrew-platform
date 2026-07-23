"""add compare_run table for model-diff comparisons

Revision ID: 020
Revises: 019
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compare_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("compare_id", sa.Text(), nullable=False),
        sa.Column("run_id_a", sa.Text(), nullable=False),
        sa.Column("run_id_b", sa.Text(), nullable=False),
        sa.Column("model_a", sa.Text(), nullable=False),
        sa.Column("model_b", sa.Text(), nullable=False),
        sa.Column("team", sa.Text(), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_compare_run_compare_id", "compare_run", ["compare_id"], unique=True)
    op.create_index("ix_compare_run_run_id_a", "compare_run", ["run_id_a"])
    op.create_index("ix_compare_run_run_id_b", "compare_run", ["run_id_b"])


def downgrade() -> None:
    op.drop_index("ix_compare_run_run_id_b", table_name="compare_run")
    op.drop_index("ix_compare_run_run_id_a", table_name="compare_run")
    op.drop_index("ix_compare_run_compare_id", table_name="compare_run")
    op.drop_table("compare_run")
