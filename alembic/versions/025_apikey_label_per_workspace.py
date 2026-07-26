"""api_key label unique per workspace (not globally)

Revision ID: 025
Revises: 024
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # PostgreSQL auto-names the inline column unique=True as "{table}_{col}_key"
        op.drop_constraint("api_key_label_key", "api_key", type_="unique")
        op.create_unique_constraint(
            "uq_api_key_label_workspace", "api_key", ["label", "workspace_id"]
        )
    else:
        # SQLite: batch mode recreates the table; alter_column removes the inline unique
        with op.batch_alter_table("api_key") as batch_op:
            batch_op.alter_column("label", existing_type=sa.String(), unique=False)
            batch_op.create_unique_constraint(
                "uq_api_key_label_workspace", ["label", "workspace_id"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("uq_api_key_label_workspace", "api_key", type_="unique")
        op.create_unique_constraint("api_key_label_key", "api_key", ["label"])
    else:
        with op.batch_alter_table("api_key") as batch_op:
            batch_op.drop_constraint("uq_api_key_label_workspace", type_="unique")
            batch_op.alter_column("label", existing_type=sa.String(), unique=True)
