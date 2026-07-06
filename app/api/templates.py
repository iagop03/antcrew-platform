"""Run template CRUD — save and reuse run configurations."""
from __future__ import annotations

import re as _re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, ws_filter, ws_accessible
from app.core.database import get_session
from app.models.run import RunTemplate
from app.services.runner import AVAILABLE_TEAMS

router = APIRouter(
    prefix="/templates",
    tags=["templates"],
    dependencies=[Depends(require_api_key)],
)

_REPO_URL_RE = _re.compile(
    r"^(https?://[\w.\-]+/[\w.\-/]+|git@[\w.\-]+:[\w.\-/]+)(\.git)?$"
)


class CreateTemplate(BaseModel):
    name: str
    team: str
    request: str
    max_cost_usd: Optional[float] = None
    hitl: bool = False
    repo_url: Optional[str] = None

    @field_validator("team")
    @classmethod
    def team_must_be_valid(cls, v: str) -> str:
        if v not in AVAILABLE_TEAMS:
            raise ValueError(f"Unknown team {v!r}. Available: {AVAILABLE_TEAMS}")
        return v

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()

    @field_validator("repo_url")
    @classmethod
    def repo_url_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not _REPO_URL_RE.match(v):
            raise ValueError("repo_url must be an HTTPS or SSH git URL")
        return v


@router.get("/", response_model=list[RunTemplate])
async def list_templates(
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """List templates. Scoped to the API key's workspace when set."""
    stmt = select(RunTemplate).order_by(desc(RunTemplate.created_at)).limit(limit)
    stmt = ws_filter(stmt, RunTemplate.workspace_id, ctx)
    result = await session.exec(stmt)
    return list(result.all())


@router.post("/", status_code=201, response_model=RunTemplate)
async def create_template(
    body: CreateTemplate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Create a reusable run template. Automatically scoped to the API key's workspace."""
    template = RunTemplate(
        name=body.name,
        team=body.team,
        request=body.request,
        max_cost_usd=body.max_cost_usd,
        hitl=body.hitl,
        repo_url=body.repo_url,
        workspace_id=ctx.workspace_id,
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    result = await session.exec(select(RunTemplate).where(RunTemplate.id == template_id))
    template = result.first()
    if not template:
        raise HTTPException(404, f"Template {template_id} not found")
    if not ws_accessible(template.workspace_id, ctx):
        raise HTTPException(403, "This template is not accessible with the current API key")
    await session.delete(template)
    await session.commit()
