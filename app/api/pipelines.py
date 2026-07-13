"""CRUD for user-defined visual pipeline definitions.

Static built-in templates are returned in-memory (no DB row needed).
User pipelines are stored in the pipeline_def table.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, require_role
from app.core.database import get_session
from app.models.run import PipelineDef

router = APIRouter(
    prefix="/pipelines",
    tags=["pipelines"],
    dependencies=[Depends(require_api_key)],
)

# ---------------------------------------------------------------------------
# Static templates (read-only, never stored in DB)
# ---------------------------------------------------------------------------

def _t(d: int) -> str:
    return f"template:{d}"


_TEMPLATES: list[dict] = [
    {
        "id": "template:fullstack",
        "name": "Full Stack Team",
        "description": "Pipeline completo de desarrollo: análisis → PM → sprint → dev → QA → review → deploy → docs.",
        "is_template": True,
        "workspace_id": None,
        "created_at": None,
        "definition": {
            "nodes": [
                {"id": "codebase_scanner",  "type": "codebase_scanner",  "label": "Codebase Scanner",  "model": "claude", "x": 30,  "y": 30},
                {"id": "business_analyst",  "type": "business_analyst",  "label": "Business Analyst",  "model": "claude", "x": 250, "y": 30},
                {"id": "pm",                "type": "pm",                "label": "Product Manager",   "model": "claude", "x": 470, "y": 30},
                {"id": "sprint_planner",    "type": "sprint_planner",    "label": "Sprint Planner",    "model": "claude", "x": 690, "y": 30},
                {"id": "backend_dev",       "type": "backend_dev",       "label": "Backend Dev",       "model": "claude", "x": 690, "y": 160},
                {"id": "frontend_dev",      "type": "frontend_dev",      "label": "Frontend Dev",      "model": "claude", "x": 690, "y": 290},
                {"id": "qa",                "type": "qa",                "label": "QA Engineer",       "model": "claude", "x": 470, "y": 290},
                {"id": "reviewer",          "type": "reviewer",          "label": "Code Reviewer",     "model": "claude", "x": 250, "y": 290},
                {"id": "devops",            "type": "devops",            "label": "DevOps",            "model": "claude", "x": 30,  "y": 290},
                {"id": "doc_writer",        "type": "doc_writer",        "label": "Doc Writer",        "model": "claude", "x": 30,  "y": 160},
            ],
            "edges": [
                {"from": "codebase_scanner", "to": "business_analyst",  "condition": None},
                {"from": "business_analyst",  "to": "pm",               "condition": None},
                {"from": "pm",                "to": "sprint_planner",   "condition": None},
                {"from": "sprint_planner",    "to": "backend_dev",      "condition": None},
                {"from": "backend_dev",       "to": "frontend_dev",     "condition": None},
                {"from": "frontend_dev",      "to": "qa",               "condition": None},
                {"from": "qa",                "to": "reviewer",         "condition": None},
                {"from": "reviewer",          "to": "backend_dev",      "condition": "reviewer_fix_requested"},
                {"from": "reviewer",          "to": "sprint_planner",   "condition": "sprint_in_progress"},
                {"from": "reviewer",          "to": "devops",           "condition": "sprint_complete"},
                {"from": "devops",            "to": "doc_writer",       "condition": None},
            ],
        },
    },
    {
        "id": "template:dev",
        "name": "Dev Team",
        "description": "Pipeline básico: Business Analyst → PM → Backend Dev.",
        "is_template": True,
        "workspace_id": None,
        "created_at": None,
        "definition": {
            "nodes": [
                {"id": "business_analyst", "type": "business_analyst", "label": "Business Analyst", "model": "claude", "x": 50,  "y": 100},
                {"id": "pm",               "type": "pm",               "label": "Product Manager",  "model": "claude", "x": 280, "y": 100},
                {"id": "backend_dev",      "type": "backend_dev",      "label": "Backend Dev",      "model": "claude", "x": 510, "y": 100},
            ],
            "edges": [
                {"from": "business_analyst", "to": "pm",          "condition": None},
                {"from": "pm",               "to": "backend_dev", "condition": None},
            ],
        },
    },
    {
        "id": "template:content",
        "name": "Content Team",
        "description": "Pipeline de contenido: Idea → Copywriter → Editor.",
        "is_template": True,
        "workspace_id": None,
        "created_at": None,
        "definition": {
            "nodes": [
                {"id": "idea",       "type": "idea",       "label": "Idea Generator", "model": "claude", "x": 50,  "y": 100},
                {"id": "copywriter", "type": "copywriter", "label": "Copywriter",     "model": "claude", "x": 280, "y": 100},
                {"id": "editor",     "type": "editor",     "label": "Editor",         "model": "claude", "x": 510, "y": 100},
            ],
            "edges": [
                {"from": "idea",       "to": "copywriter", "condition": None},
                {"from": "copywriter", "to": "editor",     "condition": None},
            ],
        },
    },
    {
        "id": "template:research",
        "name": "Research Team",
        "description": "Pipeline de investigación: Researcher → Copywriter.",
        "is_template": True,
        "workspace_id": None,
        "created_at": None,
        "definition": {
            "nodes": [
                {"id": "researcher", "type": "researcher", "label": "Researcher",  "model": "claude", "x": 50,  "y": 100},
                {"id": "copywriter", "type": "copywriter", "label": "Copywriter",  "model": "claude", "x": 280, "y": 100},
            ],
            "edges": [
                {"from": "researcher", "to": "copywriter", "condition": None},
            ],
        },
    },
]

# All available agent types for the palette (from AGENT_REGISTRY)
_AGENT_PALETTE = [
    {"type": "business_analyst", "label": "Business Analyst",  "color": "#7c3aed"},
    {"type": "pm",               "label": "Product Manager",   "color": "#7c3aed"},
    {"type": "sprint_planner",   "label": "Sprint Planner",    "color": "#2563eb"},
    {"type": "backend_dev",      "label": "Backend Dev",       "color": "#0891b2"},
    {"type": "frontend_dev",     "label": "Frontend Dev",      "color": "#0891b2"},
    {"type": "qa",               "label": "QA Engineer",       "color": "#059669"},
    {"type": "reviewer",         "label": "Code Reviewer",     "color": "#d97706"},
    {"type": "devops",           "label": "DevOps",            "color": "#dc2626"},
    {"type": "doc_writer",       "label": "Doc Writer",        "color": "#6b7280"},
    {"type": "researcher",       "label": "Researcher",        "color": "#7c3aed"},
    {"type": "idea",             "label": "Idea Generator",    "color": "#ec4899"},
    {"type": "copywriter",       "label": "Copywriter",        "color": "#ec4899"},
    {"type": "editor",           "label": "Editor",            "color": "#ec4899"},
    {"type": "codebase_scanner", "label": "Codebase Scanner",  "color": "#6b7280"},
    {"type": "feature",          "label": "Feature Agent",     "color": "#0891b2"},
]


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class PipelineOut(BaseModel):
    id: str | int
    name: str
    description: Optional[str]
    is_template: bool
    workspace_id: Optional[int]
    created_at: Optional[datetime]
    definition: dict

    model_config = {"from_attributes": True}


class PipelineCreate(BaseModel):
    name: str
    description: Optional[str] = None
    workspace_id: int
    definition: dict


class PipelineUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    definition: Optional[dict] = None


def _row_to_out(row: PipelineDef) -> PipelineOut:
    return PipelineOut(
        id=row.id,
        name=row.name,
        description=row.description,
        is_template=row.is_template,
        workspace_id=row.workspace_id,
        created_at=row.created_at,
        definition=json.loads(row.definition),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/agents")
async def list_agent_types() -> list[dict]:
    """Return the agent palette used to populate the node picker."""
    return _AGENT_PALETTE


@router.get("/", response_model=list[PipelineOut])
async def list_pipelines(
    workspace_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
) -> list[PipelineOut]:
    """Return static templates + user pipelines for the given workspace."""
    results: list[PipelineOut] = [PipelineOut(**t) for t in _TEMPLATES]

    q = select(PipelineDef)
    if workspace_id is not None:
        q = q.where(PipelineDef.workspace_id == workspace_id)
    rows = (await session.exec(q)).all()
    results += [_row_to_out(r) for r in rows]
    return results


@router.get("/{pipeline_id}", response_model=PipelineOut)
async def get_pipeline(
    pipeline_id: str,
    session: AsyncSession = Depends(get_session),
) -> PipelineOut:
    if isinstance(pipeline_id, str) and pipeline_id.startswith("template:"):
        hit = next((t for t in _TEMPLATES if t["id"] == pipeline_id), None)
        if not hit:
            raise HTTPException(404, "Template not found")
        return PipelineOut(**hit)
    try:
        pid = int(pipeline_id)
    except ValueError:
        raise HTTPException(400, "Invalid pipeline id")
    row = await session.get(PipelineDef, pid)
    if not row:
        raise HTTPException(404, "Pipeline not found")
    return _row_to_out(row)


@router.post("/", response_model=PipelineOut, status_code=201,
             dependencies=[Depends(require_role("admin"))])
async def create_pipeline(
    body: PipelineCreate,
    session: AsyncSession = Depends(get_session),
) -> PipelineOut:
    row = PipelineDef(
        workspace_id=body.workspace_id,
        name=body.name,
        description=body.description,
        is_template=False,
        definition=json.dumps(body.definition),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _row_to_out(row)


@router.patch("/{pipeline_id}", response_model=PipelineOut,
              dependencies=[Depends(require_role("admin"))])
async def update_pipeline(
    pipeline_id: int,
    body: PipelineUpdate,
    session: AsyncSession = Depends(get_session),
) -> PipelineOut:
    row = await session.get(PipelineDef, pipeline_id)
    if not row:
        raise HTTPException(404, "Pipeline not found")
    if row.is_template:
        raise HTTPException(400, "Cannot modify a built-in template")
    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    if body.definition is not None:
        row.definition = json.dumps(body.definition)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _row_to_out(row)


@router.delete("/{pipeline_id}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def delete_pipeline(
    pipeline_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(PipelineDef, pipeline_id)
    if not row:
        raise HTTPException(404, "Pipeline not found")
    if row.is_template:
        raise HTTPException(400, "Cannot delete a built-in template")
    await session.delete(row)
    await session.commit()
