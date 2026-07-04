"""POST /run — trigger a pipeline run from the REST API."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.core.auth import require_api_key
from app.services.runner import dispatch, AVAILABLE_TEAMS

router = APIRouter(prefix="/run", tags=["pipeline"], dependencies=[Depends(require_api_key)])


class RunRequest(BaseModel):
    team: str
    request: str
    thread_id: str = "default"

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


class RunAccepted(BaseModel):
    status: str = "accepted"
    run_id: Optional[str]
    team: str
    hint: str = "Poll GET /runs or connect to WS /ws/events for real-time updates"


@router.post("/", status_code=202, response_model=RunAccepted)
async def trigger_run(body: RunRequest):
    """Start a pipeline run asynchronously.

    Returns 202 Accepted with the run_id once the pipeline emits its first
    event. The run continues in background; poll GET /runs/:run_id for status
    or stream WS /ws/events?run_id=<run_id> for real-time events.
    """
    try:
        run_id = await dispatch(body.team, body.request, body.thread_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    return RunAccepted(run_id=run_id, team=body.team)


@router.get("/teams")
async def list_teams():
    """List teams that can be triggered via POST /run."""
    return {"teams": AVAILABLE_TEAMS}
