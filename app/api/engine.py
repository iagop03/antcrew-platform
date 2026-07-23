"""POST /engine/run — trigger a capability-driven engine run from the REST API.

Unlike POST /run (which dispatches a role-based team), the engine endpoint
accepts a natural-language goal and lets the Operator decide which capabilities
to invoke and in what order.

Run lifecycle is identical to team runs:
  - pipeline.start  fires immediately → Run row created in DB
  - agent.start / agent.end  fire for each capability execution
  - pipeline.end  fires on success or error
  - GET /runs and WS /ws/events work without any changes
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, require_role, ws_accessible
from app.core.database import get_session
from app.models.run import Event, Run
from app.services.engine_runner import AVAILABLE_ENGINE_CAPABILITIES

router = APIRouter(
    prefix="/engine",
    tags=["engine"],
    dependencies=[Depends(require_api_key)],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class EngineRunRequest(BaseModel):
    goal: str = ""
    model: str = "claude"
    tech: list[str] = []
    conditions: list[str] = []
    full: bool = True
    max_iter: int = 50
    fix_attempts: int = 3              # max BugFixer invocations before STUCK
    hitl_after: list[str] = []         # capability names to gate on human review
    hitl_max_rejections: int = 5       # max HITL rejections before STUCK
    max_cost_usd: Optional[float] = None       # hard USD budget; engine stops if exceeded
    capability_models: dict[str, str] = {}    # per-capability model overrides
    output_dir: Optional[str] = None          # server-side absolute path for FilesystemStore
    source_dir: Optional[str] = None          # load existing .py files from this directory
    resume: bool = False                      # reload goal + artifacts from output_dir

    @field_validator("max_iter")
    @classmethod
    def max_iter_range(cls, v: int) -> int:
        if not (1 <= v <= 200):
            raise ValueError("max_iter must be between 1 and 200")
        return v

    @field_validator("fix_attempts")
    @classmethod
    def fix_attempts_range(cls, v: int) -> int:
        if not (0 <= v <= 20):
            raise ValueError("fix_attempts must be between 0 and 20")
        return v

    @field_validator("output_dir", "source_dir")
    @classmethod
    def absolute_path(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        p = Path(v.strip())
        if not p.is_absolute():
            raise ValueError("path must be absolute on the server")
        return str(p)


class EngineRunAccepted(BaseModel):
    status: str = "accepted"
    run_id: str
    team: str = "engine"
    goal: str
    hint: str = "Poll GET /runs/{run_id} or connect to WS /ws/events for real-time updates"


class EngineCapabilitiesResponse(BaseModel):
    capabilities: list[str]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/run",
    status_code=202,
    response_model=EngineRunAccepted,
    dependencies=[Depends(require_role("admin", "write"))],
)
async def trigger_engine_run(
    body: EngineRunRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> EngineRunAccepted:
    """Start a capability-driven engine run.

    The engine builds a software project from a natural-language goal.
    It decides which capabilities to invoke (SpecExtractor, Architect,
    TaskPlanner, CodeGenerator, TestGenerator, TestRunner, CodeReviewer)
    and in what order, without a fixed pipeline.

    Set `full: false` to stop after planning (requirements + architecture + tasks).
    Set `output_dir` to an absolute server path to persist artifacts to disk.
    Set `tech` to constrain the tech stack (e.g. `["Python", "FastAPI"]`).

    Returns 202 Accepted with the run_id. Use GET /runs/{run_id} to poll
    status or connect to WS /ws/events for real-time capability events.
    """
    from app.services.engine_runner import dispatch_engine

    output_path = Path(body.output_dir) if body.output_dir else None
    source_path = Path(body.source_dir) if body.source_dir else None

    if body.resume and output_path is None:
        raise HTTPException(422, "output_dir is required when resume=true")
    if source_path is not None and not source_path.is_dir():
        raise HTTPException(422, f"source_dir does not exist or is not a directory: {source_path}")

    try:
        run_id = await dispatch_engine(
            goal=body.goal,
            model=body.model,
            capability_models=body.capability_models or None,
            tech=body.tech,
            conditions=body.conditions,
            full=body.full,
            max_iter=body.max_iter,
            fix_attempts=body.fix_attempts,
            hitl_after=body.hitl_after,
            hitl_max_rejections=body.hitl_max_rejections,
            max_cost_usd=body.max_cost_usd,
            output_dir=output_path,
            source_dir=source_path,
            resume=body.resume,
            created_by=ctx.created_by,
            workspace_id=ctx.workspace_id,
        )
    except Exception as exc:
        raise HTTPException(422, str(exc))

    return EngineRunAccepted(run_id=run_id, goal=body.goal)


@router.post(
    "/run/{run_id}/cancel",
    status_code=200,
    dependencies=[Depends(require_role("admin", "write"))],
)
async def cancel_engine_run(run_id: str) -> dict:
    """Request cancellation of an in-flight engine run.

    The Operator checks the stop signal at the start of each iteration, so
    cancellation takes effect before the next capability dispatch (not mid-LLM-call).
    Returns 200 with found=True if the run was active, found=False if already done.
    """
    from app.services.engine_runner import cancel_engine_run as _cancel
    found = _cancel(run_id)
    return {"run_id": run_id, "found": found, "status": "cancellation_requested" if found else "not_found"}


@router.get("/capabilities", response_model=EngineCapabilitiesResponse)
async def list_engine_capabilities() -> EngineCapabilitiesResponse:
    """List the capabilities available in the engine registry."""
    return EngineCapabilitiesResponse(capabilities=AVAILABLE_ENGINE_CAPABILITIES)


@router.get("/runs/{run_id}/progress")
async def get_engine_progress(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Condition satisfaction progress for an engine run.

    Returns which goal conditions have been satisfied and which are still pending,
    plus the capability execution history (name, duration, cost, produced artifacts).

    This is the Layer 2 equivalent of checking agent statuses in a Layer 1 run:
    instead of "BA done / PM done / Dev done", you see "requirements_exists ✓ /
    architecture_exists ✓ / implementation_exists pending".

    Conditions come from run.state (written by _store_engine_state after the run).
    For in-flight runs, only conditions satisfied so far appear.
    """
    run = (await session.exec(select(Run).where(Run.run_id == run_id))).first()
    if not run:
        raise HTTPException(404, f"Run {run_id!r} not found")
    if run.team != "engine":
        raise HTTPException(422, f"Run {run_id!r} is not an engine run (team={run.team!r})")
    if not ws_accessible(run.workspace_id, ctx):
        raise HTTPException(403, "Not accessible with the current API key")

    state = run.state or {}
    satisfied = set(state.get("conditions_satisfied") or [])
    expected = list(state.get("conditions_expected") or [])

    conditions: dict[str, str] = {}
    for cond in expected:
        if cond in satisfied:
            conditions[cond] = "satisfied"
        elif run.status == "running":
            conditions[cond] = "pending"
        else:
            conditions[cond] = "not_reached"
    for cond in satisfied:
        if cond not in conditions:
            conditions[cond] = "satisfied"

    # Capability execution history from the event bus (agent.start / agent.end in DB).
    cap_events = (await session.exec(
        select(Event)
        .where(Event.run_id == run_id)
        .where(Event.event_type.in_(["agent.start", "agent.end"]))
        .order_by(Event.timestamp)
    )).all()

    capabilities = []
    in_flight: dict[str, float] = {}
    for ev in cap_events:
        p = ev.payload
        name = p.get("agent_name", "")
        if ev.event_type == "agent.start":
            in_flight[name] = ev.timestamp
        elif ev.event_type == "agent.end":
            in_flight.pop(name, None)
            capabilities.append({
                "name": name,
                "duration_s": p.get("duration_s"),
                "cost_usd": p.get("cost_usd"),
                "produced": p.get("produced_keys", []),
            })

    return {
        "run_id": run_id,
        "status": run.status,
        "goal": state.get("goal", run.request),
        "conditions": conditions,
        "total_conditions": len(conditions),
        "satisfied_count": len(satisfied),
        "capabilities_executed": capabilities,
    }
