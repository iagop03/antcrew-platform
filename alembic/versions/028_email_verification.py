"""Add email_verification, workspace_invite, workspace_join_request tables; user.email_verified_at

Revision ID: 028
Revises: 027
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Add email_verified_at to user
    if dialect == "sqlite":
        with op.batch_alter_table("user") as batch:
            batch.add_column(sa.Column("email_verified_at", sa.DateTime(), nullable=True))
    else:
        op.add_column("user", sa.Column("email_verified_at", sa.DateTime(), nullable=True))

    # email_verification table
    op.create_table(
        "email_verification",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_email_verification_user_id", "email_verification", ["user_id"])

    # workspace_invite table
    op.create_table(
        "workspace_invite",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("invitee_email", sa.String(), nullable=False),
        sa.Column("inviter_email", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="write"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_workspace_invite_token", "workspace_invite", ["token"], unique=True)
    op.create_index("ix_workspace_invite_workspace_id", "workspace_invite", ["workspace_id"])
    op.create_index("ix_workspace_invite_invitee_email", "workspace_invite", ["invitee_email"])

    # workspace_join_request table
    op.create_table(
        "workspace_join_request",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("requester_email", sa.String(), nullable=False),
        sa.Column("requested_role", sa.String(), nullable=False, server_default="write"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_workspace_join_request_token", "workspace_join_request", ["token"], unique=True)
    op.create_index("ix_workspace_join_request_workspace_id", "workspace_join_request", ["workspace_id"])
    op.create_index("ix_workspace_join_request_requester_email", "workspace_join_request", ["requester_email"])


def downgrade() -> None:
    op.drop_index("ix_workspace_join_request_requester_email", table_name="workspace_join_request")
    op.drop_index("ix_workspace_join_request_workspace_id", table_name="workspace_join_request")
    op.drop_index("ix_workspace_join_request_token", table_name="workspace_join_request")
    op.drop_table("workspace_join_request")

    op.drop_index("ix_workspace_invite_invitee_email", table_name="workspace_invite")
    op.drop_index("ix_workspace_invite_workspace_id", table_name="workspace_invite")
    op.drop_index("ix_workspace_invite_token", table_name="workspace_invite")
    op.drop_table("workspace_invite")

    op.drop_index("ix_email_verification_user_id", table_name="email_verification")
    op.drop_table("email_verification")

    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("user") as batch:
            batch.drop_column("email_verified_at")
    else:
        op.drop_column("user", "email_verified_at")
