"""POST /run — trigger a pipeline run from the REST API."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, require_role
from app.core.database import get_session
from app.services.runner import dispatch, AVAILABLE_TEAMS, ALL_PIPELINE_TYPES

router = APIRouter(prefix="/run", tags=["pipeline"], dependencies=[Depends(require_api_key)])

_REPO_URL_RE = re.compile(
    r"^(https?://[\w.\-]+/[\w.\-/]+|git@[\w.\-]+:[\w.\-/]+)(\.git)?$"
)


class RunRequest(BaseModel):
    team: str
    request: str
    thread_id: str = "default"
    max_cost_usd: Optional[float] = None
    hitl: bool = False  # if True, inject PlatformChannel into all agents for this run
    repo_url: Optional[str] = None  # public or private git repo to inject as context
    repo_token: Optional[str] = None  # PAT for private HTTPS repos (never stored)

    @field_validator("team")
    @classmethod
    def team_must_be_valid(cls, v: str) -> str:
        if v not in AVAILABLE_TEAMS:
            raise ValueError(f"Unknown team {v!r}. Available: {AVAILABLE_TEAMS}")
        return v

    @field_validator("request")
    @classmethod
    def request_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("request must not be empty")
        return v.strip()

    @field_validator("max_cost_usd")
    @classmethod
    def cost_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("max_cost_usd must be positive")
        return v

    @field_validator("repo_url")
    @classmethod
    def repo_url_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not _REPO_URL_RE.match(v):
            raise ValueError(
                "repo_url must be an HTTPS or SSH git URL "
                "(e.g. https://github.com/org/repo or git@github.com:org/repo)"
            )
        return v


class RunAccepted(BaseModel):
    status: str = "accepted"
    run_id: Optional[str]
    team: str
    hitl: bool = False
    repo_context: bool = False
    hint: str = "Poll GET /runs or connect to WS /ws/events for real-time updates"


@router.post("/", status_code=202, response_model=RunAccepted,
             dependencies=[Depends(require_role("admin", "write"))])
async def trigger_run(
    body: RunRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Start a pipeline run asynchronously.

    Set `hitl: true` to inject platform HITL review into all agents for this run.
    Set `repo_url` to clone a git repository and inject its file tree + source files
    as context — agents will see the codebase before producing tickets and code.
    When `repo_url` is omitted, falls back to the workspace's `default_repo_url`.
    The run is automatically scoped to the API key's workspace if one is configured.
    Returns 202 Accepted with the run_id once the pipeline emits its first event.
    """
    from app.models.run import Workspace
    effective_repo_url = body.repo_url
    effective_hitl = body.hitl

    if ctx.workspace_id is not None:
        ws = (await session.exec(select(Workspace).where(Workspace.id == ctx.workspace_id))).first()
        if ws:
            if effective_repo_url is None and ws.default_repo_url:
                effective_repo_url = ws.default_repo_url
            if not effective_hitl and ws.hitl_default:
                effective_hitl = True

    try:
        run_id = await dispatch(
            body.team, body.request, body.thread_id,
            max_cost_usd=body.max_cost_usd,
            created_by=ctx.created_by,
            workspace_id=ctx.workspace_id,
            force_hitl=effective_hitl,
            repo_url=effective_repo_url,
            repo_token=body.repo_token,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    return RunAccepted(
        run_id=run_id, team=body.team, hitl=effective_hitl,
        repo_context=effective_repo_url is not None,
    )


@router.get("/teams")
async def list_teams():
    """List teams that can be triggered via POST /run (or POST /run/pipeline for custom)."""
    return {"teams": ALL_PIPELINE_TYPES}


class AgentStepConfig(BaseModel):
    """One agent step definition — mirrors TemplateAgent YAML fields."""
    name: str
    system_prompt: str
    input_key: str = "request"
    output_key: str = ""
    max_tokens: int = 4096
    output_json: bool = False
    interpolate: bool = True
    user_template: str = ""


class CustomPipelineRequest(BaseModel):
    request: str
    steps: list[AgentStepConfig]
    thread_id: str = "default"
    max_cost_usd: Optional[float] = None
    hitl: bool = False
    model: str = "claude"  # LLM model name passed to build_llm()

    @field_validator("steps")
    @classmethod
    def steps_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("steps must not be empty")
        if len(v) > 20:
            raise ValueError("steps must have 20 or fewer agents")
        return v

    @field_validator("request")
    @classmethod
    def request_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("request must not be empty")
        return v.strip()


@router.post("/pipeline", status_code=202, response_model=RunAccepted,
             dependencies=[Depends(require_role("admin", "write"))])
async def trigger_custom_pipeline(
    body: CustomPipelineRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Start a custom multi-agent pipeline defined inline via a list of agent steps.

    Each step is a TemplateAgent config: name, system_prompt, input/output keys.
    Steps run sequentially; each agent's output_key is available as input to
    subsequent agents via {placeholder} interpolation in their system_prompt.

    Requires antcrew >= 0.14 (TemplateAgent and CustomTeam must be importable).
    """
    from app.services.runner import dispatch_custom

    if ctx.workspace_id is not None:
        from app.models.run import Workspace as _WS
        ws = (await session.exec(select(_WS).where(_WS.id == ctx.workspace_id))).first()
        effective_hitl = body.hitl or (ws.hitl_default if ws else False)
    else:
        effective_hitl = body.hitl

    try:
        run_id = await dispatch_custom(
            steps=[s.model_dump() for s in body.steps],
            request=body.request,
            thread_id=body.thread_id,
            max_cost_usd=body.max_cost_usd,
            created_by=ctx.created_by,
            workspace_id=ctx.workspace_id,
            force_hitl=effective_hitl,
            model=body.model,
        )
    except (ValueError, ImportError) as exc:
        raise HTTPException(422, str(exc))

    return RunAccepted(
        run_id=run_id, team="custom", hitl=effective_hitl, repo_context=False,
    )
