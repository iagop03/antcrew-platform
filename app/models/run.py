"""Database models for pipeline runs and their artifacts.

Core run models (Run, Ticket, Event, RunTemplate, RunSchedule, PipelineDef) are defined
here. All other models are imported from their domain modules and re-exported so that
existing ``from app.models.run import XYZ`` imports continue to work unchanged.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel, JSON, Column

from app.models._utils import _utcnow

# ── Re-exports from domain modules ────────────────────────────────────────────
from app.models.workspace import (
    Workspace,
    LLMProviderKey,
    WorkspaceContractSchema,
    CustomAgentDef,
)
from app.models.auth import (
    ApiKey,
    WorkspaceMembership,
    User,
    UserSession,
    EmailVerification,
    WorkspaceInvite,
    WorkspaceJoinRequest,
)
from app.models.review import HitlReview, HitlReviewAssignee, HitlAuditEntry
from app.models.webhook import WebhookDelivery, WebhookConfig, WebhookEvent
from app.models.eval import EvalRun, EvalSchedule, CompareRun
from app.models.security import SecurityAuditConfig, SecurityAuditRun, AuditFinding
from app.models.feedback import UserFeedback

# ── Core run models ───────────────────────────────────────────────────────────


class PipelineDef(SQLModel, table=True):
    """User-defined visual pipeline stored as a JSON graph."""

    __tablename__ = "pipeline_def"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: Optional[int] = Field(default=None, foreign_key="workspace.id", index=True)
    name: str
    description: Optional[str] = Field(default=None)
    is_template: bool = Field(default=False)
    definition: str  # JSON: {nodes: [...], edges: [...]}
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
    client_label: Optional[str] = Field(default=None, index=True)  # cost-center / client tag for spend breakdown
    model: Optional[str] = Field(default=None, index=True)         # LLM model used (e.g. "claude-sonnet-4-5"); populated when known
    model_overrides: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # per-agent overrides: {"BackendDevAgent": "groq:llama-3.3-70b"}
    llm_key_mode: Optional[str] = Field(default=None)  # snapshotted from workspace at run creation; use for attribution queries


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
    status: str = Field(default="open")   # open | in_progress | done | blocked
    prd_title: str = Field(default="")
    # Manual-action fields
    ticket_type: str = Field(default="task")          # task | manual_action | bug
    blocking: bool = Field(default=False)             # when True, blocks the run until resolved
    assignee: Optional[str] = Field(default=None)    # email of the human responsible
    # Workspace-scoped display ID (e.g. "PROJ-00001") — set on creation, null for legacy tickets
    workspace_id: Optional[int] = Field(default=None, index=True)
    display_id: Optional[str] = Field(default=None, index=True)
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


class RunPreset(SQLModel, table=True):
    """Named model-override configuration for a team, scoped to a workspace."""

    __tablename__ = "run_preset"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    name: str
    team: str
    model_overrides: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_by: Optional[str] = None
    created_at: Optional[datetime] = Field(default_factory=_utcnow)


class RunSchedule(SQLModel, table=True):
    """Recurring engine run — fires on a cron expression, scoped to a workspace."""

    __tablename__ = "run_schedule"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    name: str
    goal: str
    model: str = Field(default="claude")
    conditions: Optional[str] = Field(default=None)   # JSON list, NULL = full default set
    full: bool = Field(default=True)
    max_cost_usd: Optional[float] = Field(default=None)
    cron_expr: str                                     # e.g. "0 8 * * 1" (Mon 08:00 UTC)
    enabled: bool = Field(default=True)
    next_run_at: datetime = Field(default_factory=_utcnow)
    last_run_id: Optional[str] = Field(default=None)
    created_by: Optional[str] = Field(default=None)   # API key label
    created_at: datetime = Field(default_factory=_utcnow)


__all__ = [
    # utilities
    "_utcnow",
    # workspace domain
    "Workspace",
    "LLMProviderKey",
    "WorkspaceContractSchema",
    "CustomAgentDef",
    # auth domain
    "ApiKey",
    "WorkspaceMembership",
    "User",
    "UserSession",
    "EmailVerification",
    "WorkspaceInvite",
    "WorkspaceJoinRequest",
    # review domain
    "HitlReview",
    "HitlReviewAssignee",
    "HitlAuditEntry",
    # webhook domain
    "WebhookDelivery",
    "WebhookConfig",
    "WebhookEvent",
    # eval domain
    "EvalRun",
    "EvalSchedule",
    "CompareRun",
    # security domain
    "SecurityAuditConfig",
    "SecurityAuditRun",
    "AuditFinding",
    # core run models
    "PipelineDef",
    "Run",
    "Ticket",
    "Event",
    "RunTemplate",
    "RunPreset",
    "RunSchedule",
]
