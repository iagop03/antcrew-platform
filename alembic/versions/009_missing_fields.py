"""009 — add all fields present in models but missing from migrations 001-008.

Covers: Workspace (Slack, Stripe, budget, HITL), run_template.repo_url,
api_key.role, eval_run.judge_model, eval_schedule (model/judge_model/
expect_review_verdict), webhook_config table, webhook_event table.

Revision ID: 009
Revises: 008
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def _cols(table: str) -> set[str]:
    conn = op.get_bind()
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _tables() -> set[str]:
    conn = op.get_bind()
    return set(sa.inspect(conn).get_table_names())


def upgrade() -> None:
    # ------------------------------------------------------------------ workspace
    ws_cols = _cols("workspace")
    with op.batch_alter_table("workspace") as batch:
        if "total_cost_usd" not in ws_cols:
            batch.add_column(sa.Column("total_cost_usd", sa.Float(), nullable=False,
                                       server_default="0.0"))
        if "default_repo_url" not in ws_cols:
            batch.add_column(sa.Column("default_repo_url", sa.String(), nullable=True))
        if "slack_webhook_url" not in ws_cols:
            batch.add_column(sa.Column("slack_webhook_url", sa.String(), nullable=True))
        if "slack_channel_id" not in ws_cols:
            batch.add_column(sa.Column("slack_channel_id", sa.String(), nullable=True))
        if "slack_bot_token_enc" not in ws_cols:
            batch.add_column(sa.Column("slack_bot_token_enc", sa.String(), nullable=True))
        if "slack_app_token_enc" not in ws_cols:
            batch.add_column(sa.Column("slack_app_token_enc", sa.String(), nullable=True))
        if "hitl_default" not in ws_cols:
            batch.add_column(sa.Column("hitl_default", sa.Boolean(), nullable=False,
                                       server_default="0"))
        if "hitl_timeout_s" not in ws_cols:
            batch.add_column(sa.Column("hitl_timeout_s", sa.Float(), nullable=True))
        if "stripe_customer_id" not in ws_cols:
            batch.add_column(sa.Column("stripe_customer_id", sa.String(), nullable=True))
        if "stripe_subscription_id" not in ws_cols:
            batch.add_column(sa.Column("stripe_subscription_id", sa.String(), nullable=True))
        if "stripe_subscription_status" not in ws_cols:
            batch.add_column(sa.Column("stripe_subscription_status", sa.String(), nullable=True))

    conn = op.get_bind()
    existing_ix = {ix["name"] for ix in sa.inspect(conn).get_indexes("workspace")}
    if "ix_workspace_stripe_customer_id" not in existing_ix:
        op.create_index("ix_workspace_stripe_customer_id", "workspace", ["stripe_customer_id"])

    # ------------------------------------------------------------------ run_template
    rt_cols = _cols("run_template")
    with op.batch_alter_table("run_template") as batch:
        if "repo_url" not in rt_cols:
            batch.add_column(sa.Column("repo_url", sa.String(), nullable=True))

    # ------------------------------------------------------------------ api_key
    ak_cols = _cols("api_key")
    with op.batch_alter_table("api_key") as batch:
        if "role" not in ak_cols:
            batch.add_column(sa.Column("role", sa.String(), nullable=False,
                                       server_default="write"))

    # ------------------------------------------------------------------ eval_run
    er_cols = _cols("eval_run")
    with op.batch_alter_table("eval_run") as batch:
        if "judge_model" not in er_cols:
            batch.add_column(sa.Column("judge_model", sa.String(), nullable=False,
                                       server_default=""))

    # ------------------------------------------------------------------ eval_schedule
    es_cols = _cols("eval_schedule")
    with op.batch_alter_table("eval_schedule") as batch:
        if "model" not in es_cols:
            batch.add_column(sa.Column("model", sa.String(), nullable=False, server_default=""))
        if "judge_model" not in es_cols:
            batch.add_column(sa.Column("judge_model", sa.String(), nullable=False,
                                       server_default=""))
        if "expect_review_verdict" not in es_cols:
            batch.add_column(sa.Column("expect_review_verdict", sa.String(), nullable=False,
                                       server_default=""))

    # ------------------------------------------------------------------ webhook_config
    if "webhook_config" not in _tables():
        op.create_table(
            "webhook_config",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("workspace_id", sa.Integer(), nullable=False),
            sa.Column("url", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_webhook_config_workspace_id", "webhook_config", ["workspace_id"])

    # ------------------------------------------------------------------ webhook_event
    if "webhook_event" not in _tables():
        op.create_table(
            "webhook_event",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("webhook_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
        )
        op.create_index("ix_webhook_event_webhook_id", "webhook_event", ["webhook_id"])
        op.create_index("ix_webhook_event_event_type", "webhook_event", ["event_type"])


def downgrade() -> None:
    op.drop_table("webhook_event")
    op.drop_table("webhook_config")
    with op.batch_alter_table("eval_schedule") as batch:
        batch.drop_column("expect_review_verdict")
        batch.drop_column("judge_model")
        batch.drop_column("model")
    with op.batch_alter_table("eval_run") as batch:
        batch.drop_column("judge_model")
    with op.batch_alter_table("api_key") as batch:
        batch.drop_column("role")
    with op.batch_alter_table("run_template") as batch:
        batch.drop_column("repo_url")
    op.drop_index("ix_workspace_stripe_customer_id", table_name="workspace")
    with op.batch_alter_table("workspace") as batch:
        batch.drop_column("stripe_subscription_status")
        batch.drop_column("stripe_subscription_id")
        batch.drop_column("stripe_customer_id")
        batch.drop_column("hitl_timeout_s")
        batch.drop_column("hitl_default")
        batch.drop_column("slack_app_token_enc")
        batch.drop_column("slack_bot_token_enc")
        batch.drop_column("slack_channel_id")
        batch.drop_column("slack_webhook_url")
        batch.drop_column("default_repo_url")
        batch.drop_column("total_cost_usd")
