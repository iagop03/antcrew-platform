"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspace_slug", "workspace", ["slug"])

    op.create_table(
        "run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(), nullable=False, unique=True),
        sa.Column("thread_id", sa.String(), nullable=False, server_default="default"),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("request", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.JSON(), nullable=True),
    )
    op.create_index("ix_run_run_id", "run", ["run_id"])

    op.create_table(
        "ticket",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("acceptance_criteria", sa.String(), nullable=False, server_default=""),
        sa.Column("dependencies", sa.String(), nullable=False, server_default=""),
        sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("prd_title", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ticket_ticket_id", "ticket", ["ticket_id"])
    op.create_index("ix_ticket_run_id", "ticket", ["run_id"])

    op.create_table(
        "event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("thread_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.Float(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_event_run_id", "event", ["run_id"])
    op.create_index("ix_event_event_type", "event", ["event_type"])
    op.create_index("ix_event_run_id_ts", "event", ["run_id", "timestamp"])

    op.create_table(
        "api_key",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("label", sa.String(), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_key_label", "api_key", ["label"])

    op.create_table(
        "hitl_review",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.String(), nullable=False, unique=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("artifact_json", sa.String(), nullable=False, server_default="null"),
        sa.Column("options_json", sa.String(), nullable=False,
                  server_default='["approve","reject"]'),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("decision", sa.String(), nullable=True),
        sa.Column("edited_json", sa.String(), nullable=True),
        sa.Column("feedback", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_hitl_review_review_id", "hitl_review", ["review_id"])
    op.create_index("ix_hitl_review_run_id", "hitl_review", ["run_id"])

    op.create_table(
        "run_template",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("request", sa.String(), nullable=False),
        sa.Column("max_cost_usd", sa.Float(), nullable=True),
        sa.Column("workspace_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_template_name", "run_template", ["name"])

    op.create_table(
        "webhook_delivery",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_delivery_run_id", "webhook_delivery", ["run_id"])


def downgrade() -> None:
    op.drop_table("webhook_delivery")
    op.drop_table("run_template")
    op.drop_table("hitl_review")
    op.drop_table("api_key")
    op.drop_table("event")
    op.drop_table("ticket")
    op.drop_table("run")
    op.drop_table("workspace")
