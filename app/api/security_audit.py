"""Security audit — config, trigger, runs, findings, FindingsTriager, convergence loop.

Three independent trigger modes per workspace:
  manual    → POST /security/runs/trigger
  on_push   → POST /security/webhook/github  (GitHub push event)
  schedule  → background task reads schedule_cron, fires when due

Convergence loop (runs inside the background task after each audit completes):
  - If findings_net_new (severity >= min_severity_to_stop) > 0 AND
    iteration < max_iterations AND cumulative_cost < max_cost_usd
    → start next iteration (triggered_by="convergence", scope="diff" if commit_sha known)
  - Otherwise → mark cycle complete

FindingsTriager (called after each run):
  critical / high  → create Ticket + HitlReview (human must approve fix)
  medium / low     → create Ticket only (BugFixer can auto-fix; human merges PR)
  info             → store only, no ticket

Role requirements:
  read-only endpoints  → any authenticated key
  write endpoints      → admin role
  webhook              → HMAC-SHA256 signature with GITHUB_WEBHOOK_SECRET env var
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import WorkspaceContext, get_workspace_context, require_api_key, require_role
from app.core.database import get_session
from app.models.run import AuditFinding, SecurityAuditConfig, SecurityAuditRun

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/security",
    tags=["security"],
    dependencies=[Depends(require_api_key)],
)

# Webhook router has no require_api_key — authenticated by HMAC signature instead
webhook_router = APIRouter(prefix="/security", tags=["security"])

_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_SEVERITY_TO_STOP = {"critical", "high", "medium"}  # default; overridden by config


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fingerprint(pattern_class: str, file_path: str, line_number: Optional[int]) -> str:
    raw = f"{pattern_class}|{file_path}|{line_number or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Config ────────────────────────────────────────────────────────────────────

class AuditConfigRequest(BaseModel):
    enabled: bool = True
    trigger_on_push: bool = False
    schedule_cron: Optional[str] = None
    github_repo: Optional[str] = None
    github_branch: str = "main"
    max_iterations: int = 3
    max_cost_usd: float = 10.0
    min_severity_to_stop: str = "medium"


@router.get("/config")
async def get_config(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Return the security audit config for the current workspace."""
    cfg = (await session.exec(
        select(SecurityAuditConfig).where(SecurityAuditConfig.workspace_id == ctx.workspace_id)
    )).first()
    if not cfg:
        raise HTTPException(404, "No audit config found — POST /security/config to create one")
    return _cfg_to_dict(cfg)


@router.post("/config", dependencies=[Depends(require_role("admin"))])
async def upsert_config(
    body: AuditConfigRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Create or update the security audit config for this workspace."""
    cfg = (await session.exec(
        select(SecurityAuditConfig).where(SecurityAuditConfig.workspace_id == ctx.workspace_id)
    )).first()

    next_run_at: Optional[datetime] = None
    if body.schedule_cron:
        next_run_at = _next_cron(body.schedule_cron)

    if cfg is None:
        cfg = SecurityAuditConfig(
            workspace_id=ctx.workspace_id,
            enabled=body.enabled,
            trigger_on_push=body.trigger_on_push,
            schedule_cron=body.schedule_cron,
            schedule_next_run_at=next_run_at,
            github_repo=body.github_repo,
            github_branch=body.github_branch,
            max_iterations=body.max_iterations,
            max_cost_usd=body.max_cost_usd,
            min_severity_to_stop=body.min_severity_to_stop,
        )
    else:
        cfg.enabled = body.enabled
        cfg.trigger_on_push = body.trigger_on_push
        cfg.schedule_cron = body.schedule_cron
        cfg.schedule_next_run_at = next_run_at
        cfg.github_repo = body.github_repo
        cfg.github_branch = body.github_branch
        cfg.max_iterations = body.max_iterations
        cfg.max_cost_usd = body.max_cost_usd
        cfg.min_severity_to_stop = body.min_severity_to_stop
        cfg.updated_at = _utcnow()

    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return _cfg_to_dict(cfg)


# ── Runs ──────────────────────────────────────────────────────────────────────

class TriggerRequest(BaseModel):
    github_token: Optional[str] = None   # overrides GITHUB_TOKEN env var
    commit_sha: Optional[str] = None     # HEAD SHA for traceability
    scope: str = "full"                  # full | diff


@router.post("/runs/trigger", status_code=202, dependencies=[Depends(require_role("admin"))])
async def trigger_audit(
    body: TriggerRequest,
    background_tasks: BackgroundTasks,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger a security audit for the current workspace."""
    cfg = await _require_config(ctx.workspace_id, session)
    if not cfg.github_repo:
        raise HTTPException(422, "github_repo is not configured — update /security/config first")

    token = body.github_token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise HTTPException(422, "github_token required (or set GITHUB_TOKEN env var)")

    run = await _create_run(
        workspace_id=ctx.workspace_id,
        cycle_id=str(uuid.uuid4()),
        iteration=1,
        triggered_by="manual",
        commit_sha=body.commit_sha,
        scope=body.scope,
        session=session,
    )
    background_tasks.add_task(
        _run_audit_task,
        run_id=run.id,
        cfg_id=cfg.id,
        github_token=token,
    )
    return {"run_id": run.id, "cycle_id": run.cycle_id, "status": "pending"}


@router.get("/runs")
async def list_runs(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
    limit: int = 20,
):
    """List recent security audit runs for this workspace (newest first)."""
    runs = (await session.exec(
        select(SecurityAuditRun)
        .where(SecurityAuditRun.workspace_id == ctx.workspace_id)
        .order_by(SecurityAuditRun.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    )).all()
    return [_run_to_dict(r) for r in runs]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Get a single audit run with its summary."""
    run = await _get_run_or_404(run_id, ctx.workspace_id, session)
    return _run_to_dict(run)


@router.get("/runs/{run_id}/findings")
async def get_run_findings(
    run_id: int,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
    severity: Optional[str] = None,
    status: Optional[str] = None,
):
    """List findings for a specific audit run, optionally filtered by severity/status."""
    await _get_run_or_404(run_id, ctx.workspace_id, session)
    q = select(AuditFinding).where(AuditFinding.run_id == run_id)
    if severity:
        q = q.where(AuditFinding.severity == severity)
    if status:
        q = q.where(AuditFinding.status == status)
    findings = (await session.exec(q)).all()
    return [_finding_to_dict(f) for f in findings]


# ── Findings management ───────────────────────────────────────────────────────

class FindingPatchRequest(BaseModel):
    status: str   # fixed | wont_fix | false_positive | open | in_review


@router.patch("/findings/{finding_id}", dependencies=[Depends(require_role("admin"))])
async def patch_finding(
    finding_id: int,
    body: FindingPatchRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Update the lifecycle status of a finding."""
    _VALID = {"open", "in_review", "fixed", "wont_fix", "false_positive"}
    if body.status not in _VALID:
        raise HTTPException(422, f"status must be one of {sorted(_VALID)}")

    finding = (await session.exec(
        select(AuditFinding).where(
            AuditFinding.id == finding_id,
            AuditFinding.workspace_id == ctx.workspace_id,
        )
    )).first()
    if not finding:
        raise HTTPException(404, f"Finding {finding_id} not found")

    finding.status = body.status
    session.add(finding)
    await session.commit()
    return _finding_to_dict(finding)


# ── GitHub push webhook ───────────────────────────────────────────────────────

@webhook_router.post("/webhook/github", include_in_schema=False)
async def github_push_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Receive GitHub push webhooks and trigger audits for configured workspaces.

    Validates HMAC-SHA256 signature with GITHUB_WEBHOOK_SECRET env var.
    Only fires if trigger_on_push=True and github_repo matches.
    """
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    payload_bytes = await request.body()

    if secret:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_github_sig(payload_bytes, sig_header, secret):
            raise HTTPException(401, "Invalid webhook signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        return {"ignored": True, "reason": f"event={event!r} is not 'push'"}

    try:
        data = json.loads(payload_bytes)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    ref = data.get("ref", "")
    full_name = data.get("repository", {}).get("full_name", "")
    head_sha = data.get("after", "")

    # Find all workspaces configured for this repo + push trigger
    from app.core.database import AsyncSessionFactory
    async with AsyncSessionFactory() as session:
        configs = (await session.exec(
            select(SecurityAuditConfig).where(
                SecurityAuditConfig.trigger_on_push == True,  # noqa: E712
                SecurityAuditConfig.enabled == True,          # noqa: E712
                SecurityAuditConfig.github_repo == full_name,
            )
        )).all()

    token = os.environ.get("GITHUB_TOKEN", "")
    fired = []
    for cfg in configs:
        branch_ref = f"refs/heads/{cfg.github_branch}"
        if ref != branch_ref:
            continue
        if not token:
            log.warning("security_audit: push webhook for %s but GITHUB_TOKEN not set", full_name)
            continue

        async with AsyncSessionFactory() as session:
            run = await _create_run(
                workspace_id=cfg.workspace_id,
                cycle_id=str(uuid.uuid4()),
                iteration=1,
                triggered_by="push",
                commit_sha=head_sha or None,
                scope="full",
                session=session,
            )
        background_tasks.add_task(_run_audit_task, run_id=run.id, cfg_id=cfg.id, github_token=token)
        fired.append({"workspace_id": cfg.workspace_id, "run_id": run.id})

    return {"fired": fired}


# ── Background audit task ─────────────────────────────────────────────────────

async def _run_audit_task(run_id: int, cfg_id: int, github_token: str) -> None:
    """Execute one SecurityAuditor iteration, triage findings, check convergence."""
    from app.core.database import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        run = (await session.exec(
            select(SecurityAuditRun).where(SecurityAuditRun.id == run_id)
        )).first()
        cfg = (await session.exec(
            select(SecurityAuditConfig).where(SecurityAuditConfig.id == cfg_id)
        )).first()
        if not run or not cfg:
            log.error("security_audit: run %s or cfg %s not found", run_id, cfg_id)
            return

        run.status = "running"
        run.started_at = _utcnow()
        session.add(run)
        await session.commit()

    try:
        # Fetch repo files via GitHub API
        files = await _fetch_github_files(
            repo=cfg.github_repo,
            branch=cfg.github_branch,
            token=github_token,
            since_sha=run.commit_sha if run.scope == "diff" else None,
        )

        if not files:
            await _fail_run(run_id, "No relevant files fetched from GitHub")
            return

        # Resolve workspace LLM config (BYOK / proxy / managed)
        async with AsyncSessionFactory() as _sess:
            from app.models.run import Workspace as _Workspace
            from sqlmodel import select as _select
            _ws = (await _sess.exec(_select(_Workspace).where(_Workspace.id == run.workspace_id))).first()
            from app.services.runner_base import resolve_workspace_llm_config
            _llm_api_key, _llm_base_url = await resolve_workspace_llm_config(
                _sess, _ws, "claude"
            ) if _ws else (None, None)

        # Build ArtifactStore and run SecurityAuditor in thread pool
        result_content = await asyncio.get_event_loop().run_in_executor(
            None,
            _run_auditor_sync,
            files,
            cfg.github_repo or "unknown repo",
            _llm_api_key,
            _llm_base_url,
        )

        findings_raw: list[dict] = result_content.get("findings", [])
        cost_usd: float = result_content.get("cost_usd", 0.0)

    except Exception as exc:
        log.exception("security_audit: audit task failed for run %s", run_id)
        await _fail_run(run_id, str(exc))
        return

    # Store findings and compute net-new vs previous iterations in this cycle
    async with AsyncSessionFactory() as session:
        run = (await session.exec(
            select(SecurityAuditRun).where(SecurityAuditRun.id == run_id)
        )).first()

        # Fingerprints already seen in previous iterations of this cycle
        prev_fingerprints: set[str] = set()
        if run.iteration > 1:
            prev_findings = (await session.exec(
                select(AuditFinding.fingerprint).where(
                    AuditFinding.cycle_id == run.cycle_id,
                    AuditFinding.run_id != run_id,
                )
            )).all()
            prev_fingerprints = set(prev_findings)

        new_findings: list[AuditFinding] = []
        net_new = 0
        for raw in findings_raw:
            fp = _fingerprint(
                raw.get("pattern_class", "unknown"),
                raw.get("file_path", ""),
                raw.get("line_number"),
            )
            is_new = fp not in prev_fingerprints
            finding = AuditFinding(
                run_id=run_id,
                workspace_id=run.workspace_id,
                cycle_id=run.cycle_id,
                severity=raw.get("severity", "info"),
                pattern_class=raw.get("pattern_class", "unknown"),
                file_path=raw.get("file_path", ""),
                line_number=raw.get("line_number"),
                title=raw.get("title", "")[:200],
                evidence=raw.get("evidence", ""),
                reference_fix=raw.get("reference_fix"),
                fingerprint=fp,
                first_seen_run_id=run_id if is_new else run_id,  # always track this run
                status="open",
            )
            session.add(finding)
            new_findings.append(finding)
            if is_new and _sev_order(raw.get("severity", "info")) >= _sev_order(cfg.min_severity_to_stop):
                net_new += 1

        run.status = "completed"
        run.completed_at = _utcnow()
        run.cost_usd = cost_usd
        run.findings_total = len(new_findings)
        run.findings_net_new = net_new
        session.add(run)
        await session.commit()

        # Refresh findings to get IDs
        for f in new_findings:
            await session.refresh(f)

        # Triage findings → create tickets / HITL reviews
        await _triage_findings(new_findings, run, session)
        await session.commit()

        # Check convergence and schedule next iteration if needed
        await _maybe_next_iteration(run, cfg, github_token, session)


def _run_auditor_sync(
    files: list[dict],
    repo_description: str,
    api_key: "str | None" = None,
    base_url: "str | None" = None,
) -> dict:
    """Synchronous wrapper: build ArtifactStore + run SecurityAuditor. Runs in thread."""
    from antcrew_engine.engine import (
        Artifact, ArtifactId, ArtifactKind, MemoryStore,
    )
    from antcrew_engine.engine.goal import Goal, DesiredProjectState, Condition, ConditionId
    from antcrew_engine.capabilities.security_auditor import SecurityAuditor

    # Build store from fetched files
    store = MemoryStore()
    for f in files:
        store.write(Artifact(
            id=ArtifactId(f["path"].replace("/", "_").replace(".", "_")),
            kind=ArtifactKind.SOURCE,
            content=f["content"],
            metadata={"file_path": f["path"]},
        ))

    # Build LLM — use workspace BYOK/proxy config when available, fall back to env key
    llm = _build_llm(api_key=api_key, base_url=base_url)

    goal = Goal(
        description=f"Security audit of {repo_description}",
        desired_state=DesiredProjectState(
            conditions=frozenset([Condition(ConditionId("security_audit_done"), "Security audit completed")])
        ),
    )
    auditor = SecurityAuditor(llm=llm)
    result = auditor.execute(store, goal)

    # Extract report content
    for art in (*result.delta.created, *result.delta.modified):
        if art.id == ArtifactId("security_audit_report"):
            content = art.content if isinstance(art.content, dict) else {}
            content["cost_usd"] = result.cost_usd or 0.0
            return content

    return {"findings": [], "cost_usd": result.cost_usd or 0.0, "errors": result.errors}


def _build_llm(api_key: "str | None" = None, base_url: "str | None" = None):
    """Build LLM instance, honouring workspace BYOK/proxy config when provided."""
    from antcrew_engine.config import build_llm as _build
    kw: dict = {}
    if api_key:
        kw["api_key"] = api_key
    if base_url:
        kw["base_url"] = base_url
    if not kw:
        # Managed mode: require platform env key
        env_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not env_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for SecurityAuditor")
        kw["api_key"] = env_key
    return _build("claude-sonnet-4-6", **kw)


# ── FindingsTriager ───────────────────────────────────────────────────────────

async def _triage_findings(
    findings: list[AuditFinding],
    run: SecurityAuditRun,
    session: AsyncSession,
) -> None:
    """Route findings to HITL reviews and/or tickets based on severity."""
    from app.models.run import HitlReview

    for finding in findings:
        sev = finding.severity
        if sev in ("critical", "high"):
            # Ticket + HitlReview — human must see and approve fix before merge
            ticket = await _create_security_ticket(finding, run, session)
            review_id = f"sec-{finding.id}-{secrets.token_hex(4)}"
            review = HitlReview(
                review_id=review_id,
                run_id=str(run.id),
                agent_name="SecurityAuditor",
                artifact_json=json.dumps(_finding_to_dict(finding)),
                options_json=json.dumps(["approve_fix", "reject", "mark_false_positive"]),
                status="pending",
                created_at=_utcnow(),
            )
            session.add(review)
            finding.hitl_review_id = review_id
            if ticket:
                finding.ticket_id = ticket.ticket_id
            session.add(finding)

        elif sev in ("medium", "low"):
            # Ticket only — BugFixer can auto-fix; human reviews the PR before merging
            ticket = await _create_security_ticket(finding, run, session)
            if ticket:
                finding.ticket_id = ticket.ticket_id
            session.add(finding)
        # info: store only, no ticket


async def _create_security_ticket(
    finding: AuditFinding,
    run: SecurityAuditRun,
    session: AsyncSession,
) -> Optional[object]:
    """Create a Ticket for a security finding, returning None on failure."""
    try:
        from app.models.run import Ticket
        ticket_id = f"sec-{run.workspace_id}-{finding.id}"
        ticket = Ticket(
            ticket_id=ticket_id,
            run_id=str(run.id),
            title=f"[Security {finding.severity.upper()}] {finding.title}",
            description=(
                f"**Pattern:** {finding.pattern_class}\n"
                f"**File:** {finding.file_path}"
                + (f":{finding.line_number}" if finding.line_number else "") + "\n\n"
                f"**Evidence:**\n{finding.evidence}"
                + (f"\n\n**Reference fix:** {finding.reference_fix}" if finding.reference_fix else "")
            ),
            status="open",
            priority=finding.severity,
        )
        session.add(ticket)
        return ticket
    except Exception as exc:
        log.warning("security_audit: could not create ticket for finding %s: %s", finding.id, exc)
        return None


# ── Convergence loop ──────────────────────────────────────────────────────────

async def _maybe_next_iteration(
    run: SecurityAuditRun,
    cfg: SecurityAuditConfig,
    github_token: str,
    session: AsyncSession,
) -> None:
    """Check convergence conditions; if not met, create and schedule next iteration."""

    # Compute cumulative cost across all runs in this cycle
    all_cycle_runs = (await session.exec(
        select(SecurityAuditRun).where(SecurityAuditRun.cycle_id == run.cycle_id)
    )).all()
    cumulative_cost = sum(r.cost_usd or 0.0 for r in all_cycle_runs)

    # Convergence: two consecutive runs with 0 net-new findings >= min_severity
    consecutive_clean = (
        len(all_cycle_runs) >= 2
        and all_cycle_runs[-1].findings_net_new == 0
        and all_cycle_runs[-2].findings_net_new == 0
    )

    stop_reason: Optional[str] = None
    if consecutive_clean:
        stop_reason = "converged"
    elif run.iteration >= cfg.max_iterations:
        stop_reason = "max_iterations"
    elif cumulative_cost >= cfg.max_cost_usd:
        stop_reason = "max_cost"

    if stop_reason:
        log.info(
            "security_audit: cycle %s stopped after %s iteration(s): %s",
            run.cycle_id, run.iteration, stop_reason,
        )
        run.stop_reason = stop_reason
        session.add(run)
        await session.commit()
        return

    # Schedule next iteration
    next_run = await _create_run(
        workspace_id=run.workspace_id,
        cycle_id=run.cycle_id,
        iteration=run.iteration + 1,
        triggered_by="convergence",
        commit_sha=run.commit_sha,
        scope="diff" if run.commit_sha else "full",
        session=session,
    )
    # Fire as background task in a fresh task to avoid blocking this coroutine
    asyncio.create_task(
        _run_audit_task(run_id=next_run.id, cfg_id=cfg.id, github_token=github_token)
    )


# ── Schedule background loop ──────────────────────────────────────────────────

async def run_schedule_loop() -> None:
    """Asyncio background task: check SecurityAuditConfig rows with a schedule_cron.

    Runs every 60 seconds. When schedule_next_run_at <= now and config is enabled,
    triggers a new audit cycle and advances schedule_next_run_at.
    """
    from app.core.database import AsyncSessionFactory

    while True:
        await asyncio.sleep(60)
        try:
            async with AsyncSessionFactory() as session:
                now = _utcnow()
                due_configs = (await session.exec(
                    select(SecurityAuditConfig).where(
                        SecurityAuditConfig.enabled == True,             # noqa: E712
                        SecurityAuditConfig.schedule_cron.isnot(None),   # type: ignore[attr-defined]
                        SecurityAuditConfig.schedule_next_run_at <= now, # type: ignore[operator]
                    )
                )).all()

            token = os.environ.get("GITHUB_TOKEN", "")
            for cfg in due_configs:
                if not cfg.github_repo or not token:
                    continue
                async with AsyncSessionFactory() as session:
                    run = await _create_run(
                        workspace_id=cfg.workspace_id,
                        cycle_id=str(uuid.uuid4()),
                        iteration=1,
                        triggered_by="schedule",
                        commit_sha=None,
                        scope="full",
                        session=session,
                    )
                    cfg.schedule_next_run_at = _next_cron(cfg.schedule_cron)
                    cfg.updated_at = _utcnow()
                    session.add(cfg)
                    await session.commit()

                asyncio.create_task(
                    _run_audit_task(run_id=run.id, cfg_id=cfg.id, github_token=token)
                )
                log.info("security_audit: schedule triggered run %s for ws %s", run.id, cfg.workspace_id)
        except Exception:
            log.exception("security_audit: error in schedule loop")


# ── GitHub file fetching ──────────────────────────────────────────────────────

_AUDIT_EXTENSIONS = {".py", ".html", ".js", ".ts", ".yaml", ".yml", ".toml"}
_EXCLUDE_DIRS = {
    "alembic", "migrations", "__pycache__", ".git", "node_modules",
    "vendor", "dist", "build", ".venv", "venv", "env", "static",
}
_EXCLUDE_PREFIXES = ("tests/", "test/", ".github/")
_MAX_FILE_BYTES = 60_000


def _is_auditable_path(path: str) -> bool:
    if any(path.startswith(p) for p in _EXCLUDE_PREFIXES):
        return False
    parts = path.split("/")
    if any(p.lower() in _EXCLUDE_DIRS for p in parts[:-1]):
        return False
    import re
    # Skip Alembic version files like alembic/versions/001_xxx.py
    if re.match(r"\d{3}_.*\.py$", parts[-1]):
        return False
    suffix = "." + parts[-1].rsplit(".", 1)[-1] if "." in parts[-1] else ""
    return suffix.lower() in _AUDIT_EXTENSIONS


async def _fetch_github_files(
    repo: str,
    branch: str,
    token: str,
    since_sha: Optional[str] = None,
) -> list[dict]:
    """Fetch relevant source files from a GitHub repo via the REST API."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        if since_sha:
            # Diff mode: only changed files since last audit commit
            paths = await _diff_paths(client, repo, branch, since_sha, headers)
        else:
            # Full mode: all files in tree
            paths = await _tree_paths(client, repo, branch, headers)

        files: list[dict] = []
        total_bytes = 0
        _MAX_TOTAL = 1_500_000  # ~375 k tokens; trim before hitting LLM limits

        for path in paths:
            if not _is_auditable_path(path):
                continue
            if total_bytes >= _MAX_TOTAL:
                break
            try:
                r = await client.get(
                    f"https://api.github.com/repos/{repo}/contents/{path}",
                    params={"ref": branch},
                    headers=headers,
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                encoded = data.get("content", "")
                if not encoded:
                    continue
                raw = base64.b64decode(encoded.replace("\n", ""))
                if len(raw) > _MAX_FILE_BYTES:
                    raw = raw[:_MAX_FILE_BYTES]
                text = raw.decode("utf-8", errors="replace")
                files.append({"path": path, "content": text})
                total_bytes += len(raw)
            except Exception as exc:
                log.debug("security_audit: could not fetch %s: %s", path, exc)

    return files


async def _tree_paths(client, repo: str, branch: str, headers: dict) -> list[str]:
    r = await client.get(
        f"https://api.github.com/repos/{repo}/git/trees/{branch}",
        params={"recursive": "1"},
        headers=headers,
    )
    if r.status_code != 200:
        raise RuntimeError(f"GitHub tree API returned {r.status_code}: {r.text[:200]}")
    tree = r.json().get("tree", [])
    return [item["path"] for item in tree if item.get("type") == "blob"]


async def _diff_paths(
    client, repo: str, branch: str, since_sha: str, headers: dict
) -> list[str]:
    r = await client.get(
        f"https://api.github.com/repos/{repo}/compare/{since_sha}...{branch}",
        headers=headers,
    )
    if r.status_code != 200:
        log.warning("security_audit: diff API failed (%s) — falling back to full tree", r.status_code)
        return await _tree_paths(client, repo, branch, headers)
    files = r.json().get("files", [])
    return [f["filename"] for f in files if f.get("status") != "removed"]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _create_run(
    workspace_id: int,
    cycle_id: str,
    iteration: int,
    triggered_by: str,
    commit_sha: Optional[str],
    scope: str,
    session: AsyncSession,
) -> SecurityAuditRun:
    run = SecurityAuditRun(
        workspace_id=workspace_id,
        cycle_id=cycle_id,
        iteration=iteration,
        triggered_by=triggered_by,
        status="pending",
        commit_sha=commit_sha,
        scope=scope,
        created_at=_utcnow(),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def _require_config(workspace_id: int, session: AsyncSession) -> SecurityAuditConfig:
    cfg = (await session.exec(
        select(SecurityAuditConfig).where(SecurityAuditConfig.workspace_id == workspace_id)
    )).first()
    if not cfg:
        raise HTTPException(422, "No audit config — POST /security/config first")
    if not cfg.enabled:
        raise HTTPException(422, "Audit is disabled for this workspace")
    return cfg


async def _get_run_or_404(run_id: int, workspace_id: int, session: AsyncSession) -> SecurityAuditRun:
    run = (await session.exec(
        select(SecurityAuditRun).where(
            SecurityAuditRun.id == run_id,
            SecurityAuditRun.workspace_id == workspace_id,
        )
    )).first()
    if not run:
        raise HTTPException(404, f"Audit run {run_id} not found")
    return run


async def _fail_run(run_id: int, detail: str) -> None:
    from app.core.database import AsyncSessionFactory
    async with AsyncSessionFactory() as session:
        run = (await session.exec(select(SecurityAuditRun).where(SecurityAuditRun.id == run_id))).first()
        if run:
            run.status = "failed"
            run.completed_at = _utcnow()
            run.error_detail = detail[:2000]
            session.add(run)
            await session.commit()


def _sev_order(severity: str) -> int:
    return _SEVERITY_ORDER.get(severity, 0)


def _next_cron(cron_expr: Optional[str]) -> Optional[datetime]:
    """Compute next run datetime from a cron expression."""
    if not cron_expr:
        return None
    try:
        from croniter import croniter
        return croniter(cron_expr, _utcnow()).get_next(datetime)
    except Exception as exc:
        log.warning("security_audit: invalid cron expression %r: %s", cron_expr, exc)
        return None


def _verify_github_sig(payload: bytes, sig_header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def _cfg_to_dict(cfg: SecurityAuditConfig) -> dict:
    return {
        "id": cfg.id,
        "workspace_id": cfg.workspace_id,
        "enabled": cfg.enabled,
        "trigger_on_push": cfg.trigger_on_push,
        "schedule_cron": cfg.schedule_cron,
        "schedule_next_run_at": cfg.schedule_next_run_at.isoformat() + "Z" if cfg.schedule_next_run_at else None,
        "github_repo": cfg.github_repo,
        "github_branch": cfg.github_branch,
        "max_iterations": cfg.max_iterations,
        "max_cost_usd": cfg.max_cost_usd,
        "min_severity_to_stop": cfg.min_severity_to_stop,
        "created_at": cfg.created_at.isoformat() + "Z",
        "updated_at": cfg.updated_at.isoformat() + "Z",
    }


def _run_to_dict(run: SecurityAuditRun) -> dict:
    return {
        "id": run.id,
        "workspace_id": run.workspace_id,
        "cycle_id": run.cycle_id,
        "iteration": run.iteration,
        "triggered_by": run.triggered_by,
        "status": run.status,
        "scope": run.scope,
        "commit_sha": run.commit_sha,
        "cost_usd": run.cost_usd,
        "findings_total": run.findings_total,
        "findings_net_new": run.findings_net_new,
        "stop_reason": run.stop_reason,
        "error_detail": run.error_detail,
        "created_at": run.created_at.isoformat() + "Z",
        "started_at": run.started_at.isoformat() + "Z" if run.started_at else None,
        "completed_at": run.completed_at.isoformat() + "Z" if run.completed_at else None,
    }


def _finding_to_dict(f: AuditFinding) -> dict:
    return {
        "id": f.id,
        "run_id": f.run_id,
        "cycle_id": f.cycle_id,
        "severity": f.severity,
        "pattern_class": f.pattern_class,
        "file_path": f.file_path,
        "line_number": f.line_number,
        "title": f.title,
        "evidence": f.evidence,
        "reference_fix": f.reference_fix,
        "fingerprint": f.fingerprint,
        "first_seen_run_id": f.first_seen_run_id,
        "status": f.status,
        "ticket_id": f.ticket_id,
        "hitl_review_id": f.hitl_review_id,
        "created_at": f.created_at.isoformat() + "Z",
    }
