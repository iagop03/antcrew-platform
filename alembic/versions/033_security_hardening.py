"""Security hardening: hash session tokens, HITL client tokens, and email codes at rest."""
import hashlib

import sqlalchemy as sa
from alembic import op

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. user_session: add token_hash ─────────────────────────────────────
    op.add_column("user_session", sa.Column("token_hash", sa.String(), nullable=True))
    try:
        op.create_index("ix_user_session_token_hash", "user_session", ["token_hash"], unique=True)
    except Exception:
        pass  # index may already exist on reruns

    # Backfill: sha256(token) for every existing session row.
    # This keeps existing sessions valid — the cookie contains the plaintext,
    # and sha256(cookie_value) == token_hash stored here.
    rows = conn.execute(
        sa.text("SELECT id, token FROM user_session WHERE token IS NOT NULL AND token_hash IS NULL")
    ).fetchall()
    for row in rows:
        if row[1]:
            conn.execute(
                sa.text("UPDATE user_session SET token_hash = :h WHERE id = :id"),
                {"h": _sha256(row[1]), "id": row[0]},
            )

    # ── 2. email_verification: add code_hash ─────────────────────────────────
    # No backfill — verification codes expire in 15 minutes; stale ones become
    # invalid and the user can request a fresh code.
    op.add_column("email_verification", sa.Column("code_hash", sa.String(), nullable=True))
    try:
        op.create_index("ix_email_verification_code_hash", "email_verification", ["code_hash"])
    except Exception:
        pass

    # ── 3. hitl_review: add client_token_hash ────────────────────────────────
    op.add_column("hitl_review", sa.Column("client_token_hash", sa.String(), nullable=True))
    try:
        op.create_index(
            "ix_hitl_review_client_token_hash", "hitl_review", ["client_token_hash"], unique=True
        )
    except Exception:
        pass

    # Backfill: existing client_token values are plaintext — hash them so
    # existing review links continue to work after the application is updated.
    rows = conn.execute(
        sa.text(
            "SELECT id, client_token FROM hitl_review "
            "WHERE client_token IS NOT NULL AND client_token_hash IS NULL"
        )
    ).fetchall()
    for row in rows:
        if row[1]:
            conn.execute(
                sa.text("UPDATE hitl_review SET client_token_hash = :h WHERE id = :id"),
                {"h": _sha256(row[1]), "id": row[0]},
            )


def downgrade() -> None:
    try:
        op.drop_index("ix_user_session_token_hash", table_name="user_session")
    except Exception:
        pass
    op.drop_column("user_session", "token_hash")

    try:
        op.drop_index("ix_email_verification_code_hash", table_name="email_verification")
    except Exception:
        pass
    op.drop_column("email_verification", "code_hash")

    try:
        op.drop_index("ix_hitl_review_client_token_hash", table_name="hitl_review")
    except Exception:
        pass
    op.drop_column("hitl_review", "client_token_hash")
