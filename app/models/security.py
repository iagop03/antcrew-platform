"""Security audit configuration, run, and finding models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models._utils import _utcnow


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
