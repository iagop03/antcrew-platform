"""Model package — re-exports all SQLModel table classes for convenience.

``from app.models import Run`` works in addition to
``from app.models.run import Run``.
"""
from app.models.run import (
    # utilities
    _utcnow,
    # workspace domain
    Workspace,
    LLMProviderKey,
    WorkspaceContractSchema,
    CustomAgentDef,
    # auth domain
    ApiKey,
    WorkspaceMembership,
    User,
    UserSession,
    EmailVerification,
    WorkspaceInvite,
    WorkspaceJoinRequest,
    # review domain
    HitlReview,
    HitlReviewAssignee,
    HitlAuditEntry,
    # webhook domain
    WebhookDelivery,
    WebhookConfig,
    WebhookEvent,
    # eval domain
    EvalRun,
    EvalSchedule,
    CompareRun,
    # security domain
    SecurityAuditConfig,
    SecurityAuditRun,
    AuditFinding,
    # core run models
    PipelineDef,
    Run,
    Ticket,
    Event,
    RunTemplate,
    RunSchedule,
)

__all__ = [
    "_utcnow",
    "Workspace",
    "LLMProviderKey",
    "WorkspaceContractSchema",
    "CustomAgentDef",
    "ApiKey",
    "WorkspaceMembership",
    "User",
    "UserSession",
    "EmailVerification",
    "WorkspaceInvite",
    "WorkspaceJoinRequest",
    "HitlReview",
    "HitlReviewAssignee",
    "HitlAuditEntry",
    "WebhookDelivery",
    "WebhookConfig",
    "WebhookEvent",
    "EvalRun",
    "EvalSchedule",
    "CompareRun",
    "SecurityAuditConfig",
    "SecurityAuditRun",
    "AuditFinding",
    "PipelineDef",
    "Run",
    "Ticket",
    "Event",
    "RunTemplate",
    "RunSchedule",
]
