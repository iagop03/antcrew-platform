"""DiscoverySession model — stores conversational discovery state."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field

from app.models._utils import _utcnow


class DiscoverySession(SQLModel, table=True):
    """Persistent state for a single conversational discovery session."""

    __tablename__ = "discovery_session"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, unique=True)  # UUID string
    workspace_id: Optional[int] = Field(default=None, foreign_key="workspace.id", index=True)
    model: str = "claude"
    status: str = "active"  # "active" | "complete"
    context_json: str = "{}"  # serialized DiscoveryContext JSON
    current_question: str = ""  # latest question from agent
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
