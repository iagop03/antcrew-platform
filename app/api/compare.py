"""POST /run/compare — run the same request against two LLM backends and diff the outputs.

Model diff: the definitive proof of LLM-agnosticism. Given that the Spec is a typed
artifact and the system is model-decoupled by design, this endpoint runs the same
request against two different backends (e.g. claude vs gpt-4o) and returns a structured
diff of code_artifacts, tickets, and PRD — with cost and latency per model.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, ws_accessible
from app.core.database import get_session
from app.models.run import CompareRun, Run
from app.services.runner import dispatch, AVAILABLE_TEAMS
from app.services.engine_runner import dispatch_engine

router = APIRouter(
    prefix="/run",
    tags=["compare"],
    dependencies=[Depends(require_api_key)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CompareRequest(BaseModel):
    team: str
    request: str = ""   # for team runs
    goal: str = ""      # for engine runs (team="engine")
    model_a: str = "claude"
    model_b: str
    max_cost_usd: Optional[float] = None


class CompareCreated(BaseModel):
    compare_id: str
    run_id_a: str
    run_id_b: str
    model_a: str
    model_b: str
    status: str = "running"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_state_summary(state: dict | None) -> dict:
    if not state:
        return {
            "tickets": [], "code_files": [], "doc_files": [], "test_files": [],
            "prd_title": "", "review_verdict": "",
        }

    def _names(raw: list, key: str = "file_path") -> list[str]:
        return [
            a.get("filename", a.get(key, "")) if isinstance(a, dict) else str(a)
            for a in (raw or [])
        ]

    tickets_raw = state.get("tickets") or []
    ticket_titles = [t.get("title", "") if isinstance(t, dict) else str(t) for t in tickets_raw]

    prd = state.get("prd") or {}
    prd_title = prd.get("title", "") if isinstance(prd, dict) else str(prd)[:80]

    return {
        "tickets":        ticket_titles,
        "code_files":     _names(state.get("code_artifacts") or []),
        "doc_files":      _names(state.get("doc_artifacts")  or []),
        "test_files":     _names(state.get("test_artifacts") or []),
        "prd_title":      prd_title,
        "review_verdict": state.get("review_verdict", ""),
    }


def _set_diff(list_a: list[str], list_b: list[str]) -> dict:
    set_a = set(list_a)
    set_b = set(list_b)
    return {
        "only_in_a": sorted(set_a - set_b),
        "only_in_b": sorted(set_b - set_a),
        "shared": sorted(set_a & set_b),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/compare", status_code=202, response_model=CompareCreated)
async def create_compare(
    body: CompareRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Start a model-diff comparison: run the same request with two different LLM backends.

    Returns 202 immediately with compare_id. Poll GET /run/compare/{compare_id} to get
    the diff once both runs complete.

    Both runs execute in parallel. Each is a full pipeline run (visible in GET /runs/)
    and counted toward workspace budget.
    """
    if body.model_a == body.model_b:
        raise HTTPException(422, "model_a and model_b must be different to produce a meaningful diff")

    is_engine = body.team == "engine"
    if is_engine:
        if not body.goal:
            raise HTTPException(422, "goal is required for engine compares (team='engine')")
    elif body.team not in AVAILABLE_TEAMS:
        raise HTTPException(422, f"Unknown team {body.team!r}. Available: {AVAILABLE_TEAMS}")
    elif not body.request:
        raise HTTPException(422, "request is required for team compares")

    compare_id = str(uuid.uuid4())

    if is_engine:
        run_id_a, run_id_b = await asyncio.gather(
            dispatch_engine(
                goal=body.goal,
                model=body.model_a,
                max_cost_usd=body.max_cost_usd,
                workspace_id=ctx.workspace_id,
                created_by=ctx.created_by,
            ),
            dispatch_engine(
                goal=body.goal,
                model=body.model_b,
                max_cost_usd=body.max_cost_usd,
                workspace_id=ctx.workspace_id,
                created_by=ctx.created_by,
            ),
        )
        stored_request = body.goal
    else:
        thread_a = f"cmp-{compare_id[:8]}-a"
        thread_b = f"cmp-{compare_id[:8]}-b"
        run_id_a, run_id_b = await asyncio.gather(
            dispatch(
                body.team, body.request, thread_id=thread_a,
                model=body.model_a, max_cost_usd=body.max_cost_usd,
                workspace_id=ctx.workspace_id, created_by=ctx.created_by,
            ),
            dispatch(
                body.team, body.request, thread_id=thread_b,
                model=body.model_b, max_cost_usd=body.max_cost_usd,
                workspace_id=ctx.workspace_id, created_by=ctx.created_by,
            ),
        )
        stored_request = body.request

    if not run_id_a or not run_id_b:
        raise HTTPException(500, "Failed to dispatch one or both comparison runs. Check server logs.")

    row = CompareRun(
        compare_id=compare_id,
        run_id_a=run_id_a,
        run_id_b=run_id_b,
        model_a=body.model_a,
        model_b=body.model_b,
        team=body.team,
        request=stored_request,
        workspace_id=ctx.workspace_id,
    )
    session.add(row)
    await session.commit()

    return CompareCreated(
        compare_id=compare_id,
        run_id_a=run_id_a,
        run_id_b=run_id_b,
        model_a=body.model_a,
        model_b=body.model_b,
    )


@router.get("/compare/{compare_id}")
async def get_compare(
    compare_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Get the diff result for a model comparison.

    While both runs are still executing, status is "running" and diff is null.
    Once both complete, returns:
    - Per-model cost and latency
    - code_files diff (only_in_a, only_in_b, shared)
    - tickets diff
    - PRD titles
    - Summary stats with winners on cost and speed
    """
    row = (await session.exec(select(CompareRun).where(CompareRun.compare_id == compare_id))).first()
    if not row:
        raise HTTPException(404, f"Comparison {compare_id!r} not found")
    if not ws_accessible(row.workspace_id, ctx):
        raise HTTPException(403, "Not accessible with the current API key")

    run_a = (await session.exec(select(Run).where(Run.run_id == row.run_id_a))).first()
    run_b = (await session.exec(select(Run).where(Run.run_id == row.run_id_b))).first()

    if not run_a or not run_b:
        raise HTTPException(500, "One or both comparison runs not found in database")

    still_running = run_a.status == "running" or run_b.status == "running"
    if still_running:
        return {
            "compare_id": compare_id,
            "status": "running",
            "model_a": {"name": row.model_a, "run_id": row.run_id_a, "status": run_a.status},
            "model_b": {"name": row.model_b, "run_id": row.run_id_b, "status": run_b.status},
            "diff": None,
        }

    summary_a = _extract_state_summary(run_a.state)
    summary_b = _extract_state_summary(run_b.state)

    code_diff = _set_diff(summary_a["code_files"], summary_b["code_files"])
    ticket_diff = _set_diff(summary_a["tickets"], summary_b["tickets"])
    doc_diff = _set_diff(summary_a["doc_files"], summary_b["doc_files"])
    test_diff = _set_diff(summary_a["test_files"], summary_b["test_files"])

    cost_a = run_a.cost_usd or 0.0
    cost_b = run_b.cost_usd or 0.0
    dur_a = run_a.duration_s or 0.0
    dur_b = run_b.duration_s or 0.0

    any_error = run_a.status == "error" or run_b.status == "error"
    final_status = "error" if any_error else "done"

    if row.status == "running":
        row.status = final_status
        row.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(row)
        await session.commit()

    return {
        "compare_id": compare_id,
        "status": final_status,
        "model_a": {
            "name": row.model_a,
            "run_id": row.run_id_a,
            "status": run_a.status,
            "cost_usd": cost_a,
            "duration_s": dur_a,
        },
        "model_b": {
            "name": row.model_b,
            "run_id": row.run_id_b,
            "status": run_b.status,
            "cost_usd": cost_b,
            "duration_s": dur_b,
        },
        "diff": {
            "code_files": code_diff,
            "tickets": ticket_diff,
            "doc_files": doc_diff,
            "test_files": test_diff,
            "prd": {
                "a": summary_a["prd_title"],
                "b": summary_b["prd_title"],
            },
            "review_verdict": {
                "a": summary_a["review_verdict"],
                "b": summary_b["review_verdict"],
            },
            "summary": {
                "code_file_count": {"a": len(summary_a["code_files"]), "b": len(summary_b["code_files"])},
                "ticket_count": {"a": len(summary_a["tickets"]), "b": len(summary_b["tickets"])},
                "cost_usd": {
                    "a": round(cost_a, 6),
                    "b": round(cost_b, 6),
                    "winner": "b" if cost_b < cost_a else ("a" if cost_a < cost_b else "tie"),
                },
                "duration_s": {
                    "a": round(dur_a, 2),
                    "b": round(dur_b, 2),
                    "winner": "b" if dur_b < dur_a else ("a" if dur_a < dur_b else "tie"),
                },
            },
        },
    }


@router.get("/compare")
async def list_compares(
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> list[dict]:
    """List recent model-diff comparisons for this workspace."""
    q = (
        select(CompareRun)
        .order_by(desc(CompareRun.id))
        .limit(min(limit, 100))
    )
    if ctx.workspace_id is not None:
        q = q.where(CompareRun.workspace_id == ctx.workspace_id)
    rows = (await session.exec(q)).all()
    return [
        {
            "compare_id": r.compare_id,
            "team": r.team,
            "model_a": r.model_a,
            "model_b": r.model_b,
            "status": r.status,
            "request_preview": r.request[:80],
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows
    ]
