"""Micro-feedback API — one endpoint to capture in-app feedback events."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.admin_auth import get_session_user
from app.core.database import get_session
from app.models.auth import User
from app.models.feedback import UserFeedback

router = APIRouter(prefix="/feedback", tags=["feedback"])

_VALID_CONTEXTS = frozenset({
    "run_complete", "ticket_closed", "run_failed", "general",
})


class FeedbackCreate(BaseModel):
    context: str = "general"
    helpful: Optional[bool] = None
    message: Optional[str] = Field(default=None, max_length=1000)
    workspace_id: Optional[int] = None


@router.post("", status_code=201)
async def submit_feedback(
    body: FeedbackCreate,
    user: User = Depends(get_session_user),
    session=Depends(get_session),
):
    """Record a micro-feedback event. Requires a valid session cookie."""
    if body.context not in _VALID_CONTEXTS:
        raise HTTPException(422, f"context must be one of {sorted(_VALID_CONTEXTS)}")

    fb = UserFeedback(
        user_id=user.id,
        workspace_id=body.workspace_id,
        context=body.context,
        helpful=body.helpful,
        message=body.message.strip() if body.message else None,
    )
    session.add(fb)
    await session.commit()
    return {"ok": True}
