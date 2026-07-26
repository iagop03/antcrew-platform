"""security_audit_config, security_audit_run, audit_finding tables

Revision ID: 026
Revises: 025
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "security_audit_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("trigger_on_push", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("schedule_cron", sa.String(), nullable=True),
        sa.Column("schedule_next_run_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("github_repo", sa.String(), nullable=True),
        sa.Column("github_branch", sa.String(), nullable=False, server_default="main"),
        sa.Column("max_iterations", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("max_cost_usd", sa.Float(), nullable=False, server_default="10.0"),
        sa.Column("min_severity_to_stop", sa.String(), nullable=False, server_default="medium"),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_index("ix_security_audit_config_workspace_id", "security_audit_config", ["workspace_id"], unique=True)

    op.create_table(
        "security_audit_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.String(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("triggered_by", sa.String(), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("commit_sha", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=False, server_default="full"),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("findings_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("findings_net_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stop_reason", sa.String(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.create_index("ix_security_audit_run_workspace_id", "security_audit_run", ["workspace_id"])
    op.create_index("ix_security_audit_run_cycle_id", "security_audit_run", ["cycle_id"])

    op.create_table(
        "audit_finding",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("pattern_class", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("reference_fix", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("first_seen_run_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("ticket_id", sa.String(), nullable=True),
        sa.Column("hitl_review_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
    )
    op.create_index("ix_audit_finding_run_id", "audit_finding", ["run_id"])
    op.create_index("ix_audit_finding_workspace_id", "audit_finding", ["workspace_id"])
    op.create_index("ix_audit_finding_cycle_id", "audit_finding", ["cycle_id"])
    op.create_index("ix_audit_finding_fingerprint", "audit_finding", ["fingerprint"])
    op.create_index("ix_audit_finding_first_seen_run_id", "audit_finding", ["first_seen_run_id"])


def downgrade() -> None:
    op.drop_table("audit_finding")
    op.drop_table("security_audit_run")
    op.drop_table("security_audit_config")
