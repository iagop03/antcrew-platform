"""User feedback model — micro-feedback events from the platform UI."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models._utils import _utcnow


class UserFeedback(SQLModel, table=True):
    """A single micro-feedback event submitted by a user from any page.

    context identifies where in the product the feedback was triggered:
      run_complete | ticket_closed | run_failed | general
    helpful is a simple thumbs up (True) / thumbs down (False); NULL = no rating.
    message is optional free text (max ~1000 chars enforced at API layer).
    """

    __tablename__ = "user_feedback"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True)
    workspace_id: Optional[int] = Field(default=None, index=True)
    context: str
    helpful: Optional[bool] = Field(default=None)
    message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
