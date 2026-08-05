"""POST /run — trigger a pipeline run from the REST API."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, require_role, ws_accessible
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
    client_label: Optional[str] = None  # cost-center / client tag for spend breakdown
    write_back: bool = False  # if True, write artifacts back to repo branch after run
    model: Optional[str] = None  # override default model for the whole run (e.g. "groq:llama-3.3-70b")
    model_overrides: Optional[dict] = None  # per-agent overrides: {"BackendDevAgent": "claude:claude-sonnet-5"}

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
            client_label=body.client_label,
            write_back=body.write_back,
            model=body.model or "",
            model_overrides=body.model_overrides,
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


_TEAM_AGENTS: dict[str, list[dict]] = {
    "DevTeam": [
        {"name": "BusinessAnalystAgent", "description": "Analiza el brief y produce un spec estructurado de requisitos mediante Q&A.", "artifact_type": "requirements_spec", "position": 0},
        {"name": "PMAgent", "description": "Convierte el spec en un backlog de tickets priorizados.", "artifact_type": "tickets", "position": 1},
        {"name": "BackendDevAgent", "description": "Implementa el código backend, APIs REST y modelos de datos.", "artifact_type": "implementation_plan", "position": 2},
    ],
    "FullStackTeam": [
        {"name": "CodebaseScannerAgent", "description": "Escanea el repositorio existente para dar contexto antes de planificar.", "artifact_type": None, "position": 0},
        {"name": "BusinessAnalystAgent", "description": "Analiza requisitos y produce un spec estructurado.", "artifact_type": "requirements_spec", "position": 1},
        {"name": "PMAgent", "description": "Convierte el spec en un sprint backlog priorizado.", "artifact_type": "tickets", "position": 2},
        {"name": "SprintPlannerAgent", "description": "Planifica la capacidad del sprint y asigna tickets.", "artifact_type": None, "position": 3},
        {"name": "BackendDevAgent", "description": "Implementa APIs y capa de datos.", "artifact_type": "implementation_plan", "position": 4},
        {"name": "FrontendDevAgent", "description": "Construye componentes UI y páginas.", "artifact_type": None, "position": 5},
        {"name": "QAAgent", "description": "Escribe y ejecuta tests para la implementación.", "artifact_type": None, "position": 6},
        {"name": "ReviewerAgent", "description": "Revisa los artefactos y puede pausar para aprobación HITL.", "artifact_type": None, "position": 7},
        {"name": "DevOpsAgent", "description": "Configura CI/CD, Docker y despliegue.", "artifact_type": None, "position": 8},
        {"name": "DocWriterAgent", "description": "Escribe documentación de desarrollador y README.", "artifact_type": None, "position": 9},
    ],
    "ResearchTeam": [
        {"name": "ResearcherAgent", "description": "Investiga el tema desde múltiples fuentes y sintetiza los hallazgos clave.", "artifact_type": "research_report", "position": 0},
        {"name": "CopywriterAgent", "description": "Convierte los hallazgos en un escrito estructurado y pulido.", "artifact_type": None, "position": 1},
    ],
    "ContentTeam": [
        {"name": "IdeaAgent", "description": "Genera ideas de contenido y esquemas basados en el brief.", "artifact_type": None, "position": 0},
        {"name": "CopywriterAgent", "description": "Redacta el contenido a partir del esquema aprobado.", "artifact_type": None, "position": 1},
        {"name": "EditorAgent", "description": "Edita por claridad, tono y precisión.", "artifact_type": None, "position": 2},
    ],
    "FeatureTeam": [
        {"name": "FeatureAgent", "description": "Implementa una feature completa de extremo a extremo: requisitos, código y revisión en un solo paso.", "artifact_type": None, "position": 0},
    ],
}


@router.get("/teams/{team}/agents")
async def get_team_agents(team: str):
    """Return the ordered agent list for a team, with descriptions and artifact types."""
    if team not in AVAILABLE_TEAMS:
        raise HTTPException(404, f"Team {team!r} not found. Available: {AVAILABLE_TEAMS}")
    agents = _TEAM_AGENTS.get(team, [])
    return {"team": team, "agents": agents}


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


class VisualPipelineRequest(BaseModel):
    pipeline_id: str  # int ID for user pipelines; "template:xxx" for built-in templates
    request: str
    thread_id: str = "default"
    max_cost_usd: Optional[float] = None
    hitl: bool = False
    model: str = "claude"
    workspace_id: Optional[int] = None  # override workspace; falls back to API key scope

    @field_validator("request")
    @classmethod
    def request_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("request must not be empty")
        return v.strip()


@router.post("/visual", status_code=202, response_model=RunAccepted,
             dependencies=[Depends(require_role("admin", "write"))])
async def trigger_visual_pipeline(
    body: VisualPipelineRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Run a visually-defined pipeline by its pipeline_id.

    pipeline_id can be an integer (user pipeline) or "template:fullstack",
    "template:dev", "template:content", or "template:research" for built-in templates.
    """
    from app.api.pipelines import _TEMPLATES
    from app.services.runner import dispatch_pipeline

    # Resolve definition
    if isinstance(body.pipeline_id, str) and body.pipeline_id.startswith("template:"):
        template = next((t for t in _TEMPLATES if t["id"] == body.pipeline_id), None)
        if not template:
            raise HTTPException(404, f"Template {body.pipeline_id!r} not found")
        definition = template["definition"]
    else:
        try:
            pid = int(body.pipeline_id)
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid pipeline_id")
        from app.models.run import PipelineDef
        import json
        row = await session.get(PipelineDef, pid)
        if not row:
            raise HTTPException(404, "Pipeline not found")
        # Treat cross-workspace pipeline access as not-found to avoid confirming existence.
        if row.workspace_id is not None and not ws_accessible(row.workspace_id, ctx):
            raise HTTPException(404, "Pipeline not found")
        definition = json.loads(row.definition)

    # Validate that the caller can actually use the requested workspace.
    if body.workspace_id is not None and not ws_accessible(body.workspace_id, ctx):
        raise HTTPException(403, "workspace_id is not accessible with the current API key")

    effective_hitl = body.hitl
    if ctx.workspace_id is not None:
        from app.models.run import Workspace as _WS
        ws = (await session.exec(select(_WS).where(_WS.id == ctx.workspace_id))).first()
        if ws and not effective_hitl and ws.hitl_default:
            effective_hitl = True

    effective_workspace_id = body.workspace_id or ctx.workspace_id

    try:
        run_id = await dispatch_pipeline(
            definition=definition,
            request=body.request,
            thread_id=body.thread_id,
            max_cost_usd=body.max_cost_usd,
            created_by=ctx.created_by,
            workspace_id=effective_workspace_id,
            force_hitl=effective_hitl,
            model=body.model,
            pipeline_id=body.pipeline_id,
        )
    except (ValueError, ImportError) as exc:
        raise HTTPException(422, str(exc))

    return RunAccepted(
        run_id=run_id, team=f"visual:{body.pipeline_id}",
        hitl=effective_hitl, repo_context=False,
    )


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
