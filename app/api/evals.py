"""POST /evals/ — run an antcrew EvalCase against a team (async, persisted to DB)."""
from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, ws_filter, ws_accessible
from app.core.database import get_session
from app.models.run import EvalRun, Run
from app.services.runner import AVAILABLE_TEAMS
from app.services.eval_runner import EvalRunConfig, run_eval_sync, _executor

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/evals",
    tags=["evals"],
    dependencies=[Depends(require_api_key)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EvalRequest(BaseModel):
    team: str
    request: str
    name: str = ""
    model: str = ""
    judge_model: str = ""
    expect_min_tickets: int = 0
    expect_min_code_files: int = 0
    expect_review_verdict: str = ""


class EvalCreated(BaseModel):
    eval_id: str
    status: str = "running"
    team: str
    request: str
    name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", status_code=202, response_model=EvalCreated)
async def create_eval(
    body: EvalRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Start an eval run asynchronously. Returns 202 with eval_id to poll or list.

    The eval runs the full antcrew pipeline then scores output quality without
    an LLM judge (deterministic metrics: ticket count, code files, review verdict).
    """
    if body.team not in AVAILABLE_TEAMS:
        raise HTTPException(422, f"Unknown team {body.team!r}. Available: {AVAILABLE_TEAMS}")

    eval_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    name = body.name or body.request[:60]

    # Stub Run so the eval appears in the runs dashboard and cost rolls up to workspace.
    run_stub = Run(
        run_id=run_id,
        thread_id="eval",
        team=body.team,
        request=body.request,
        status="running",
        workspace_id=ctx.workspace_id,
        created_by=ctx.created_by,
    )
    session.add(run_stub)

    row = EvalRun(
        eval_id=eval_id,
        run_id=run_id,
        team=body.team,
        request=body.request,
        name=name,
        model=body.model,
        judge_model=body.judge_model,
        status="running",
        workspace_id=ctx.workspace_id,
    )
    session.add(row)
    await session.commit()

    cfg = EvalRunConfig(
        team=body.team,
        request=body.request,
        name=body.name,
        model=body.model,
        judge_model=body.judge_model,
        expect_min_tickets=body.expect_min_tickets,
        expect_min_code_files=body.expect_min_code_files,
        expect_review_verdict=body.expect_review_verdict,
    )
    loop = asyncio.get_running_loop()
    loop.run_in_executor(_executor, functools.partial(run_eval_sync, eval_id, cfg, loop))

    return EvalCreated(eval_id=eval_id, team=body.team, request=body.request, name=name)


class EvalReportUpload(BaseModel):
    """Pre-computed eval report from a local CLI run."""
    team: str
    request: str
    name: str = ""
    report: dict
    elapsed_ms: float = 0.0
    cost_usd: float = 0.0


@router.post("/report", status_code=201, response_model=EvalRun)
async def upload_eval_report(
    body: EvalReportUpload,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Store a pre-computed eval report from a local CLI run.

    Use with ``antcrew eval --push-to <platform-url>`` to publish local results
    to the platform dashboard without re-running the eval on the server.
    """
    if body.team not in AVAILABLE_TEAMS:
        raise HTTPException(422, f"Unknown team {body.team!r}. Available: {AVAILABLE_TEAMS}")

    from datetime import datetime, timezone
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    run_stub = Run(
        run_id=run_id,
        thread_id="eval",
        team=body.team,
        request=body.request,
        status="success",
        cost_usd=body.cost_usd,
        duration_s=round(body.elapsed_ms / 1000, 3) if body.elapsed_ms else None,
        workspace_id=ctx.workspace_id,
        created_by=ctx.created_by,
        finished_at=now,
    )
    session.add(run_stub)

    row = EvalRun(
        eval_id=str(uuid.uuid4()),
        run_id=run_id,
        team=body.team,
        request=body.request,
        name=body.name or body.request[:60],
        status="done",
        report=body.report,
        cost_usd=body.cost_usd,
        elapsed_ms=body.elapsed_ms,
        workspace_id=ctx.workspace_id,
        finished_at=now,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("/", response_model=list[EvalRun])
async def list_evals(
    status: Optional[str] = Query(None, description="Filter by status (running, done, error)"),
    team: Optional[str] = None,
    regression_id: Optional[str] = Query(None, description="Filter by regression batch ID"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """List eval runs, newest first."""
    q = select(EvalRun).order_by(desc(EvalRun.id)).limit(limit)
    if offset:
        q = q.offset(offset)
    if status:
        q = q.where(EvalRun.status == status)
    if team:
        q = q.where(EvalRun.team == team)
    if regression_id:
        q = q.where(EvalRun.regression_id == regression_id)
    q = ws_filter(q, EvalRun.workspace_id, ctx)
    result = await session.exec(q)
    return result.all()


class RegressionRequest(BaseModel):
    """Re-run a set of historical pipeline runs with the current prompts to detect regressions.

    For each run_id, the original request is replayed with the current pipeline configuration
    and the output is scored against what the baseline run produced. This is "CI for your
    own agents": change a capability prompt, run regression, see what drifted.
    """
    run_ids: list[str]
    team: str = ""        # override the team; defaults to each run's original team
    model: str = ""       # optional model override
    judge_model: str = ""


@router.post("/regression", status_code=202)
async def start_regression(
    body: RegressionRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run historical runs against current prompts and detect quality regression.

    Returns regression_id + one eval_id per input run. Poll GET /evals/regression/{regression_id}
    to get aggregate status. Each eval can also be queried individually via GET /evals/{eval_id}.

    Scoring thresholds:
    - expect_min_tickets = floor(original_count * 0.8)  — 20% tolerance on ticket count
    - expect_min_code_files = floor(original_count * 0.8)
    - expect_review_verdict = original verdict (if any)
    """
    from sqlmodel import select as _sel
    from app.models.run import Run as _Run

    if not body.run_ids:
        raise HTTPException(422, "run_ids must not be empty")
    if len(body.run_ids) > 20:
        raise HTTPException(422, "Maximum 20 run_ids per regression batch")

    regression_id = str(uuid.uuid4())
    cases = []
    loop = asyncio.get_running_loop()

    for run_id in body.run_ids:
        baseline = (await session.exec(_sel(_Run).where(_Run.run_id == run_id))).first()
        if not baseline:
            raise HTTPException(404, f"Run {run_id!r} not found")
        if not ws_accessible(baseline.workspace_id, ctx):
            raise HTTPException(403, f"Run {run_id!r} is not accessible with the current API key")

        state = baseline.state or {}
        tickets_count = len(state.get("tickets") or [])
        code_count = len(state.get("code_artifacts") or [])
        review_verdict = state.get("review_verdict", "")

        team = body.team or baseline.team
        if team not in AVAILABLE_TEAMS:
            raise HTTPException(
                422,
                f"Team {team!r} (from run {run_id!r}) not available. Available: {AVAILABLE_TEAMS}",
            )

        eval_id = str(uuid.uuid4())
        new_run_id = str(uuid.uuid4())
        name = f"[reg:{regression_id[:8]}] {baseline.request[:50]}"

        run_stub = Run(
            run_id=new_run_id,
            thread_id="regression",
            team=team,
            request=baseline.request,
            status="running",
            workspace_id=ctx.workspace_id,
            created_by=ctx.created_by,
        )
        session.add(run_stub)

        row = EvalRun(
            eval_id=eval_id,
            run_id=new_run_id,
            team=team,
            request=baseline.request,
            name=name,
            model=body.model,
            judge_model=body.judge_model,
            status="running",
            workspace_id=ctx.workspace_id,
            regression_id=regression_id,
        )
        session.add(row)

        cfg = EvalRunConfig(
            team=team,
            request=baseline.request,
            name=name,
            model=body.model,
            judge_model=body.judge_model,
            expect_min_tickets=int(tickets_count * 0.8),
            expect_min_code_files=int(code_count * 0.8),
            expect_review_verdict=review_verdict,
        )
        loop.run_in_executor(_executor, functools.partial(run_eval_sync, eval_id, cfg, loop))

        cases.append({
            "eval_id": eval_id,
            "baseline_run_id": run_id,
            "team": team,
            "request_preview": baseline.request[:80],
            "baseline": {
                "tickets": tickets_count,
                "code_files": code_count,
                "review_verdict": review_verdict,
            },
        })

    await session.commit()
    return {
        "regression_id": regression_id,
        "baseline_count": len(body.run_ids),
        "cases": cases,
        "hint": f"Poll GET /evals/regression/{regression_id} for aggregate status and pass rate",
    }


@router.get("/regression/{regression_id}")
async def get_regression(
    regression_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Get the aggregate status and results for a regression batch.

    regression_rate is the fraction of evals that failed their baseline thresholds.
    A regression_rate > 0 means at least one run degraded after the prompt change.
    """
    rows = (await session.exec(
        select(EvalRun).where(EvalRun.regression_id == regression_id)
    )).all()
    if not rows:
        raise HTTPException(404, f"Regression {regression_id!r} not found")
    for row in rows:
        if not ws_accessible(row.workspace_id, ctx):
            raise HTTPException(403, "Not accessible with the current API key")

    total = len(rows)
    done = sum(1 for r in rows if r.status == "done")
    errors = sum(1 for r in rows if r.status == "error")
    running = total - done - errors

    scored = [r for r in rows if r.status == "done" and r.report]
    passed = sum(1 for r in scored if r.report.get("passed", False))
    regression_rate = round(1 - (passed / len(scored)), 4) if scored else None

    return {
        "regression_id": regression_id,
        "status": "running" if running > 0 else ("done" if done > 0 else "error"),
        "total": total,
        "done": done,
        "running": running,
        "errors": errors,
        "passed": passed,
        "failed": len(scored) - passed,
        "regression_rate": regression_rate,
        "cases": [
            {
                "eval_id": r.eval_id,
                "name": r.name,
                "status": r.status,
                "passed": r.report.get("passed", False) if r.report else None,
                "overall_score": r.report.get("overall_score") if r.report else None,
                "cost_usd": r.cost_usd,
                "elapsed_ms": r.elapsed_ms,
            }
            for r in rows
        ],
    }


@router.get("/compare")
async def compare_evals(
    a: str = Query(..., description="eval_id of the baseline eval"),
    b: str = Query(..., description="eval_id of the candidate eval"),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Compare two eval runs and return a score delta.

    Useful for regression detection: run the same eval before/after a code
    change and compare overall_score to detect quality drops.

    ``regression`` is True when the candidate score is more than 5 percentage
    points lower than the baseline.
    """
    def _fetch_or_404(eval_id: str):
        return select(EvalRun).where(EvalRun.eval_id == eval_id)

    row_a = (await session.exec(_fetch_or_404(a))).first()
    row_b = (await session.exec(_fetch_or_404(b))).first()

    if not row_a:
        raise HTTPException(404, f"Baseline eval {a!r} not found")
    if not row_b:
        raise HTTPException(404, f"Candidate eval {b!r} not found")

    for row, label in ((row_a, "baseline"), (row_b, "candidate")):
        if not ws_accessible(row.workspace_id, ctx):
            raise HTTPException(403, f"The {label} eval is not accessible with the current API key")

    def _score(row: EvalRun) -> float:
        if row.report and isinstance(row.report, dict):
            return float(row.report.get("overall_score", 0.0))
        return 0.0

    score_a = _score(row_a)
    score_b = _score(row_b)
    delta = round(score_b - score_a, 4)

    def _summary(row: EvalRun) -> dict:
        return {
            "eval_id": row.eval_id,
            "name": row.name,
            "team": row.team,
            "status": row.status,
            "overall_score": _score(row),
            "passed": row.report.get("passed", False) if row.report else False,
            "cost_usd": row.cost_usd,
            "elapsed_ms": row.elapsed_ms,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    return {
        "baseline": _summary(row_a),
        "candidate": _summary(row_b),
        "delta": {
            "overall_score": delta,
            "cost_usd": round(row_b.cost_usd - row_a.cost_usd, 6),
            "elapsed_ms": round(row_b.elapsed_ms - row_a.elapsed_ms, 1),
            "regression": delta < -0.05,
            "improved": delta > 0.05,
        },
    }


@router.get("/{eval_id}", response_model=EvalRun)
async def get_eval(
    eval_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Get a single eval run by ID. Includes the full report when status is 'done'."""
    result = await session.exec(select(EvalRun).where(EvalRun.eval_id == eval_id))
    row = result.first()
    if not row:
        raise HTTPException(404, f"Eval {eval_id!r} not found")
    if not ws_accessible(row.workspace_id, ctx):
        raise HTTPException(403, "This eval is not accessible with the current API key")
    return row
