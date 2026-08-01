"""Human-in-the-Loop review models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models._utils import _utcnow


class HitlReview(SQLModel, table=True):
    """A pending or resolved Human-in-the-Loop review request."""

    __tablename__ = "hitl_review"

    id: Optional[int] = Field(default=None, primary_key=True)
    review_id: str = Field(index=True, unique=True)  # UUID from PlatformChannel
    client_token: Optional[str] = Field(default=None, index=True, unique=True)      # legacy plaintext — NULL for new reviews
    client_token_hash: Optional[str] = Field(default=None, index=True, unique=True)  # sha256(client_token)
    run_id: str = Field(index=True)
    agent_name: str
    artifact_json: str = Field(default="null")   # JSON-serialized artifact
    options_json: str = Field(default='["approve","reject"]')  # JSON list
    status: str = Field(default="pending")  # pending | approved | rejected | edited | timeout
    decision: Optional[str] = Field(default=None)
    edited_json: Optional[str] = Field(default=None)
    feedback: Optional[str] = Field(default=None)
    assigned_to: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    resolved_at: Optional[datetime] = Field(default=None)


class HitlReviewAssignee(SQLModel, table=True):
    """One row per reviewer assigned to a HitlReview (many-to-many).

    Assignees are identified by their API key label.  Any assignee can resolve
    the review (first-to-respond model).  Use the 'mine' query param on
    GET /reviews/ to filter to reviews where the calling key is an assignee.
    """

    __tablename__ = "hitl_review_assignee"

    id: Optional[int] = Field(default=None, primary_key=True)
    review_id: str = Field(index=True)    # FK → hitl_review.review_id
    assignee_label: str = Field(index=True)  # FK → api_key.label
    created_at: datetime = Field(default_factory=_utcnow)


class HitlAuditEntry(SQLModel, table=True):
    """Immutable audit log for HITL review lifecycle events.

    Tracks who did what and when: creation, assignment, approval, rejection, timeout.
    Never updated — only appended to.
    """

    __tablename__ = "hitl_audit_entry"

    id: Optional[int] = Field(default=None, primary_key=True)
    review_id: str = Field(index=True)     # FK → hitl_review.review_id
    actor_label: Optional[str] = Field(default=None)  # API key label who triggered this event
    action: str  # created | assigned | approved | rejected | timed_out
    note: Optional[str] = Field(default=None)  # free-text from verdict note or error
    created_at: datetime = Field(default_factory=_utcnow)
