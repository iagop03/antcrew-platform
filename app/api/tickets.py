"""REST endpoints for the ticket kanban."""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.exceptions import TicketNotFoundError
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, ws_accessible, require_role
from app.core.database import get_session
from app.models.run import Run, Ticket, Workspace
from app.services.runs import list_tickets, _claim_display_id

router = APIRouter(prefix="/tickets", tags=["tickets"], dependencies=[Depends(require_api_key)])

_VALID_STATUSES = ("open", "in_progress", "done", "blocked")
_VALID_TICKET_TYPES = ("task", "manual_action", "bug")


class StatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {_VALID_STATUSES}")
        return v


class ManualActionCreate(BaseModel):
    """Body for POST /tickets/ — create a manual-action blocking ticket."""
    run_id: str
    title: str
    description: str = ""
    assignee: Optional[str] = None
    priority: str = "high"


@router.post(
    "/",
    status_code=201,
    response_model=Ticket,
    dependencies=[Depends(require_role("admin", "write"))],
)
async def create_manual_action(
    body: ManualActionCreate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Ticket:
    """Create a manual-action blocking ticket for any run.

    The associated run is immediately set to ``blocked``. The run resumes
    (status → ``running``) when the ticket is resolved via PATCH /tickets/{id}/status
    with ``status="done"``.

    Use this endpoint when an external agent or the platform itself determines
    that a human step is needed outside of an engine capability graph.
    """
    # Verify the run is accessible
    run_result = await session.exec(select(Run).where(Run.run_id == body.run_id))
    run = run_result.first()
    if run is None:
        raise HTTPException(404, f"Run {body.run_id!r} not found")
    if ctx.workspace_ids is not None and not ws_accessible(run.workspace_id, ctx):
        raise HTTPException(403, "Run is not accessible with the current API key")

    workspace_id = run.workspace_id
    display_id: Optional[str] = None
    if workspace_id is not None:
        display_id = await _claim_display_id(session, workspace_id)

    ticket_id = str(uuid.uuid4())
    ticket = Ticket(
        ticket_id=ticket_id,
        run_id=body.run_id,
        title=body.title,
        description=body.description,
        ticket_type="manual_action",
        blocking=True,
        assignee=body.assignee,
        priority=body.priority,
        status="open",
        workspace_id=workspace_id,
        display_id=display_id,
    )
    session.add(ticket)

    # Block the run
    if run.status not in ("error", "success", "cancelled"):
        run.status = "blocked"
        session.add(run)

    await session.commit()
    await session.refresh(ticket)
    return ticket


@router.get("/export-targets")
async def export_targets() -> dict:
    """Return which export integrations are currently configured."""
    from app.services.export import available_targets
    return {"targets": available_targets()}


@router.get("/", response_model=list[Ticket])
async def index(
    status: Optional[str] = None,
    search: Optional[str] = Query(None, description="Filter by title, description, or PRD"),
    run_id: Optional[str] = Query(None, description="Filter tickets by run ID"),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    return await list_tickets(
        session,
        status=status,
        search=search,
        run_id=run_id,
        limit=limit,
        offset=offset,
        workspace_ids=ctx.workspace_ids,
    )


@router.patch("/{ticket_id}/status", response_model=Ticket)
async def update_status(
    ticket_id: str,
    body: StatusUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    result = await session.exec(select(Ticket).where(Ticket.ticket_id == ticket_id))
    ticket = result.first()
    if not ticket:
        raise TicketNotFoundError(ticket_id)

    run = None
    if ctx.workspace_ids is not None:
        run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
        run = run_result.first()
        if run is None or not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This ticket is not accessible with the current API key")

    ticket.status = body.status
    ticket.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(ticket)

    # Unblock the run when the last blocking ticket is resolved
    if ticket.blocking and body.status == "done":
        if run is None:
            run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
            run = run_result.first()

        if run and run.status == "blocked":
            # Check if there are other open blocking tickets for this run
            other_blocking = (await session.exec(
                select(Ticket).where(
                    Ticket.run_id == ticket.run_id,
                    Ticket.blocking == True,  # noqa: E712
                    Ticket.ticket_id != ticket_id,
                    Ticket.status != "done",
                )
            )).first()
            if not other_blocking:
                run.status = "running"
                session.add(run)

        # Wake up the engine thread if it's blocking on this ticket
        from app.services.engine_runner import resolve_manual_action
        resolve_manual_action(ticket_id)

    await session.commit()
    await session.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/export")
async def export_ticket(
    ticket_id: str,
    target: str = Query(..., description="Export target: 'jira' or 'linear'"),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Export a ticket to an external project management tool."""
    result = await session.exec(select(Ticket).where(Ticket.ticket_id == ticket_id))
    ticket = result.first()
    if not ticket:
        raise TicketNotFoundError(ticket_id)
    if ctx.workspace_ids is not None:
        from app.models.run import Run
        run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
        run = run_result.first()
        if run is None or not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This ticket is not accessible with the current API key")

    from app.services.export import export_to_jira, export_to_linear
    try:
        if target == "jira":
            url = await export_to_jira(ticket)
        elif target == "linear":
            url = await export_to_linear(ticket)
        else:
            raise HTTPException(422, f"Unknown target {target!r}. Use 'jira' or 'linear'.")
        return {"url": url, "target": target, "ticket_id": ticket_id}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Export failed: {exc}")


def _parse_github_repo(url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub URL or SSH remote. Returns None if not parseable."""
    url = url.strip().removesuffix(".git")
    # HTTPS: https://github.com/owner/repo
    m = re.match(r"https?://(?:www\.)?github\.com/([^/]+/[^/]+)", url)
    if m:
        return m.group(1)
    # SSH: git@github.com:owner/repo
    m = re.match(r"git@github\.com:([^/]+/[^/]+)", url)
    if m:
        return m.group(1)
    return None


@router.get("/{ticket_id}/commits")
async def get_linked_commits(
    ticket_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Search GitHub for commits and PRs mentioning this ticket's display_id.

    Requires GITHUB_TOKEN environment variable and a repo URL on the workspace
    (default_repo_url). Returns up to 10 commits and 5 PRs.
    """
    result = await session.exec(select(Ticket).where(Ticket.ticket_id == ticket_id))
    ticket = result.first()
    if not ticket:
        raise TicketNotFoundError(ticket_id)
    if ctx.workspace_ids is not None:
        run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
        run = run_result.first()
        if run is None or not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This ticket is not accessible with the current API key")

    if not ticket.display_id:
        return {"display_id": None, "commits": [], "pull_requests": [], "error": "no_display_id"}

    # Resolve workspace for repo URL
    ws_id = ticket.workspace_id
    if ws_id is None:
        run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
        run = run_result.first()
        ws_id = run.workspace_id if run else None

    repo_slug: str | None = None
    if ws_id is not None:
        ws_result = await session.exec(select(Workspace).where(Workspace.id == ws_id))
        ws = ws_result.first()
        if ws and ws.default_repo_url:
            repo_slug = _parse_github_repo(ws.default_repo_url)

    if not repo_slug:
        return {"display_id": ticket.display_id, "commits": [], "pull_requests": [], "error": "no_repo_configured"}

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"display_id": ticket.display_id, "commits": [], "pull_requests": [], "error": "no_github_token"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    query = f"{ticket.display_id} repo:{repo_slug}"

    async with httpx.AsyncClient(timeout=10) as client:
        commits_resp, prs_resp = await asyncio.gather(
            client.get("https://api.github.com/search/commits", params={"q": query, "per_page": 10}, headers=headers),
            client.get("https://api.github.com/search/issues", params={"q": f"{query} type:pr", "per_page": 5}, headers=headers),
        )

    commits = []
    if commits_resp.status_code == 200:
        for item in commits_resp.json().get("items", []):
            commits.append({
                "sha": item["sha"][:7],
                "message": item["commit"]["message"].split("\n")[0],
                "author": item["commit"]["author"]["name"],
                "date": item["commit"]["author"]["date"],
                "url": item["html_url"],
            })

    pull_requests = []
    if prs_resp.status_code == 200:
        for item in prs_resp.json().get("items", []):
            pull_requests.append({
                "number": item["number"],
                "title": item["title"],
                "state": item["state"],
                "url": item["html_url"],
                "created_at": item["created_at"],
            })

    return {"display_id": ticket.display_id, "commits": commits, "pull_requests": pull_requests}
