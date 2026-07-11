"""014 billing provider — neutral subscription_status + MoR lane fields

Revision ID: 014
Revises: 013
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename stripe_subscription_status → subscription_status (provider-neutral)
    with op.batch_alter_table("workspace") as batch_op:
        batch_op.alter_column(
            "stripe_subscription_status",
            new_column_name="subscription_status",
            existing_type=sa.Text(),
            existing_nullable=True,
        )

    # Add billing lane fields
    with op.batch_alter_table("workspace") as batch_op:
        batch_op.add_column(sa.Column("billing_provider", sa.Text(), nullable=False, server_default="mor"))
        batch_op.add_column(sa.Column("mor_customer_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("mor_subscription_id", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workspace") as batch_op:
        batch_op.drop_column("mor_subscription_id")
        batch_op.drop_column("mor_customer_id")
        batch_op.drop_column("billing_provider")

    with op.batch_alter_table("workspace") as batch_op:
        batch_op.alter_column(
            "subscription_status",
            new_column_name="stripe_subscription_status",
            existing_type=sa.Text(),
            existing_nullable=True,
        )
