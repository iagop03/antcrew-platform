"""Hash workspace invite tokens at rest (sha256); legacy plaintext becomes nullable."""
import hashlib

import sqlalchemy as sa
from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def upgrade() -> None:
    conn = op.get_bind()

    # Add token_hash column (nullable until backfill, then retains NULL for legacy rows
    # where we don't have the original plaintext to hash).
    op.add_column("workspace_invite", sa.Column("token_hash", sa.String(), nullable=True))
    op.create_index("ix_workspace_invite_token_hash", "workspace_invite", ["token_hash"], unique=True)

    # Make token nullable so new invites can omit the plaintext.
    op.alter_column("workspace_invite", "token", nullable=True)

    # Backfill: for existing rows where token is plaintext (43-char urlsafe b64), hash it.
    rows = conn.execute(
        sa.text("SELECT id, token FROM workspace_invite WHERE token IS NOT NULL AND token_hash IS NULL")
    ).fetchall()
    for row_id, raw_token in rows:
        conn.execute(
            sa.text("UPDATE workspace_invite SET token_hash = :h, token = NULL WHERE id = :id"),
            {"h": _sha256(raw_token), "id": row_id},
        )


def downgrade() -> None:
    op.drop_index("ix_workspace_invite_token_hash", table_name="workspace_invite")
    op.drop_column("workspace_invite", "token_hash")
    op.alter_column("workspace_invite", "token", nullable=False)
