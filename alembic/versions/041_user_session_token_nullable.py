"""Make user_session.token nullable (plaintext no longer stored since migration 033)."""
import sqlalchemy as sa
from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("user_session") as batch_op:
            batch_op.alter_column("token", existing_type=sa.String(), nullable=True)
    else:
        op.alter_column("user_session", "token", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        with op.batch_alter_table("user_session") as batch_op:
            batch_op.alter_column("token", existing_type=sa.String(), nullable=False)
    else:
        op.alter_column("user_session", "token", existing_type=sa.String(), nullable=False)
