"""Add run_schedule table for recurring engine runs."""
import sqlalchemy as sa
from alembic import op

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_schedule",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("model", sa.String(), nullable=False, server_default="claude"),
        sa.Column("conditions", sa.Text(), nullable=True),
        sa.Column("full", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("max_cost_usd", sa.Float(), nullable=True),
        sa.Column("cron_expr", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("next_run_at", sa.DateTime(), nullable=False),
        sa.Column("last_run_id", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_run_schedule_workspace_id", "run_schedule", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_run_schedule_workspace_id", table_name="run_schedule")
    op.drop_table("run_schedule")
