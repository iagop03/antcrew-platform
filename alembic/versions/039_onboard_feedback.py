"""Onboarding profile fields on user + user_feedback table.

Adds:
  user.use_case    — main use case declared at onboarding (nullable)
  user.team_size   — team size bucket declared at onboarding (nullable)
  user_feedback    — micro-feedback events (thumbs up/down + free text)
"""
import sqlalchemy as sa
from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("user") as batch:
            batch.add_column(sa.Column("use_case", sa.String(), nullable=True))
            batch.add_column(sa.Column("team_size", sa.String(), nullable=True))
    else:
        op.add_column("user", sa.Column("use_case", sa.String(), nullable=True))
        op.add_column("user", sa.Column("team_size", sa.String(), nullable=True))

    op.create_table(
        "user_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True, index=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True, index=True),
        sa.Column("context", sa.String(), nullable=False),   # run_complete | ticket_closed | run_failed | general
        sa.Column("helpful", sa.Boolean(), nullable=True),   # thumbs up/down; NULL = no rating
        sa.Column("message", sa.String(), nullable=True),    # free text
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("user_feedback")

    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("user") as batch:
            batch.drop_column("team_size")
            batch.drop_column("use_case")
    else:
        op.drop_column("user", "team_size")
        op.drop_column("user", "use_case")
