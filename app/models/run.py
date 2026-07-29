"""Database models for pipeline runs and their artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
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
    subscription_status: Optional[str] = Field(default=None)              # active | trialing | past_due | canceled | unpaid
    billing_provider: str = Field(default="mor")                          # mor | stripe
    mor_customer_id: Optional[str] = Field(default=None)                  # Lemon Squeezy customer ID
    mor_subscription_id: Optional[str] = Field(default=None)              # Lemon Squeezy subscription ID
    llm_key_mode: str = Field(default="managed")  # managed | byok | proxy
    byok_managed_fallback: bool = Field(default=False)  # fall back to platform key when no BYOK key for a model
    proxy_url: Optional[str] = Field(default=None)          # antcrew-proxy base URL (e.g. https://proxy.example.com)
    proxy_token_enc: Optional[str] = Field(default=None)    # Fernet-encrypted UUID token sent to the proxy
    is_trial: bool = Field(default=True)  # workspace is on the free-trial credit; costs at TRIAL_MULTIPLIER
    owner_user_id: Optional[int] = Field(default=None, index=True)  # user.id of the registering user
    created_at: datetime = Field(default_factory=_utcnow)


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

    __table_args__ = (
        UniqueConstraint("label", "workspace_id", name="uq_api_key_label_workspace"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    label: str = Field(index=True)
    key_hash: str  # bcrypt hash (or legacy sha256 until next login)
    key_prefix: Optional[str] = Field(default=None, index=True)  # sha256(raw)[:16] for O(1) lookup
    workspace_id: Optional[int] = Field(default=None)
    role: str = Field(default="write")  # admin | write | read | reviewer
    email: Optional[str] = Field(default=None)  # for HITL assignment notifications
    slack_user_id: Optional[str] = Field(default=None)      # Slack member ID (U…) for DM notifications
    telegram_chat_id: Optional[str] = Field(default=None)   # Telegram chat ID for bot notifications
    user_id: Optional[int] = Field(default=None, index=True)  # FK → user.id (set on register)
    created_at: datetime = Field(default_factory=_utcnow)
    revoked_at: Optional[datetime] = Field(default=None)


class HitlReview(SQLModel, table=True):
    """A pending or resolved Human-in-the-Loop review request."""

    __tablename__ = "hitl_review"

    id: Optional[int] = Field(default=None, primary_key=True)
    review_id: str = Field(index=True, unique=True)  # UUID from PlatformChannel
    client_token: Optional[str] = Field(default=None, index=True, unique=True)      # legacy plaintext — NULL for new reviews
    client_token_hash: Optional[str] = Field(default=None, index=True, unique=True)  # sha256(client_token)
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
    regression_id: Optional[str] = Field(default=None, index=True)  # batch ID when created via POST /evals/regression
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = Field(default=None)


class CompareRun(SQLModel, table=True):
    """Side-by-side comparison of the same request run against two LLM backends."""

    __tablename__ = "compare_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    compare_id: str = Field(index=True, unique=True)
    run_id_a: str = Field(index=True)
    run_id_b: str = Field(index=True)
    model_a: str
    model_b: str
    team: str
    request: str
    status: str = Field(default="running")  # running | done | error
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


class WorkspaceContractSchema(SQLModel, table=True):
    """Per-workspace JSON Schema for the custom_fields extension point of an artifact contract.

    Stores a JSON Schema that describes what keys are expected in the PRD.custom_fields
    (or any other extendable contract) for a specific workspace.  Purely informational
    in Phase 1 — operators ignore custom_fields; the schema is used for documentation
    and future prompt-injection.

    contract_name must match an entry in EXTENDABLE_CONTRACTS (e.g. "PRD").
    json_schema is any valid JSON Schema object.
    """

    __tablename__ = "workspace_contract_schema"
    __table_args__ = (
        UniqueConstraint("workspace_id", "contract_name", name="uq_contract_schema_ws_contract"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    contract_name: str  # e.g. "PRD"
    json_schema: dict = Field(default_factory=dict, sa_column=Column(JSON))
    description: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class LLMProviderKey(SQLModel, table=True):
    """Per-workspace, per-provider LLM API key for BYOK mode.

    key_enc is Fernet-encrypted with BYOK_ENCRYPTION_KEY, or plaintext in dev mode.
    Unique per (workspace_id, provider) — upsert by deleting and re-inserting.
    """

    __tablename__ = "llm_provider_key"
    __table_args__ = (UniqueConstraint("workspace_id", "provider", name="uq_llm_key_ws_provider"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    provider: str  # anthropic | openai | groq | gemini | ollama
    key_enc: str   # Fernet-encrypted or plaintext (dev mode without BYOK_ENCRYPTION_KEY); empty for keyless providers
    base_url: Optional[str] = Field(default=None)  # required for ollama / custom OpenAI-compat endpoints
    created_at: datetime = Field(default_factory=_utcnow)


class User(SQLModel, table=True):
    """Platform user with email+password credentials."""

    __tablename__ = "user"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    display_name: Optional[str] = Field(default=None)
    email_verified_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class UserSession(SQLModel, table=True):
    """Browser session backed by a UUID4 token stored in an HttpOnly cookie."""

    __tablename__ = "user_session"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: Optional[str] = Field(default=None, unique=True, index=True)  # legacy plaintext — NULL for new sessions
    token_hash: Optional[str] = Field(default=None, unique=True, index=True)  # sha256(raw_token) — primary lookup
    user_id: Optional[int] = Field(default=None, index=True)
    api_key_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    revoked: bool = Field(default=False)


# ── Security Audit ────────────────────────────────────────────────────────────

class SecurityAuditConfig(SQLModel, table=True):
    """Per-workspace configuration for the SecurityAuditor convergence loop.

    One row per workspace. Three independent trigger modes:
      trigger_manual   — POST /security/runs/trigger
      trigger_on_push  — GitHub push webhook
      schedule_cron    — cron expression (requires croniter)

    The audit reads files from github_repo via the GitHub API using
    GITHUB_TOKEN env var (or the token supplied per-request for manual runs).
    """

    __tablename__ = "security_audit_config"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(unique=True, index=True)
    enabled: bool = Field(default=True)
    # Trigger modes
    trigger_on_push: bool = Field(default=False)
    schedule_cron: Optional[str] = Field(default=None)   # e.g. "0 3 * * 1"
    schedule_next_run_at: Optional[datetime] = Field(default=None)
    # GitHub target
    github_repo: Optional[str] = Field(default=None)    # "owner/repo"
    github_branch: str = Field(default="main")
    # Convergence budget
    max_iterations: int = Field(default=3)
    max_cost_usd: float = Field(default=10.0)
    min_severity_to_stop: str = Field(default="medium")  # stop if no net-new findings >= this
    # Timestamps
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SecurityAuditRun(SQLModel, table=True):
    """One execution of the SecurityAuditor for a workspace.

    Multiple runs share a cycle_id when they are part of the same convergence
    loop (iteration 1, 2, 3 …). A new manual trigger starts a new cycle.
    """

    __tablename__ = "security_audit_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    cycle_id: str = Field(index=True)          # groups iterations of one hardening cycle
    iteration: int = Field(default=1)
    triggered_by: str = Field(default="manual")  # manual | schedule | push | convergence
    status: str = Field(default="pending")        # pending | running | completed | failed | stopped
    commit_sha: Optional[str] = Field(default=None)
    scope: str = Field(default="full")            # full | diff
    cost_usd: Optional[float] = Field(default=None)
    findings_total: int = Field(default=0)
    findings_net_new: int = Field(default=0)      # not seen in previous iterations of this cycle
    stop_reason: Optional[str] = Field(default=None)  # converged | max_iterations | max_cost | error
    error_detail: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)


class CustomAgentDef(SQLModel, table=True):
    """User-defined agent backed by TemplateAgent — scoped to workspace.

    agent_type is stable (never reused after delete) and matches the ``type``
    field stored on pipeline nodes — e.g. "custom_3".  The system_prompt is
    passed directly to TemplateAgent at runtime via node.agent_cfg.
    """

    __tablename__ = "custom_agent_def"
    __table_args__ = (
        UniqueConstraint("workspace_id", "agent_type", name="uq_custom_agent_ws_type"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)
    agent_type: str = Field(index=True)   # e.g. "custom_3"
    label: str
    color: str = Field(default="#7c3aed")
    system_prompt: str
    role_description: Optional[str] = Field(default=None)
    phase: str = Field(default="build")
    glyph: str = Field(default="✦")
    created_at: datetime = Field(default_factory=_utcnow)


class EmailVerification(SQLModel, table=True):
    """6-digit email verification code; invalidated on resend or successful use."""

    __tablename__ = "email_verification"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    code: Optional[str] = Field(default=None)      # legacy plaintext — NULL for new codes
    code_hash: Optional[str] = Field(default=None, index=True)  # HMAC-SHA256(SECRET_KEY, code)
    expires_at: datetime
    used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)


class WorkspaceInvite(SQLModel, table=True):
    """Invite link that grants a specific email access to a workspace."""

    __tablename__ = "workspace_invite"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True)   # secrets.token_urlsafe(32)
    workspace_id: int = Field(index=True)
    invitee_email: str = Field(index=True)
    inviter_email: str
    role: str = Field(default="write")             # admin | write | read | reviewer
    status: str = Field(default="pending")          # pending | accepted | expired | revoked
    expires_at: datetime
    created_at: datetime = Field(default_factory=_utcnow)
    accepted_at: Optional[datetime] = Field(default=None)


class WorkspaceJoinRequest(SQLModel, table=True):
    """Request from an authenticated user to join a workspace they found by slug."""

    __tablename__ = "workspace_join_request"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True)    # for approve/reject one-click URL
    workspace_id: int = Field(index=True)
    requester_email: str = Field(index=True)
    requested_role: str = Field(default="write")
    status: str = Field(default="pending")          # pending | approved | rejected
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = Field(default=None)


class AuditFinding(SQLModel, table=True):
    """Individual security finding produced by SecurityAuditor.

    fingerprint deduplicates findings across iterations of the same cycle:
      sha256(pattern_class + "|" + file_path + "|" + str(line_number or ""))[:16]

    first_seen_run_id tracks which iteration of the cycle first surfaced this
    finding — used to compute SecurityAuditRun.findings_net_new.
    """

    __tablename__ = "audit_finding"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)            # FK → security_audit_run.id
    workspace_id: int = Field(index=True)
    cycle_id: str = Field(index=True)          # FK → security_audit_run.cycle_id
    severity: str                              # critical | high | medium | low | info
    pattern_class: str                         # ssrf | idor | path_traversal | …
    file_path: str
    line_number: Optional[int] = Field(default=None)
    title: str
    evidence: str
    reference_fix: Optional[str] = Field(default=None)
    fingerprint: str = Field(index=True)       # sha256[:16] for dedup within cycle
    first_seen_run_id: int = Field(index=True)
    # Lifecycle
    status: str = Field(default="open")        # open | in_review | fixed | wont_fix | false_positive
    ticket_id: Optional[str] = Field(default=None)
    hitl_review_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
