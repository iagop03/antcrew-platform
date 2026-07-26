"""Add ticket_type, blocking, assignee to ticket table

Revision ID: 027
Revises: 026
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("ticket") as batch:
            batch.add_column(sa.Column("ticket_type", sa.String(), nullable=False, server_default="task"))
            batch.add_column(sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.false()))
            batch.add_column(sa.Column("assignee", sa.String(), nullable=True))
    else:
        op.add_column("ticket", sa.Column("ticket_type", sa.String(), nullable=False, server_default="task"))
        op.add_column("ticket", sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.add_column("ticket", sa.Column("assignee", sa.String(), nullable=True))

    op.create_index("ix_ticket_blocking", "ticket", ["blocking"])
    op.create_index("ix_ticket_type", "ticket", ["ticket_type"])


def downgrade() -> None:
    op.drop_index("ix_ticket_blocking", table_name="ticket")
    op.drop_index("ix_ticket_type", table_name="ticket")

    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("ticket") as batch:
            batch.drop_column("assignee")
            batch.drop_column("blocking")
            batch.drop_column("ticket_type")
    else:
        op.drop_column("ticket", "assignee")
        op.drop_column("ticket", "blocking")
        op.drop_column("ticket", "ticket_type")
