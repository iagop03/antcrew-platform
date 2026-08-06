"""Add tokens_in / tokens_out to run table for materialised token analytics.

token counts come from agent.end event payloads (antcrew-engine >= 0.3.13).
The listener accumulates them per-run so analytics queries avoid JSON extraction
over the full event table.

Revision ID: 055
Revises: 054
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("run", sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("run", sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("run", "tokens_in")
    op.drop_column("run", "tokens_out")
