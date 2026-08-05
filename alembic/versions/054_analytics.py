"""Analytics improvements: run.llm_key_mode snapshot + event.event_type index.

Revision ID: 054
Revises: 053
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Snapshot the workspace's LLM mode at run creation time so historical
    # analytics are not silently rewritten when a workspace changes billing mode.
    op.add_column("run", sa.Column("llm_key_mode", sa.String(), nullable=True))

    # Ensure ix_event_event_type exists — SQLModel declares Field(index=True)
    # but the index may not have been emitted in earlier manual migrations.
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    existing = {i["name"] for i in sa_inspect(bind).get_indexes("event")}
    if "ix_event_event_type" not in existing:
        op.create_index("ix_event_event_type", "event", ["event_type"])


def downgrade() -> None:
    op.drop_column("run", "llm_key_mode")
    # Intentionally leave ix_event_event_type — it is a valid performance index.
