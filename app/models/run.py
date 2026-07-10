"""Database models for pipeline runs and their artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel, JSON, Column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Workspace(SQLModel, table=True):
    """Isolated project scope for multi-team deployments."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    max_cost_usd: Optional[float] = Field(default=None)
    total_cost_usd: float = Field(default=0.0)  # cached total; updated after each run via SQL SUM
    default_repo_url: Optional[str] = Field(default=None)
    slack_webhook_url: Optional[str] = Field(default=None)   # per-workspace HITL incoming webhook URL
    slack_channel_id: Optional[str] = Field(default=None)    # Slack channel ID for interactive HITL
    slack_bot_token_enc: Optional[str] = Field(default=None) # encrypted xoxb-… (Fernet, key=SLACK_TOKEN_ENCRYPTION_KEY)
    slack_app_token_enc: Optional[str] = Field(default=None) # encrypted xapp-… for Socket Mode
    hitl_default: bool = Field(default=False)
    hitl_timeout_s: Optional[float] = Field(default=None)  # per-workspace HITL timeout (overrides env HITL_TIMEOUT_S)
    stripe_customer_id: Optional[str] = Field(default=None, index=True)  # cus_...
    stripe_subscription_id: Optional[str] = Field(default=None)           # sub_...
    stripe_subscription_status: Optional[str] = Field(default=None)       # active | trialing | past_due | canceled | unpaid
    created_at: datetime = Field(default_factory=_utcnow)


class Run(SQLModel, table=True):
    """One pipeline execution."""

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, unique=True)
    thread_id: str = Field(default="default")
    team: str
    request: str
    status: str = Field(default="running")  # running | success | error | cancelled
    cost_usd: float = Field(default=0.0)
    duration_s: Optional[float] = Field(default=None)
    created_by: Optional[str] = Field(default=None)  # API key label
    workspace_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = Field(default=None)
    state: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class Ticket(SQLModel, table=True):
    """A PM ticket produced by a pipeline run — stable by deterministic ID."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: str = Field(index=True)
    run_id: str = Field(index=True)
    title: str
    description: str = Field(default="")
    acceptance_criteria: str = Field(default="")
    dependencies: str = Field(default="")  # JSON-encoded list of ticket_ids
    priority: str = Field(default="medium")
    status: str = Field(default="open")
    prd_title: str = Field(default="")
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Event(SQLModel, table=True):
    """Raw event emitted by the antcrew event bus."""

    __table_args__ = (Index("ix_event_run_id_ts", "run_id", "timestamp"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[str] = Field(default=None, index=True)
    thread_id: Optional[str] = Field(default=None)
    event_type: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    timestamp: float
    recorded_at: datetime = Field(default_factory=_utcnow)


class ApiKey(SQLModel, table=True):
    """Platform API key — used in multi-key mode when PLATFORM_API_KEY env is not set."""

    __tablename__ = "api_key"

    id: Optional[int] = Field(default=None, primary_key=True)
    label: str = Field(index=True, unique=True)
    key_hash: str  # bcrypt hash (or legacy sha256 until next login)
    workspace_id: Optional[int] = Field(default=None)
    role: str = Field(default="write")  # admin | write | read | reviewer
    email: Optional[str] = Field(default=None)  # for HITL assignment notifications
    created_at: datetime = Field(default_factory=_utcnow)
    revoked_at: Optional[datetime] = Field(default=None)


class HitlReview(SQLModel, table=True):
    """A pending or resolved Human-in-the-Loop review request."""

    __tablename__ = "hitl_review"

    id: Optional[int] = Field(default=None, primary_key=True)
    review_id: str = Field(index=True, unique=True)  # UUID from PlatformChannel
    run_id: str = Field(index=True)
    agent_name: str
    artifact_json: str = Field(default="null")   # JSON-serialized artifact
    options_json: str = Field(default='["approve","reject"]')  # JSON list
    status: str = Field(default="pending")  # pending | approved | rejected | edited | timeout
    decision: Optional[str] = Field(default=None)
    edited_json: Optional[str] = Field(default=None)
    feedback: Optional[str] = Field(default=None)
    assigned_to: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = Field(default=None)


class RunTemplate(SQLModel, table=True):
    """A reusable run configuration saved by the user."""

    __tablename__ = "run_template"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    team: str
    request: str
    max_cost_usd: Optional[float] = Field(default=None)
    hitl: bool = Field(default=False)
    repo_url: Optional[str] = Field(default=None)
    workspace_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class EvalRun(SQLModel, table=True):
    """A platform-dispatched eval run — result of POST /evals/."""

    __tablename__ = "eval_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    eval_id: str = Field(index=True, unique=True)   # UUID
    run_id: Optional[str] = Field(default=None, index=True)  # FK → run.run_id (stub Run)
    team: str
    request: str
    name: str = Field(default="")
    model: str = Field(default="")                  # informational only
    judge_model: str = Field(default="")            # LLM used as eval judge
    status: str = Field(default="running")          # running | done | error
    report: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    error: Optional[str] = Field(default=None)
    cost_usd: float = Field(default=0.0)
    elapsed_ms: float = Field(default=0.0)
    workspace_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = Field(default=None)


class EvalSchedule(SQLModel, table=True):
    """A recurring eval schedule that dispatches EvalRun entries on a cron-like basis."""

    __tablename__ = "eval_schedule"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    team: str
    request: str
    interval_hours: float = Field(default=24.0)
    next_run_at: datetime = Field(default_factory=_utcnow)
    enabled: bool = Field(default=True)
    model: str = Field(default="")
    judge_model: str = Field(default="")
    expect_min_tickets: int = Field(default=0)
    expect_min_code_files: int = Field(default=0)
    expect_review_verdict: str = Field(default="")
    workspace_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    last_eval_id: Optional[str] = Field(default=None)


class WebhookDelivery(SQLModel, table=True):
    """A webhook delivery attempt with retry tracking."""

    __tablename__ = "webhook_delivery"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    url: str
    payload_json: str
    status: str = Field(default="pending")  # pending | delivered | retrying | failed
    attempts: int = Field(default=0)
    next_retry_at: datetime = Field(default_factory=_utcnow)
    last_error: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class WebhookConfig(SQLModel, table=True):
    """Per-workspace webhook registration — fires on subscribed event types."""

    __tablename__ = "webhook_config"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    url: str
    label: Optional[str] = Field(default=None)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)


class WebhookEvent(SQLModel, table=True):
    """Event-type subscription for a WebhookConfig (one row per event type per webhook)."""

    __tablename__ = "webhook_event"

    id: Optional[int] = Field(default=None, primary_key=True)
    webhook_id: int = Field(index=True)   # FK → webhook_config.id
    event_type: str = Field(index=True)   # e.g. "pipeline.end", "hitl.review_required", "*"


class WorkspaceMembership(SQLModel, table=True):
    """Many-to-many between ApiKey and Workspace — allows one key to access multiple workspaces.

    When a key has membership rows, the set of accessible workspace IDs is the union of
    all memberships. The key's own workspace_id remains its primary workspace (used for
    creating new resources). Keys with no memberships fall back to workspace_id scoping.
    """

    __tablename__ = "workspace_membership"

    id: Optional[int] = Field(default=None, primary_key=True)
    api_key_id: int = Field(index=True)
    workspace_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=_utcnow)


class HitlReviewAssignee(SQLModel, table=True):
    """One row per reviewer assigned to a HitlReview (many-to-many).

    Assignees are identified by their API key label.  Any assignee can resolve
    the review (first-to-respond model).  Use the 'mine' query param on
    GET /reviews/ to filter to reviews where the calling key is an assignee.
    """

    __tablename__ = "hitl_review_assignee"

    id: Optional[int] = Field(default=None, primary_key=True)
    review_id: str = Field(index=True)    # FK → hitl_review.review_id
    assignee_label: str = Field(index=True)  # FK → api_key.label
    created_at: datetime = Field(default_factory=_utcnow)


class HitlAuditEntry(SQLModel, table=True):
    """Immutable audit log for HITL review lifecycle events.

    Tracks who did what and when: creation, assignment, approval, rejection, timeout.
    Never updated — only appended to.
    """

    __tablename__ = "hitl_audit_entry"

    id: Optional[int] = Field(default=None, primary_key=True)
    review_id: str = Field(index=True)     # FK → hitl_review.review_id
    actor_label: Optional[str] = Field(default=None)  # API key label who triggered this event
    action: str  # created | assigned | approved | rejected | timed_out
    note: Optional[str] = Field(default=None)  # free-text from verdict note or error
    created_at: datetime = Field(default_factory=_utcnow)
