"""Add workspace-scoped display IDs for tickets (e.g. PROJ-00001).

Adds:
  workspace.ticket_prefix  — configurable prefix per workspace (default TKT)
  workspace.ticket_counter — auto-incrementing per-workspace counter
  ticket.workspace_id      — denormalised FK (backfilled from run.workspace_id)
  ticket.display_id        — human-readable scoped ID (e.g. TKT-00001)
"""
import sqlalchemy as sa
from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("workspace") as batch:
            batch.add_column(sa.Column("ticket_prefix", sa.String(), nullable=False, server_default="TKT"))
            batch.add_column(sa.Column("ticket_counter", sa.Integer(), nullable=False, server_default="0"))
        with op.batch_alter_table("ticket") as batch:
            batch.add_column(sa.Column("workspace_id", sa.Integer(), nullable=True))
            batch.add_column(sa.Column("display_id", sa.String(), nullable=True))
    else:
        op.add_column("workspace", sa.Column("ticket_prefix", sa.String(), nullable=False, server_default="TKT"))
        op.add_column("workspace", sa.Column("ticket_counter", sa.Integer(), nullable=False, server_default="0"))
        op.add_column("ticket", sa.Column("workspace_id", sa.Integer(), nullable=True))
        op.add_column("ticket", sa.Column("display_id", sa.String(), nullable=True))

    op.create_index("ix_ticket_workspace_id", "ticket", ["workspace_id"])
    op.create_index("ix_ticket_display_id", "ticket", ["display_id"])

    # Backfill ticket.workspace_id from run.workspace_id
    op.execute(
        sa.text(
            "UPDATE ticket SET workspace_id = ("
            "  SELECT r.workspace_id FROM run r WHERE r.run_id = ticket.run_id"
            ")"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_display_id", table_name="ticket")
    op.drop_index("ix_ticket_workspace_id", table_name="ticket")

    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("ticket") as batch:
            batch.drop_column("display_id")
            batch.drop_column("workspace_id")
        with op.batch_alter_table("workspace") as batch:
            batch.drop_column("ticket_counter")
            batch.drop_column("ticket_prefix")
    else:
        op.drop_column("ticket", "display_id")
        op.drop_column("ticket", "workspace_id")
        op.drop_column("workspace", "ticket_counter")
        op.drop_column("workspace", "ticket_prefix")
