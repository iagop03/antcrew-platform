"""HITL review resolution and listing."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, model_validator
from sqlmodel import select, col, desc
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, require_role, ws_accessible, ws_filter
from app.core.channel import resolve_review
from app.core.database import get_session
from app.models.run import HitlReview, HitlReviewAssignee, HitlAuditEntry, ApiKey, Run
from app.services.runs import list_reviews as list_reviews_svc


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

class HitlReviewPublic(BaseModel):
    """HitlReview with artifact/options parsed and assignees enriched."""
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
    assignees: list[str] = []
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
        if "assignees" not in d:
            d["assignees"] = []
        return d


async def _to_public(
    session: AsyncSession,
    reviews: list[HitlReview],
) -> list[HitlReviewPublic]:
    """Convert HitlReview list to public models, batch-loading assignees in one query."""
    if not reviews:
        return []
    review_ids = [r.review_id for r in reviews]
    assignee_rows = (await session.exec(
        select(HitlReviewAssignee).where(
            col(HitlReviewAssignee.review_id).in_(review_ids)
        )
    )).all()
    assignees_map: dict[str, list[str]] = {}
    for row in assignee_rows:
        assignees_map.setdefault(row.review_id, []).append(row.assignee_label)

    result: list[HitlReviewPublic] = []
    for r in reviews:
        d = {k: v for k, v in r.__dict__.items() if not k.startswith("_")}
        d["assignees"] = assignees_map.get(r.review_id, [])
        result.append(HitlReviewPublic.model_validate(d))
    return result


class AuditEntryPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: Optional[int] = None
    review_id: str
    actor_label: Optional[str] = None
    action: str
    note: Optional[str] = None
    created_at: Optional[datetime] = None


router = APIRouter(
    prefix="/reviews",
    tags=["hitl"],
    dependencies=[Depends(require_api_key)],
)

_VALID_DECISIONS = ("approve", "reject", "edit", "feedback")
_VALID_STATUSES = ("pending", "approved", "rejected", "edited", "feedback", "cancelled", "timeout")

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
    assigned_to: str


class CreateReview(BaseModel):
    run_id: str
    review_id: Optional[str] = None
    agent_name: str
    artifact_json: str = "null"
    options: list[str] = ["approve", "edit", "reject"]
    workspace_id: Optional[int] = None
    assignees: list[str] = []   # API key labels to assign; any one can resolve


@router.post("/", status_code=201, response_model=HitlReviewPublic,
             dependencies=[Depends(require_role("admin", "write"))])
async def create_review(
    body: CreateReview,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Register a HITL review created by an external (local) run.

    Pass ``assignees`` as a list of API key labels to notify specific reviewers.
    Any one of the assignees can resolve the review.
    """
    review_id = body.review_id or str(uuid.uuid4())

    existing = (await session.exec(
        select(HitlReview).where(HitlReview.review_id == review_id)
    )).first()
    if existing:
        return (await _to_public(session, [existing]))[0]

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
        await session.flush()

    # First assignee becomes the legacy assigned_to field for backward compat
    primary = body.assignees[0] if body.assignees else None
    review = HitlReview(
        review_id=review_id,
        run_id=body.run_id,
        agent_name=body.agent_name,
        artifact_json=body.artifact_json,
        options_json=json.dumps(body.options),
        status="pending",
        assigned_to=primary,
    )
    session.add(review)
    await session.flush()

    for label in body.assignees:
        session.add(HitlReviewAssignee(
            review_id=review_id,
            assignee_label=label,
        ))

    session.add(HitlAuditEntry(
        review_id=review_id,
        actor_label=ctx.created_by,
        action="created",
        note=f"assignees={body.assignees}" if body.assignees else None,
    ))

    await session.commit()
    await session.refresh(review)

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
                "assignees": body.assignees,
            },
            run_id=body.run_id,
            thread_id="external",
        )
    except Exception:
        pass

    # Email assignees who have an email address — via BackgroundTasks (safe, tracked by FastAPI)
    if body.assignees:
        try:
            from app.services.email import send_review_assigned as _send_email
            key_rows = (await session.exec(
                select(ApiKey).where(
                    col(ApiKey.label).in_(body.assignees),
                    ApiKey.email != None,  # noqa: E711
                )
            )).all()
            for k in key_rows:
                if k.email:
                    background_tasks.add_task(
                        _send_email,
                        to_email=k.email,
                        assignee_label=k.label,
                        review_id=review_id,
                        agent_name=body.agent_name,
                        run_id=body.run_id,
                    )
        except Exception:
            pass

    return (await _to_public(session, [review]))[0]


@router.get("/{review_id}", response_model=HitlReviewPublic)
async def get_review(
    review_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Get a specific HITL review by ID, including its full assignee list."""
    result = await session.exec(select(HitlReview).where(HitlReview.review_id == review_id))
    review = result.first()
    if not review:
        raise HTTPException(404, f"Review {review_id!r} not found")
    if ctx.workspace_ids is not None:
        run_result = await session.exec(select(Run).where(Run.run_id == review.run_id))
        run = run_result.first()
        if run and not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This review is not accessible with the current API key")
    return (await _to_public(session, [review]))[0]


@router.get("/", response_model=list[HitlReviewPublic])
async def list_reviews(
    status: str = Query("pending", description=f"Filter by status. One of: {_VALID_STATUSES}"),
    run_id: Optional[str] = None,
    assigned_to: Optional[str] = Query(None, description="Filter by legacy single-assignee field"),
    mine: bool = Query(False, description="Return only reviews assigned to the calling key"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """List HITL reviews.

    Use ``mine=true`` to see only reviews where the authenticated API key is an assignee.
    Reviewers see their own queue; admins/writers see all.
    """
    if status not in _VALID_STATUSES:
        raise HTTPException(422, f"status must be one of {_VALID_STATUSES}")

    # Resolve assignee_label for 'mine' filter
    assignee_label: Optional[str] = None
    if mine and ctx.created_by:
        assignee_label = ctx.created_by

    reviews = await list_reviews_svc(
        session,
        status=status,
        run_id=run_id,
        limit=limit,
        offset=offset,
        workspace_ids=ctx.workspace_ids,
        assignee_label=assignee_label,
    )
    # Legacy single-field filter (backward compat)
    if assigned_to is not None:
        reviews = [r for r in reviews if r.assigned_to == assigned_to]

    return await _to_public(session, reviews)


@router.get("/{review_id}/audit", response_model=list[AuditEntryPublic])
async def get_review_audit(
    review_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Return the immutable audit trail for a HITL review (created, assigned, approved, etc.)."""
    review = (await session.exec(
        select(HitlReview).where(HitlReview.review_id == review_id)
    )).first()
    if not review:
        raise HTTPException(404, f"Review {review_id!r} not found")
    if ctx.workspace_ids is not None:
        run = (await session.exec(select(Run).where(Run.run_id == review.run_id))).first()
        if run and not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This review is not accessible with the current API key")
    entries = (await session.exec(
        select(HitlAuditEntry)
        .where(HitlAuditEntry.review_id == review_id)
        .order_by(HitlAuditEntry.created_at)
    )).all()
    return [AuditEntryPublic.model_validate(e) for e in entries]


@router.patch("/{review_id}/assign", response_model=HitlReviewPublic,
              dependencies=[Depends(require_role("admin", "write", "reviewer"))])
async def assign_review(
    review_id: str,
    body: ReviewAssign,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Assign (or reassign) a pending review to a named reviewer.

    Also adds the assignee to the multi-reviewer table if not already present.
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

    label = body.assigned_to.strip() or None
    review.assigned_to = label
    session.add(review)

    # Ensure the assignee has a row in hitl_review_assignee
    if label:
        existing_row = (await session.exec(
            select(HitlReviewAssignee)
            .where(HitlReviewAssignee.review_id == review_id)
            .where(HitlReviewAssignee.assignee_label == label)
        )).first()
        if not existing_row:
            session.add(HitlReviewAssignee(review_id=review_id, assignee_label=label))

    session.add(HitlAuditEntry(
        review_id=review_id,
        actor_label=ctx.created_by,
        action="assigned",
        note=label,
    ))

    await session.commit()
    await session.refresh(review)
    return (await _to_public(session, [review]))[0]


@router.post("/{review_id}", response_model=HitlReviewPublic,
             dependencies=[Depends(require_role("admin", "write", "reviewer"))])
async def submit_review(
    review_id: str,
    body: ReviewDecision,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Submit a HITL review decision.

    The run's executor thread is unblocked immediately.  Any assignee (or any key
    with write/admin role) can resolve a review.
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

    resolve_review(review_id, decision_payload)

    try:
        from app.services.engine_runner import resolve_engine_review
        new_content = None
        if body.decision == "edit" and body.edited:
            try:
                new_content = json.loads(body.edited)
            except Exception:
                new_content = body.edited
        resolve_engine_review(review_id, body.decision, body.feedback, new_content)
    except Exception:
        pass

    review.status = _DECISION_TO_STATUS.get(body.decision, body.decision)
    review.decision = body.decision
    review.edited_json = body.edited
    review.feedback = body.feedback
    review.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    # Record who resolved it in assigned_to if not already set
    if not review.assigned_to and ctx.created_by:
        review.assigned_to = ctx.created_by
    session.add(review)

    audit_action = {"approve": "approved", "reject": "rejected"}.get(body.decision, body.decision)
    session.add(HitlAuditEntry(
        review_id=review_id,
        actor_label=ctx.created_by,
        action=audit_action,
        note=body.feedback,
    ))

    await session.commit()
    await session.refresh(review)
    return (await _to_public(session, [review]))[0]
