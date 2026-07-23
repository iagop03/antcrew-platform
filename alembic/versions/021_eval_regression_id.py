"""add regression_id to eval_run for prompt regression batches

Revision ID: 021
Revises: 020
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("eval_run", sa.Column("regression_id", sa.Text(), nullable=True))
    op.create_index("ix_eval_run_regression_id", "eval_run", ["regression_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_run_regression_id", table_name="eval_run")
    op.drop_column("eval_run", "regression_id")
