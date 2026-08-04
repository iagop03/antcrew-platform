"""Discovery session endpoints — conversational project discovery via DiscoveryAgent.

Flow:
  POST /discovery/sessions            → create session, get first question
  POST /discovery/sessions/{id}/answer → submit answer, get next question
  GET  /discovery/sessions/{id}       → read current state
  POST /discovery/sessions/{id}/run   → finalize → PRD → dispatch pipeline run
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, require_role
from app.core.database import get_session
from app.models.discovery import DiscoverySession

router = APIRouter(
    prefix="/discovery",
    tags=["discovery"],
    dependencies=[Depends(require_api_key)],
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── private helpers ────────────────────────────────────────────────────────────

def _build_agent(model: str):
    """Instantiate a DiscoveryAgent backed by the given model name."""
    from antcrew import build_llm
    from antcrew.agents.discovery import DiscoveryAgent
    llm = build_llm(model)
    return DiscoveryAgent(llm=llm)


def _load_context(row: DiscoverySession):
    """Deserialise a DiscoverySession row into a DiscoveryContext object."""
    from antcrew.core.artifacts import DiscoveryContext
    data = json.loads(row.context_json or "{}")
    return DiscoveryContext(**data)


def _dump_context(ctx) -> str:
    """Serialise a DiscoveryContext to a JSON string for DB storage."""
    return ctx.model_dump_json()


async def _fetch_session(
    session_id: str,
    db: AsyncSession,
    ctx: WorkspaceContext,
) -> DiscoverySession:
    result = await db.exec(
        select(DiscoverySession).where(DiscoverySession.session_id == session_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(404, f"Discovery session {session_id!r} not found")
    if ctx.workspace_ids is not None and row.workspace_id not in ctx.workspace_ids:
        raise HTTPException(403, "Access denied")
    return row


# ── request / response schemas ─────────────────────────────────────────────────

class _CreateBody(BaseModel):
    project_name: str = ""
    model: str = "claude"


class _AnswerBody(BaseModel):
    answer: str


class _RunBody(BaseModel):
    team: str = "DevTeam"


# ── endpoints ──────────────────────────────────────────────────────────────────

@router.post("/sessions", status_code=201)
async def create_session(
    body: _CreateBody,
    db: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Create a new discovery session and return the first agent question."""
    from antcrew.core.artifacts import DiscoveryContext

    sid = str(uuid.uuid4())
    ctx_obj = DiscoveryContext(project_name=body.project_name)

    agent = _build_agent(body.model)
    result: dict = await asyncio.to_thread(agent.ask_next, ctx_obj)

    row = DiscoverySession(
        session_id=sid,
        workspace_id=ctx.workspace_id,
        model=body.model,
        status="active",
        context_json=_dump_context(ctx_obj),
        current_question=result.get("next_question", ""),
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return {
        "session_id": sid,
        "question": result.get("next_question", ""),
        "context": json.loads(row.context_json),
    }


@router.post("/sessions/{session_id}/answer")
async def answer_question(
    session_id: str,
    body: _AnswerBody,
    db: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Submit an answer and receive the next question (or null when discovery is complete)."""
    row = await _fetch_session(session_id, db, ctx)
    if row.status == "complete":
        raise HTTPException(409, "Session is already complete")

    agent = _build_agent(row.model)
    ctx_obj = _load_context(row)
    question = row.current_question

    # ingest answer → updated context
    ctx_obj = await asyncio.to_thread(agent.ingest, ctx_obj, question, body.answer)

    # ask for the next question
    result: dict = await asyncio.to_thread(agent.ask_next, ctx_obj)
    is_complete: bool = result.get("is_complete", False)
    next_q: str = result.get("next_question", "")

    row.context_json = _dump_context(ctx_obj)
    row.current_question = next_q
    row.updated_at = _utcnow()
    if is_complete:
        row.status = "complete"

    db.add(row)
    await db.commit()

    return {
        "question": next_q if not is_complete else None,
        "is_complete": is_complete,
        "context": json.loads(row.context_json),
    }


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: str,
    db: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return the current state of a discovery session."""
    row = await _fetch_session(session_id, db, ctx)
    return {
        "session_id": row.session_id,
        "status": row.status,
        "context": json.loads(row.context_json),
        "current_question": row.current_question,
    }


@router.post(
    "/sessions/{session_id}/run",
    status_code=202,
    dependencies=[Depends(require_role("admin", "write"))],
)
async def run_from_session(
    session_id: str,
    body: _RunBody,
    db: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Finalize the discovery context into a PRD and dispatch a pipeline run.

    The PRD summary is used as the run request.  Returns the new run_id.
    """
    from app.services.runner import dispatch, AVAILABLE_TEAMS

    row = await _fetch_session(session_id, db, ctx)
    agent = _build_agent(row.model)
    ctx_obj = _load_context(row)

    # finalize → PRD
    prd = await asyncio.to_thread(agent.finalize, ctx_obj)

    # Build a human-readable request from the PRD
    prd_dict = prd.model_dump() if hasattr(prd, "model_dump") else prd.dict()
    title = prd_dict.get("title") or ctx_obj.project_name or "Untitled"
    summary = prd_dict.get("summary") or ""
    goals = prd_dict.get("goals") or []
    goals_text = "\n".join(f"- {g}" for g in goals) if goals else ""
    request_text = f"{title}\n\n{summary}"
    if goals_text:
        request_text += f"\n\nGoals:\n{goals_text}"

    team = body.team
    if team not in AVAILABLE_TEAMS:
        team = AVAILABLE_TEAMS[0]  # fall back to first available team

    try:
        run_id = await dispatch(
            team,
            request_text.strip(),
            "default",
            created_by=ctx.created_by,
            workspace_id=ctx.workspace_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    row.status = "complete"
    row.updated_at = _utcnow()
    db.add(row)
    await db.commit()

    return {"run_id": run_id}
