"""HITL review resolution and listing."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, require_role, ws_accessible, ws_filter
from app.core.channel import resolve_review
from app.core.database import get_session
from app.models.run import HitlReview, Run
from app.services.runs import list_reviews as list_reviews_svc


# ---------------------------------------------------------------------------
# P3.1 — Normalized response model: exposes artifact/options as parsed types
# ---------------------------------------------------------------------------

class HitlReviewPublic(BaseModel):
    """HitlReview with artifact_json parsed to dict and options_json to list."""
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    review_id: str
    run_id: str
    agent_name: str
    artifact: dict = {}
    options: list[str] = ["approve", "reject"]
    status: str
    decision: Optional[str] = None
    edited_json: Optional[str] = None
    feedback: Optional[str] = None
    assigned_to: Optional[str] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, v: Any) -> dict:
        if hasattr(v, "__dict__"):
            d = {k: val for k, val in v.__dict__.items() if not k.startswith("_")}
        elif isinstance(v, dict):
            d = dict(v)
        else:
            return v
        try:
            d["artifact"] = json.loads(d.pop("artifact_json", None) or "null") or {}
        except Exception:
            d.pop("artifact_json", None)
            d["artifact"] = {}
        try:
            d["options"] = json.loads(d.pop("options_json", None) or '["approve","reject"]')
        except Exception:
            d.pop("options_json", None)
            d["options"] = ["approve", "reject"]
        return d

router = APIRouter(
    prefix="/reviews",
    tags=["hitl"],
    dependencies=[Depends(require_api_key)],
)

_VALID_DECISIONS = ("approve", "reject", "edit", "feedback")
_VALID_STATUSES = ("pending", "approved", "rejected", "edited", "feedback", "cancelled", "timeout")

# Map decision verb → stored status noun
_DECISION_TO_STATUS = {
    "approve": "approved",
    "reject": "rejected",
    "edit": "edited",
    "feedback": "feedback",
}


class ReviewDecision(BaseModel):
    decision: str
    edited: Optional[str] = None
    feedback: Optional[str] = None


class ReviewAssign(BaseModel):
    assigned_to: str  # reviewer name / email / identifier


class CreateReview(BaseModel):
    run_id: str
    review_id: Optional[str] = None     # generated if omitted
    agent_name: str
    artifact_json: str = "null"
    options: list[str] = ["approve", "edit", "reject"]
    workspace_id: Optional[int] = None  # used when creating a stub Run for local runs


@router.post("/", status_code=201, response_model=HitlReviewPublic,
             dependencies=[Depends(require_role("admin", "write"))])
async def create_review(
    body: CreateReview,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Register a HITL review created by an external (local) run.

    Called by antcrew.integrations.PlatformChannel when a local pipeline run
    reaches a HITL checkpoint. Creates the HitlReview row and fires the same
    notification path (Slack, webhook) as platform-dispatched runs.

    If the run_id does not correspond to a known Run row, a stub Run with
    status='external' is created so the workspace listener can resolve Slack
    tokens and the run appears in the dashboard.

    The review_id is generated if not supplied. The caller should then poll
    GET /reviews/{review_id} until status != 'pending' to receive the decision.
    """
    review_id = body.review_id or str(uuid.uuid4())

    existing = (await session.exec(
        select(HitlReview).where(HitlReview.review_id == review_id)
    )).first()
    if existing:
        return existing

    # Ensure the run_id has a corresponding Run row so workspace listeners work.
    run_exists = (await session.exec(
        select(Run).where(Run.run_id == body.run_id)
    )).first()
    if not run_exists:
        workspace_id = body.workspace_id or ctx.workspace_id
        stub = Run(
            run_id=body.run_id,
            thread_id="external",
            team="external",
            request=f"External HITL review ({body.agent_name})",
            status="external",
            workspace_id=workspace_id,
            created_by=ctx.created_by,
        )
        session.add(stub)
        await session.flush()  # write stub before the review FK

    review = HitlReview(
        review_id=review_id,
        run_id=body.run_id,
        agent_name=body.agent_name,
        artifact_json=body.artifact_json,
        options_json=json.dumps(body.options),
        status="pending",
    )
    session.add(review)
    await session.commit()
    await session.refresh(review)

    # Fire the bus event so the existing listener handles Slack/webhook notifications.
    # The listener is idempotent — it skips HitlReview creation when the row exists.
    try:
        from antcrew.core.events import bus as _bus
        artifact_data: object = {}
        try:
            artifact_data = json.loads(body.artifact_json)
        except Exception:
            pass
        _bus.emit(
            "hitl.review_required",
            {
                "review_id": review_id,
                "agent_name": body.agent_name,
                "options": body.options,
                "artifact": artifact_data,
            },
            run_id=body.run_id,
            thread_id="external",
        )
    except Exception:
        pass  # notification failure must not fail the endpoint

    return review


@router.get("/{review_id}", response_model=HitlReviewPublic)
async def get_review(
    review_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Get a specific HITL review by ID.

    Reviewers can use this to inspect the full artifact before resolving.
    Returns 403 when the review belongs to a different workspace than the API key.
    """
    result = await session.exec(select(HitlReview).where(HitlReview.review_id == review_id))
    review = result.first()
    if not review:
        raise HTTPException(404, f"Review {review_id!r} not found")
    if ctx.workspace_ids is not None:
        run_result = await session.exec(select(Run).where(Run.run_id == review.run_id))
        run = run_result.first()
        if run and not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This review is not accessible with the current API key")
    return review


@router.get("/", response_model=list[HitlReviewPublic])
async def list_reviews(
    status: str = Query("pending", description=f"Filter by status. One of: {_VALID_STATUSES}"),
    run_id: Optional[str] = None,
    assigned_to: Optional[str] = Query(None, description="Filter by assignee name"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """List HITL reviews. Default: pending reviews. Scoped to key's workspace when set."""
    if status not in _VALID_STATUSES:
        raise HTTPException(422, f"status must be one of {_VALID_STATUSES}")
    reviews = await list_reviews_svc(
        session, status=status, run_id=run_id, limit=limit, offset=offset,
        workspace_ids=ctx.workspace_ids,
    )
    if assigned_to is not None:
        reviews = [r for r in reviews if r.assigned_to == assigned_to]
    return reviews


@router.patch("/{review_id}/assign", response_model=HitlReviewPublic,
              dependencies=[Depends(require_role("admin", "write", "reviewer"))])
async def assign_review(
    review_id: str,
    body: ReviewAssign,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Assign a pending review to a named reviewer.

    Use this to claim ownership before resolving, so other reviewers know
    someone is already handling it. The pipeline is not unblocked by this call.
    """
    result = await session.exec(select(HitlReview).where(HitlReview.review_id == review_id))
    review = result.first()
    if not review:
        raise HTTPException(404, f"Review {review_id!r} not found")
    if review.status != "pending":
        raise HTTPException(409, f"Review {review_id!r} already resolved (status: {review.status!r})")
    if ctx.workspace_ids is not None:
        run_result = await session.exec(select(Run).where(Run.run_id == review.run_id))
        run = run_result.first()
        if run and not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This review is not accessible with the current API key")

    review.assigned_to = body.assigned_to.strip() or None
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return review


@router.post("/{review_id}", response_model=HitlReviewPublic,
             dependencies=[Depends(require_role("admin", "write", "reviewer"))])
async def submit_review(
    review_id: str,
    body: ReviewDecision,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Submit a HITL review decision.

    The run's executor thread is unblocked immediately; LangGraph continues from
    the checkpoint using the provided decision.
    """
    if body.decision not in _VALID_DECISIONS:
        raise HTTPException(422, f"decision must be one of {_VALID_DECISIONS}")

    result = await session.exec(
        select(HitlReview).where(HitlReview.review_id == review_id)
    )
    review = result.first()
    if not review:
        raise HTTPException(404, f"Review {review_id!r} not found")
    if review.status != "pending":
        raise HTTPException(409, f"Review {review_id!r} already resolved (status: {review.status!r})")

    # Verify the requesting key has access to the run this review belongs to
    if ctx.workspace_ids is not None:
        run_result = await session.exec(select(Run).where(Run.run_id == review.run_id))
        run = run_result.first()
        if run and not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This review is not accessible with the current API key")

    decision_payload = {
        "decision": body.decision,
        "edited": body.edited,
        "feedback": body.feedback,
    }

    # Resolve the Future — unblocks the executor thread
    resolve_review(review_id, decision_payload)

    review.status = _DECISION_TO_STATUS.get(body.decision, body.decision)
    review.decision = body.decision
    review.edited_json = body.edited
    review.feedback = body.feedback
    review.resolved_at = datetime.now(timezone.utc)
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return review
