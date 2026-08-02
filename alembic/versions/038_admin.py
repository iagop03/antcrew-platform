"""Admin panel: is_platform_admin on user, multiplier overrides on workspace, campaign table.

Adds:
  user.is_platform_admin        — boolean flag to grant admin panel access
  workspace.cost_multiplier_override — manual multiplier override (NULL = use default)
  workspace.multiplier_locked        — if True, ignores active campaigns
  campaign table                     — time-limited multiplier campaigns
"""
import sqlalchemy as sa
from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("user") as batch:
            batch.add_column(sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default="0"))
        with op.batch_alter_table("workspace") as batch:
            batch.add_column(sa.Column("cost_multiplier_override", sa.Float(), nullable=True))
            batch.add_column(sa.Column("multiplier_locked", sa.Boolean(), nullable=False, server_default="0"))
    else:
        op.add_column("user", sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default="false"))
        op.add_column("workspace", sa.Column("cost_multiplier_override", sa.Float(), nullable=True))
        op.add_column("workspace", sa.Column("multiplier_locked", sa.Boolean(), nullable=False, server_default="false"))

    op.create_table(
        "campaign",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("multiplier", sa.Float(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("target", sa.String(), nullable=False, server_default="all"),  # all | new
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true" if bind.dialect.name != "sqlite" else "1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("campaign")

    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("workspace") as batch:
            batch.drop_column("multiplier_locked")
            batch.drop_column("cost_multiplier_override")
        with op.batch_alter_table("user") as batch:
            batch.drop_column("is_platform_admin")
    else:
        op.drop_column("workspace", "multiplier_locked")
        op.drop_column("workspace", "cost_multiplier_override")
        op.drop_column("user", "is_platform_admin")
